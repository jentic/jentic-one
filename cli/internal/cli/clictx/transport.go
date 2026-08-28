package clictx

// transport.go is the exported transport-hook seam (local-MCP phase 1, PR 1-A).
// A long-lived embedder of the CLI (the `jentic mcp` server) needs to decorate
// every control-plane request with process-level attribution headers
// (User-Agent, the per-process X-Jentic-Session-Id fallback) without owning
// client construction itself. configFromState builds the client.Config
// internally and cfg.HTTPClient is reserved for the SEC-20 CA-pinned client,
// so before this seam there was no way to compose a RoundTripper over that
// transport from outside this package.
//
// The hook WRAPS the transport the config resolution produced — the CA-pinned
// transport when the environment declares ca_cert_path, the default transport
// otherwise. It never displaces it: SEC-20's fail-closed pinning decision is
// made first, and the hook's RoundTripper delegates to whatever it decided.
// (The SDK's retry/backoff transport still decorates the outside at
// construction, so hooked headers are present on every retry attempt.)

import (
	"context"
	"net/http"

	"github.com/jentic/jentic-one/cli/client"
)

// TransportHook decorates the *http.RoundTripper* a plane client sends through.
// base is never nil: callers receive the CA-pinned transport when one is
// configured, else http.DefaultTransport. Implementations must delegate to
// base (wrap, never displace — SEC-20).
type TransportHook func(base http.RoundTripper) http.RoundTripper

const transportHookKey contextKey = "jentic_transport_hook"

// WithTransportHook stores a TransportHook in the context. Every client the
// clictx constructors (GetControlClient, GetControlRawClient) build from that
// context composes the hook over the resolved transport. A nil hook is a no-op.
func WithTransportHook(ctx context.Context, hook TransportHook) context.Context {
	return context.WithValue(ctx, transportHookKey, hook)
}

// transportHookFrom retrieves the TransportHook, or nil when none was set.
func transportHookFrom(ctx context.Context) TransportHook {
	h, _ := ctx.Value(transportHookKey).(TransportHook)
	return h
}

// applyTransportHook composes the context's TransportHook (if any) over the
// config's resolved HTTP client. The hook wraps the transport SEC-20 resolution
// produced — the CA-pinned transport, or the default one — so the pinning
// decision is always the inner RoundTripper.
func applyTransportHook(ctx context.Context, cfg *client.Config) {
	hook := transportHookFrom(ctx)
	if hook == nil {
		return
	}
	hc := cfg.HTTPClient
	if hc == nil {
		hc = &http.Client{}
	}
	wrapped := *hc
	base := wrapped.Transport
	if base == nil {
		base = http.DefaultTransport
	}
	wrapped.Transport = hook(base)
	cfg.HTTPClient = &wrapped
}
