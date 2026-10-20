package container

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"sync"
	"time"
)

// InvocationToken is one minted `cit_` invocation token.
type InvocationToken struct {
	ID            string
	Token         string
	ExpiresAt     time.Time
	HardExpiresAt time.Time
}

// ErrTokenGone is what TokenIssuer.Renew returns for a token that can no
// longer be renewed (revoked, expired, past its hard cap): the keeper mints a
// replacement.
var ErrTokenGone = errors.New("the invocation token can no longer be renewed")

// TokenIssuer mints, renews and revokes invocation tokens on the control
// plane. The command layer implements it with the agent's own credentials.
type TokenIssuer interface {
	Mint(ctx context.Context, vendor string) (InvocationToken, error)
	// Renew extends a token and returns its new expiry.
	Renew(ctx context.Context, id string) (time.Time, error)
	Revoke(ctx context.Context, id string) error
}

const (
	// minHeartbeat and maxHeartbeat bound the renewal interval.
	minHeartbeat = time.Second
	maxHeartbeat = 5 * time.Minute
	// tokenCallTimeout bounds one issuer call.
	tokenCallTimeout = 30 * time.Second
)

// TokenKeeper holds a session's invocation tokens, one per governed vendor:
// it mints them before the session starts, renews them on a heartbeat at a
// third of their lifetime, replaces one that nears its hard cap or can no
// longer be renewed (handing the proxy the new set before revoking the old
// token), and revokes them all at Close.
type TokenKeeper struct {
	issuer TokenIssuer
	now    func() time.Time

	mu      sync.Mutex
	current map[string]InvocationToken
	push    func(ctx context.Context, tokens map[string]string) error

	stop   chan struct{}
	done   chan struct{}
	closed bool
}

// MintSessionTokens mints one token per vendor. It fails closed: when any
// mint fails, the tokens already minted are revoked and the error returned.
func MintSessionTokens(ctx context.Context, issuer TokenIssuer, vendors []string) (*TokenKeeper, error) {
	k := &TokenKeeper{issuer: issuer, now: time.Now, current: map[string]InvocationToken{}}
	for _, v := range vendors {
		callCtx, cancel := context.WithTimeout(ctx, tokenCallTimeout)
		tok, err := issuer.Mint(callCtx, v)
		cancel()
		if err != nil {
			_ = k.revokeAll(ctx)
			return nil, fmt.Errorf("mint the session's %s invocation token: %w", v, err)
		}
		k.current[v] = tok
	}
	return k, nil
}

// Tokens is the current vendor → token map.
func (k *TokenKeeper) Tokens() map[string]string {
	k.mu.Lock()
	defer k.mu.Unlock()
	out := make(map[string]string, len(k.current))
	for v, t := range k.current {
		out[v] = t.Token
	}
	return out
}

// Vendors lists the vendors the keeper holds tokens for, sorted.
func (k *TokenKeeper) Vendors() []string {
	k.mu.Lock()
	defer k.mu.Unlock()
	out := make([]string, 0, len(k.current))
	for v := range k.current {
		out = append(out, v)
	}
	sort.Strings(out)
	return out
}

// Run starts the heartbeat; push hands a changed token set to the proxy.
func (k *TokenKeeper) Run(ctx context.Context, push func(ctx context.Context, tokens map[string]string) error) {
	k.mu.Lock()
	if k.closed || k.stop != nil || len(k.current) == 0 {
		k.mu.Unlock()
		return
	}
	k.push = push
	k.stop, k.done = make(chan struct{}), make(chan struct{})
	k.mu.Unlock()
	bg := context.WithoutCancel(ctx)
	go func() {
		defer close(k.done)
		for {
			timer := time.NewTimer(k.interval())
			select {
			case <-k.stop:
				timer.Stop()
				return
			case <-timer.C:
				k.beat(bg)
			}
		}
	}()
}

// interval is a third of the shortest remaining lifetime, bounded.
func (k *TokenKeeper) interval() time.Duration {
	k.mu.Lock()
	defer k.mu.Unlock()
	now := k.now()
	shortest := maxHeartbeat * 3
	for _, t := range k.current {
		if left := t.ExpiresAt.Sub(now); left < shortest {
			shortest = left
		}
	}
	return min(max(shortest/3, minHeartbeat), maxHeartbeat)
}

// beat renews every token, or replaces it when it nears its hard cap or the
// server will not renew it.
func (k *TokenKeeper) beat(ctx context.Context) {
	interval := k.interval()
	k.mu.Lock()
	snapshot := make(map[string]InvocationToken, len(k.current))
	for v, t := range k.current {
		snapshot[v] = t
	}
	k.mu.Unlock()

	var replaced []InvocationToken
	for vendor, tok := range snapshot {
		rotate := tok.HardExpiresAt.Sub(k.now()) <= 2*interval
		if !rotate {
			callCtx, cancel := context.WithTimeout(ctx, tokenCallTimeout)
			exp, err := k.issuer.Renew(callCtx, tok.ID)
			cancel()
			switch {
			case err == nil:
				k.mu.Lock()
				if cur, ok := k.current[vendor]; ok && cur.ID == tok.ID {
					cur.ExpiresAt = exp
					k.current[vendor] = cur
				}
				k.mu.Unlock()
				continue
			case errors.Is(err, ErrTokenGone):
				// Replaced below.
			default:
				slog.Warn("could not renew a session invocation token; retrying", "vendor", vendor, "invocation_id", tok.ID, "error", err)
				continue
			}
		}
		callCtx, cancel := context.WithTimeout(ctx, tokenCallTimeout)
		fresh, err := k.issuer.Mint(callCtx, vendor)
		cancel()
		if err != nil {
			slog.Warn("could not replace a session invocation token; retrying", "vendor", vendor, "error", err)
			continue
		}
		k.mu.Lock()
		k.current[vendor] = fresh
		k.mu.Unlock()
		replaced = append(replaced, tok)
	}
	if len(replaced) == 0 {
		return
	}
	k.mu.Lock()
	push := k.push
	k.mu.Unlock()
	if push != nil {
		if err := push(ctx, k.Tokens()); err != nil {
			slog.Warn("could not hand the replaced session tokens to the egress proxy", "error", err)
		}
	}
	for _, old := range replaced {
		_ = k.revoke(ctx, old.ID) // logged; it expires on its own
	}
}

// Close stops the heartbeat and revokes every token. It is idempotent.
func (k *TokenKeeper) Close(ctx context.Context) error {
	k.mu.Lock()
	if k.closed {
		k.mu.Unlock()
		return nil
	}
	k.closed = true
	stop, done := k.stop, k.done
	k.mu.Unlock()
	if stop != nil {
		close(stop)
		<-done
	}
	return k.revokeAll(ctx)
}

func (k *TokenKeeper) revokeAll(ctx context.Context) error {
	k.mu.Lock()
	ids := make([]string, 0, len(k.current))
	for _, t := range k.current {
		ids = append(ids, t.ID)
	}
	k.current = map[string]InvocationToken{}
	k.mu.Unlock()
	var errs []error
	for _, id := range ids {
		if err := k.revoke(ctx, id); err != nil {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}

func (k *TokenKeeper) revoke(ctx context.Context, id string) error {
	callCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), tokenCallTimeout)
	defer cancel()
	if err := k.issuer.Revoke(callCtx, id); err != nil {
		slog.Warn("could not revoke a session invocation token; it expires on its own", "invocation_id", id, "error", err)
		return err
	}
	return nil
}
