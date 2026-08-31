package clictx

// client.go is the CLI adapter (impl/4.2 §3b): it translates the Cobra-resolved
// ActiveState into the SDK's UX-free client.Config and constructs the generated,
// authenticated plane clients. It lives in the leaf clictx package so command
// subpackages can import it without cycling back into the api command root.

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"net/http"
	"os"

	"github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/client/generated/control"
)

// configFromState maps the CLI's resolved ActiveState onto the SDK's UX-free
// Config. ActiveState embeds *config.ResolvedState, so the SDK fields (BaseURL,
// BrokerURL, IdentityName, …) are promoted and read through the same value.
func configFromState(state *ActiveState) (client.Config, error) {
	cfg := client.Config{
		ControlBaseURL:      state.BaseURL,
		BrokerBaseURL:       state.BrokerURL,
		IdentityName:        state.IdentityName,
		EnvironmentName:     state.EnvironmentName,
		InjectedBearerToken: state.InjectedBearerToken,
		SessionID:           state.SessionID,
	}
	// SEC-3/SEC-20: a per-environment custom CA bundle is honored by building an
	// HTTPClient whose transport verifies against that pool. When ca_cert_path is
	// set but cannot be loaded, we FAIL CLOSED (SEC-20) — previously this silently
	// fell back to system roots, downgrading the operator's explicit trust
	// decision without a word. A corrupted/deleted bundle is a hard error the
	// operator must fix, not a silent trust widening.
	hc, err := caCertHTTPClient(state.CACertPath)
	if err != nil {
		return client.Config{}, err
	}
	if hc != nil {
		cfg.HTTPClient = hc
	}
	return cfg, nil
}

// caCertHTTPClient returns an *http.Client pinned to the CA bundle at path, or
// (nil, nil) when path is empty (no custom CA configured → SDK default transport
// on system roots). When path is set but the bundle can't be read or contains no
// usable certificate, it returns an ERROR (SEC-20): the operator asked to trust
// a specific CA, so a broken bundle must fail closed rather than silently fall
// back to system roots.
func caCertHTTPClient(path string) (*http.Client, error) {
	if path == "" {
		return nil, nil
	}
	pem, err := os.ReadFile(path) //nolint:gosec // path is the operator-configured ca_cert_path from their own config.
	if err != nil {
		return nil, fmt.Errorf("ca_cert_path %q is set but could not be read: %w", path, err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(pem) {
		return nil, fmt.Errorf("ca_cert_path %q contains no usable PEM certificate", path)
	}
	return &http.Client{
		Transport: &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}},
	}, nil
}

// ErrNoConfig is returned by the client constructors when the machine has no
// usable configuration (nil/degraded ActiveState, or a state with no plane URL).
// It is a typed sentinel — not a *ux.CodedError — so this leaf package stays
// free of the ux/command layers (the leaf-layering arch gate forbids clictx →
// ux). The api command layer's asCoded recognizes it and maps it to
// RESOLVE_FAILED (AGT-22): an unconfigured machine is a recoverable "run
// register" state, NOT the INTERNAL_ERROR "stop, CLI bug" bucket a bare error
// would fall into.
var ErrNoConfig = errors.New("no configuration found; run 'jentic register' (or 'jentic migrate' on a machine with a V1 setup) first")

// stateForClient validates the ActiveState a client constructor needs. It exists
// so a degraded state (root interceptor's no-config fallback carries an empty
// ResolvedState) or a nil embed can never nil-deref: commands get an actionable
// error instead of a panic (whose runtime exit code 2 collides with ExitDenied).
func stateForClient(state *ActiveState) (*ActiveState, error) {
	if state == nil {
		return nil, errors.New("no active state in context (was the root PersistentPreRunE run?)")
	}
	// A nil embed or a state with no plane URL at all is the unconfigured-machine
	// case — surface the recovery command rather than the SDK's terser
	// "control base URL is required". Returned as the typed ErrNoConfig sentinel
	// so the command layer codes it RESOLVE_FAILED, not INTERNAL_ERROR (AGT-22).
	if state.ResolvedState == nil || (state.BaseURL == "" && state.BrokerURL == "") {
		return nil, ErrNoConfig
	}
	return state, nil
}

// BrokerHTTPClient builds the base *http.Client for the broker leg of an
// execute from the active context. Local-MCP §3.7.2: `execute`'s broker leg
// historically built its own un-pinned client; the MCP path routes through
// this constructor so broker calls honor the same trust decision as every
// other backend call. Callers pass the result to client.BrokerTransport, which
// decorates the retry/backoff policy on the outside.
func BrokerHTTPClient(ctx context.Context) (*http.Client, error) {
	return hookedPlaneHTTPClient(ctx)
}

// ControlHTTPClient builds the base *http.Client for RAW control-plane
// requests that have no generated route — the schema-hidden agent-discovery
// documents (`GET /skills/<name>.md` / `/skills/index.json`, #651) the
// `jentic mcp` skill:// resources fetch. Those routes are public (no auth
// editors needed — they must be fetchable while a registration is still
// pending), but the transport posture is identical to every other plane call:
// the SEC-20 CA-pinned client when the environment declares ca_cert_path,
// with the context's TransportHook composed over it.
func ControlHTTPClient(ctx context.Context) (*http.Client, error) {
	return hookedPlaneHTTPClient(ctx)
}

// hookedPlaneHTTPClient is the shared constructor behind the raw plane
// clients: the SEC-20 CA-pinned transport when the environment declares
// ca_cert_path (fail closed on a broken bundle, exactly like the generated
// clients), the default transport otherwise, with the context's TransportHook
// composed over it (wrap, never displace — the pinning decision stays the
// inner RoundTripper).
func hookedPlaneHTTPClient(ctx context.Context) (*http.Client, error) {
	state, err := stateForClient(FromContext(ctx))
	if err != nil {
		return nil, err
	}
	hc, err := caCertHTTPClient(state.CACertPath)
	if err != nil {
		return nil, err
	}
	if hc == nil {
		hc = &http.Client{}
	}
	if hook := transportHookFrom(ctx); hook != nil {
		base := hc.Transport
		if base == nil {
			base = http.DefaultTransport
		}
		hc.Transport = hook(base)
	}
	return hc, nil
}

// GetControlClient is the single constructor every Control Plane command uses. It
// pulls the ActiveState the root interceptor injected and delegates to the SDK, so
// token state, API-key credentials, and the file-less override are all handled
// transparently and identically to a third-party SDK consumer.
func GetControlClient(ctx context.Context) (*control.ClientWithResponses, error) {
	state, err := stateForClient(FromContext(ctx))
	if err != nil {
		return nil, err
	}
	cfg, err := configFromState(state)
	if err != nil {
		return nil, err
	}
	applyTransportHook(ctx, &cfg)
	return client.NewControl(cfg)
}

// GetControlRawClient returns the raw control client for the `jentic api`
// passthrough (arbitrary METHOD/PATH). It shares the identical transport + editor
// chain as GetControlClient, so the passthrough inherits auth, session, retry, and
// the transport guard from the SDK rather than re-implementing them.
func GetControlRawClient(ctx context.Context) (*control.Client, error) {
	state, err := stateForClient(FromContext(ctx))
	if err != nil {
		return nil, err
	}
	cfg, err := configFromState(state)
	if err != nil {
		return nil, err
	}
	applyTransportHook(ctx, &cfg)
	return client.NewControlRaw(cfg)
}
