package client

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
)

// Retry/idempotency policy knobs (13 §5). Package-level so tests can shrink the
// waits and caps instead of sleeping real wall-clock seconds.
var (
	// maxIdempotentRetries bounds 5xx/transport backoff for idempotent calls.
	maxIdempotentRetries = 3
	// retryBaseDelay is the first backoff step; it doubles each attempt.
	retryBaseDelay = 200 * time.Millisecond
	// maxRetryAfter caps how long a 429 Retry-After we will honor inline. A
	// server asking us to wait longer than this surfaces the 429 to the caller
	// instead (the wait-capable commands manage their own budget via --timeout).
	maxRetryAfter = 30 * time.Second
)

// retryTransport implements the SDK-level response policy from 13 §5:
//
//   - 401 → exactly one token re-exchange, then one retry, then surface. Only for
//     re-exchangeable credentials (the JWT-bearer disk path); injected tokens and
//     API keys are fixed, so a 401 on those is a hard denial we do not loop on.
//   - 429 → honor Retry-After up to maxRetryAfter, then one retry; otherwise
//     surface the 429 unchanged.
//   - 5xx / transport error → bounded backoff, but for IDEMPOTENT requests only
//     (GET/HEAD, or a POST carrying an Idempotency-Key). A plain POST is never
//     blind-retried.
//
// It wraps a base RoundTripper and re-stamps auth via auth.AttachAuth on the 401
// retry so the fresh token actually lands on the resent request (the generated
// client applies editors ONCE, before the first RoundTrip).
type retryTransport struct {
	base  http.RoundTripper
	creds auth.Credentials
	// reExchange enables the 401→token-refresh arm. True for the SDK's typed
	// clients (disk-backed identity to refresh); set false by BrokerTransport,
	// where the caller (jentic execute) owns its bearer and a 401 is a denial to
	// surface intact, not refresh.
	reExchange bool
}

// newRetryTransport wraps base with the response policy for creds. A nil base
// uses http.DefaultTransport. The 401 re-exchange arm is enabled by default;
// BrokerTransport flips reExchange off for the caller-owned-auth case.
func newRetryTransport(base http.RoundTripper, creds auth.Credentials) *retryTransport {
	if base == nil {
		base = http.DefaultTransport
	}
	return &retryTransport{base: base, creds: creds, reExchange: true}
}

func (t *retryTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	// Buffer the body once so we can safely resend it on any retry. A nil GetBody
	// (streaming body) means we cannot rewind, so such requests get exactly one
	// attempt regardless of status.
	body, canRewind, err := bufferBody(req)
	if err != nil {
		return nil, err
	}

	idempotent := isIdempotent(req)
	triedReauth := false
	tried429 := false
	delay := retryBaseDelay

	for attempt := 0; ; attempt++ {
		rewind(req, body, canRewind)

		resp, rtErr := t.base.RoundTrip(req)

		// Transport error (no response): bounded backoff for idempotent calls only.
		if rtErr != nil {
			if idempotent && canRewind && attempt < maxIdempotentRetries {
				if !sleepCtx(req.Context(), delay) {
					return nil, rtErr
				}
				delay *= 2
				continue
			}
			return nil, rtErr
		}

		switch {
		// 401: one re-exchange + retry, only for re-exchangeable creds and a
		// rewindable body. Discard the body before retrying to free the conn.
		case resp.StatusCode == http.StatusUnauthorized &&
			t.reExchange && !triedReauth && canRewind && auth.CanReExchange(t.creds):
			triedReauth = true
			drain(resp)
			// Force a fresh token: the on-disk one looked valid to us but the
			// server rejected it (revoked/rotated). Then re-stamp auth so the
			// resent request carries the new bearer.
			_ = auth.InvalidateTokens(t.creds.IdentityRef())
			if aerr := auth.AttachAuth(t.creds, req); aerr != nil {
				//nolint:nilerr // deliberate: if re-auth fails we surface the ORIGINAL 401 response to the caller, not the re-auth error.
				return resp, nil
			}
			continue

		// 429: honor Retry-After within budget, then retry ONCE; further 429s
		// surface. The tried429 flag enforces the single-retry contract
		// (SEC-3): without it, a server returning perpetual 429 + small
		// Retry-After loops the client indefinitely wherever no context
		// deadline or client timeout applies.
		case resp.StatusCode == http.StatusTooManyRequests:
			wait, ok := retryAfter(resp)
			if tried429 || !ok || wait > maxRetryAfter || !canRewind {
				return resp, nil // surface the 429 to the caller
			}
			tried429 = true
			drain(resp)
			if !sleepCtx(req.Context(), wait) {
				return resp, nil
			}
			continue

		// 5xx: bounded backoff for idempotent calls only.
		case resp.StatusCode >= 500 && idempotent && canRewind && attempt < maxIdempotentRetries:
			drain(resp)
			if !sleepCtx(req.Context(), delay) {
				// Context cancelled mid-backoff: re-issue once to get a final
				// response/error rather than fabricating one.
				rewind(req, body, canRewind)
				return t.base.RoundTrip(req)
			}
			delay *= 2
			continue

		default:
			return resp, nil
		}
	}
}

// isIdempotent reports whether req may be safely retried. GET/HEAD are idempotent
// by method; a POST/PUT is retry-safe only when it carries an Idempotency-Key
// (13 §5) — the broker dedupes on that header, so a resend cannot double-execute.
func isIdempotent(req *http.Request) bool {
	switch req.Method {
	case http.MethodGet, http.MethodHead, http.MethodOptions:
		return true
	}
	return req.Header.Get("Idempotency-Key") != ""
}

// bufferBody reads req.Body into memory so retries can resend it. Returns
// canRewind=false for a bodyless request or one whose body cannot be rewound.
func bufferBody(req *http.Request) (buf []byte, canRewind bool, err error) {
	if req.Body == nil || req.Body == http.NoBody {
		return nil, true, nil
	}
	data, err := io.ReadAll(req.Body)
	_ = req.Body.Close()
	if err != nil {
		return nil, false, err
	}
	return data, true, nil
}

// rewind resets req.Body to a fresh reader over buf before each attempt.
func rewind(req *http.Request, buf []byte, canRewind bool) {
	if !canRewind {
		return
	}
	if buf == nil {
		req.Body = http.NoBody
		return
	}
	req.Body = io.NopCloser(bytes.NewReader(buf))
	req.ContentLength = int64(len(buf))
}

// retryAfter parses a Retry-After header (delta-seconds or HTTP-date).
func retryAfter(resp *http.Response) (time.Duration, bool) {
	v := resp.Header.Get("Retry-After")
	if v == "" {
		return 0, false
	}
	if secs, err := strconv.Atoi(v); err == nil {
		if secs < 0 {
			return 0, false
		}
		return time.Duration(secs) * time.Second, true
	}
	if when, err := http.ParseTime(v); err == nil {
		d := time.Until(when)
		if d < 0 {
			d = 0
		}
		return d, true
	}
	return 0, false
}

// drain reads and closes a response body so the connection can be reused.
func drain(resp *http.Response) {
	if resp == nil || resp.Body == nil {
		return
	}
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4<<10))
	_ = resp.Body.Close()
}

// sleepCtx waits for d or until ctx is done; it returns false if ctx was
// cancelled first (so the caller stops retrying).
func sleepCtx(ctx context.Context, d time.Duration) bool {
	if ctx == nil {
		time.Sleep(d)
		return true
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}
