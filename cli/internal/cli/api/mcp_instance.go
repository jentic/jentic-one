package api

// mcp_instance.go is the identity-stamp plumbing (§3.7.4): a TTL-cached
// GET /instance whose result is stamped as the top-level `instance` key on
// every tool result, so the model can always see WHICH Jentic One instance
// answered (backend locality, host, install digest) and how fresh that
// knowledge is. When the control plane can't be reached the stamp degrades to
// backend "unreachable" + the last-known identity + the real fetched_at of
// that last success — never a fabricated timestamp, never a missing key.

import (
	"context"
	"encoding/json"
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

// backendUnreachable is the degraded stamp's backend marker (§3.7.4): the
// degraded form lives ON the backend field, not in a separate flag.
const backendUnreachable = "unreachable"

// instanceIdentity is the last-known good GET /instance projection.
type instanceIdentity struct {
	backend    string
	host       string
	instanceID *string
	fetchedAt  time.Time // when THIS identity was actually fetched
}

// instanceCache is the TTL cache. One per server process: the stamp is
// per-instance state, not per-call state.
type instanceCache struct {
	ttl time.Duration
	now func() time.Time // test seam

	// fetch resolves GET /instance for the given context. Swappable in tests;
	// production wiring is fetchInstance (clictx.GetControlClient).
	fetch func(ctx context.Context) (*control.InstanceIdentityResponse, error)

	mu       sync.Mutex
	lastGood *instanceIdentity
}

func newInstanceCache() *instanceCache {
	return &instanceCache{
		ttl:   instanceStampTTL,
		now:   time.Now,
		fetch: fetchInstance,
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
// while it is fresh, else a re-fetch; on failure the DEGRADED form — backend
// "unreachable", the last-known host/instance_id (empty/null when none was
// ever seen), and the real fetched_at of the last success (null when never).
func (c *instanceCache) stamp(ctx context.Context) map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.lastGood != nil && c.now().Sub(c.lastGood.fetchedAt) < c.ttl {
		return c.lastGood.asMap()
	}
	if err := c.refreshLocked(ctx); err != nil {
		return c.degradedLocked()
	}
	return c.lastGood.asMap()
}

// probe force-refreshes the identity, ignoring the TTL, and reports whether
// the instance answered. get_started uses it as its reachability check, so
// "reachable" in the diagnosis and "fresh stamp" on the result can't disagree.
func (c *instanceCache) probe(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.refreshLocked(ctx)
}

func (c *instanceCache) refreshLocked(ctx context.Context) error {
	ident, err := c.fetch(ctx)
	if err != nil {
		return err
	}
	c.lastGood = &instanceIdentity{
		backend:    string(ident.Backend),
		host:       ident.Host,
		instanceID: ident.InstanceId,
		fetchedAt:  c.now(),
	}
	return nil
}

// degradedLocked builds the unreachable-form stamp from whatever identity was
// last known. fetched_at stays the LAST SUCCESS time (real, possibly old —
// that staleness is the signal) and is null when the instance was never seen.
func (c *instanceCache) degradedLocked() map[string]any {
	stamp := map[string]any{
		"backend":     backendUnreachable,
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
