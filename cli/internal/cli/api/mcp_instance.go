package api

// mcp_instance.go is the identity-stamp plumbing (§3.7.4): a TTL-cached
// GET /instance whose result is stamped as the top-level `instance` key on
// every tool result, so the model can always see WHICH Jentic One instance
// answered (backend locality, host, install digest) and how fresh that
// knowledge is. When the fetch fails the stamp degrades honestly: backend
// "unreachable" for transport failures, backend "error" when the instance
// ANSWERED with a non-2xx (it is reachable — claiming otherwise would
// affirmatively mislead, §3.7.4), plus the last-known identity + the real
// fetched_at of that last success — never a fabricated timestamp, never a
// missing key.
//
// Concurrency posture: the SDK runs tool calls concurrently, so the fetch
// happens strictly OUTSIDE the mutex — a slow/hung dial must never
// head-of-line-block calls that can answer from cache. Concurrent refreshes
// are collapsed to one wire call (singleflight): the first caller dials,
// everyone else waits for that outcome or their own context, whichever ends
// first. Failures are negatively cached for a short window so a down control
// plane is not re-dialed by every tool call (and get_started's probe→stamp
// sequence dials once, not twice).

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
)

// instanceStampTTL bounds how stale a fresh-looking stamp may be. Instance
// identity changes rarely (an operator re-pointing the environment), so a
// short TTL is about honest staleness, not correctness under change.
const instanceStampTTL = 60 * time.Second

// instanceFailureTTL is the negative-result cache: after a failed fetch,
// stamps serve the degraded form without re-dialing for this window. It keeps
// a down control plane from being dialed on every tool call, and collapses
// get_started's probe-then-stamp within one call to a single dial. Kept short
// so recovery is noticed quickly; probe() bypasses it (it IS the check).
const instanceFailureTTL = 5 * time.Second

// backendUnreachable is the degraded stamp's backend marker for transport
// failures (§3.7.4): the degraded form lives ON the backend field, not in a
// separate flag.
const backendUnreachable = "unreachable"

// backendError marks the degraded form when the instance ANSWERED with a
// non-2xx: it is reachable (an "unreachable" claim would mislead, §3.7.4) but
// its identity could not be (re-)read — e.g. GET /instance returned 401/500.
const backendError = "error"

// instanceIdentity is the last-known good GET /instance projection.
type instanceIdentity struct {
	backend    string
	host       string
	instanceID *string
	fetchedAt  time.Time // when THIS identity was actually fetched
}

// fetchFailure is the last failed fetch: the negative-cache entry and the
// input for choosing the degraded form's backend marker.
type fetchFailure struct {
	err error
	at  time.Time
	// answered records that the instance responded with an HTTP status (a
	// *HTTPError) rather than failing at the transport: reachable, but its
	// identity could not be read.
	answered bool
}

// inflightFetch is one in-progress GET /instance shared by every concurrent
// caller that needs a refresh. err is written before done is closed (the
// close is the happens-before edge waiters read err across).
type inflightFetch struct {
	done chan struct{}
	err  error
}

// instanceCache is the TTL cache. One per server process: the stamp is
// per-instance state, not per-call state.
type instanceCache struct {
	ttl        time.Duration
	failureTTL time.Duration
	now        func() time.Time // test seam

	// fetch resolves GET /instance for the given context. Swappable in tests;
	// production wiring is fetchInstance (clictx.GetControlClient).
	fetch func(ctx context.Context) (*control.InstanceIdentityResponse, error)

	// mu guards ONLY the fields below; it is never held across fetch.
	mu       sync.Mutex
	lastGood *instanceIdentity
	lastFail *fetchFailure
	// stale forces the next stamp to re-fetch even inside the TTL (§3.7.4
	// refresh-on-auth-error: after a tool-call auth failure the cached
	// identity can no longer be presumed current).
	stale    bool
	inflight *inflightFetch
}

func newInstanceCache() *instanceCache {
	return &instanceCache{
		ttl:        instanceStampTTL,
		failureTTL: instanceFailureTTL,
		now:        time.Now,
		fetch:      fetchInstance,
	}
}

// fetchInstance is the production GET /instance call: through
// clictx.GetControlClient, so the CA pinning, auth editors, and the session's
// transport hook (User-Agent/session-id) all apply.
func fetchInstance(ctx context.Context) (*control.InstanceIdentityResponse, error) {
	client, err := clictx.GetControlClient(ctx)
	if err != nil {
		return nil, err
	}
	resp, err := client.GetInstanceWithResponse(ctx)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	if resp.JSON200 != nil {
		return resp.JSON200, nil
	}
	// The typed field is only populated for an application/json content type;
	// fall back to the raw body so an unusual-but-valid response still stamps.
	var ident control.InstanceIdentityResponse
	if err := json.Unmarshal(resp.Body, &ident); err != nil {
		return nil, fmt.Errorf("decode /instance response: %w", err)
	}
	return &ident, nil
}

// stamp returns the `instance` value for a tool result: the cached identity
// while it is fresh, else a re-fetch (deduplicated across concurrent calls);
// on failure the DEGRADED form — backend "unreachable"/"error", the
// last-known host/instance_id (empty/null when none was ever seen), and the
// real fetched_at of the last success (null when never). A recent failure is
// served from the negative cache without another dial.
func (c *instanceCache) stamp(ctx context.Context) map[string]any {
	c.mu.Lock()
	if !c.stale && c.lastGood != nil && c.now().Sub(c.lastGood.fetchedAt) < c.ttl {
		defer c.mu.Unlock()
		return c.lastGood.asMap()
	}
	if c.lastFail != nil && c.now().Sub(c.lastFail.at) < c.failureTTL {
		defer c.mu.Unlock()
		return c.degradedLocked()
	}
	c.mu.Unlock()

	err := c.refresh(ctx)

	c.mu.Lock()
	defer c.mu.Unlock()
	if err != nil || c.lastGood == nil {
		return c.degradedLocked()
	}
	return c.lastGood.asMap()
}

// probe force-refreshes the identity, ignoring the TTL and the negative
// cache, and reports whether the instance answered. get_started uses it as
// its reachability check, so "reachable" in the diagnosis and "fresh stamp"
// on the result can't disagree — and the stamp that follows on the same call
// reuses this probe's outcome instead of dialing again.
func (c *instanceCache) probe(ctx context.Context) error {
	return c.refresh(ctx)
}

// invalidate marks the cached identity stale so the next stamp re-fetches
// even inside the TTL (§3.7.4 refresh-on-auth-error). The last-known identity
// is kept — it still feeds the degraded form — and the negative cache is
// cleared so the re-fetch actually dials.
func (c *instanceCache) invalidate() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.stale = true
	c.lastFail = nil
}

// refresh resolves GET /instance exactly once no matter how many callers need
// it concurrently: the first caller dials (with its own ctx), everyone else
// waits for that shared outcome or their own ctx, whichever ends first. The
// fetch runs OUTSIDE c.mu so cache-hit stamps never queue behind a slow dial.
func (c *instanceCache) refresh(ctx context.Context) error {
	c.mu.Lock()
	if fl := c.inflight; fl != nil {
		c.mu.Unlock()
		select {
		case <-fl.done:
			return fl.err
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	fl := &inflightFetch{done: make(chan struct{})}
	c.inflight = fl
	c.mu.Unlock()

	ident, err := c.fetch(ctx)

	c.mu.Lock()
	c.inflight = nil
	if err != nil {
		var httpErr *HTTPError
		c.lastFail = &fetchFailure{
			err:      err,
			at:       c.now(),
			answered: errors.As(err, &httpErr),
		}
	} else {
		c.lastGood = &instanceIdentity{
			backend:    string(ident.Backend),
			host:       ident.Host,
			instanceID: ident.InstanceId,
			fetchedAt:  c.now(),
		}
		c.lastFail = nil
		c.stale = false
	}
	c.mu.Unlock()

	fl.err = err
	close(fl.done)
	return err
}

// degradedLocked builds the degraded stamp from whatever identity was last
// known. backend distinguishes a transport failure ("unreachable") from an
// instance that answered with an HTTP error ("error" — reachable, identity
// unreadable). fetched_at stays the LAST SUCCESS time (real, possibly old —
// that staleness is the signal) and is null when the instance was never seen.
func (c *instanceCache) degradedLocked() map[string]any {
	backend := backendUnreachable
	if c.lastFail != nil && c.lastFail.answered {
		backend = backendError
	}
	stamp := map[string]any{
		"backend":     backend,
		"host":        "",
		"instance_id": nil,
		"fetched_at":  nil,
	}
	if c.lastGood != nil {
		stamp["host"] = c.lastGood.host
		stamp["instance_id"] = idOrNil(c.lastGood.instanceID)
		stamp["fetched_at"] = c.lastGood.fetchedAt.UTC().Format(time.RFC3339)
	}
	return stamp
}

func (i *instanceIdentity) asMap() map[string]any {
	return map[string]any{
		"backend":     i.backend,
		"host":        i.host,
		"instance_id": idOrNil(i.instanceID),
		"fetched_at":  i.fetchedAt.UTC().Format(time.RFC3339),
	}
}

// idOrNil collapses the SDK's nullable instance_id pointer to a JSON-friendly
// value (the backend sends null when telemetry has not resolved an id).
func idOrNil(id *string) any {
	if id == nil {
		return nil
	}
	return *id
}
