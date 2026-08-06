// Package client is the top-level, UX-free entrypoint for the Jentic SDK. It maps
// a Config into the auth layer's Credentials, assembles the request-editor chain
// (auth bearer + any caller-supplied editors), and constructs the generated,
// strictly-typed control- and broker-plane clients.
//
// Everything here and below (client/auth, client/config, client/generated,
// client/paginate) is import-safe for third-party consumers: it must NOT import
// internal/*, Cobra, or any UX/theme package. Arch test 1A enforces this the
// moment this package exists.
package client

import (
	"context"
	"errors"
	"fmt"
	"net/http"

	"github.com/jentic/jentic-one/cli/client/auth"
	broker "github.com/jentic/jentic-one/cli/client/generated/broker"
	control "github.com/jentic/jentic-one/cli/client/generated/control"
)

// RequestEditor mutates an outbound request before it is sent. It is the generated
// clients' editor signature, re-exported so SDK consumers needn't import a
// generated package to add headers/tracing.
type RequestEditor = func(ctx context.Context, req *http.Request) error

// Config is the resolved connection + identity a client needs. It is produced by
// mapping a config.ResolvedState (disk or file-less) into plain fields, so the SDK
// never re-reads config.yaml or env vars itself — the caller owns resolution.
type Config struct {
	// ControlBaseURL is the management/control-plane URL (registry, auth,
	// executions). Required.
	ControlBaseURL string
	// BrokerBaseURL is the execution/broker-plane URL. Optional; NewBroker errors
	// if it is empty.
	BrokerBaseURL string

	IdentityName    string
	EnvironmentName string

	// SessionID, when set, is attached as X-Jentic-Session-Id on every request so
	// the backend can group a run's executions under a client-chosen session (the
	// server-side correlation pivot is trace_id; this is an additional grouping —
	// impl/5.0 §1). Populated for the CLI from JENTIC_SESSION_ID via ResolvedState.
	SessionID string

	// InjectedBearerToken, when set, bypasses the on-disk key/token exchange and is
	// attached verbatim (file-less / bring-your-own-token mode).
	InjectedBearerToken string

	// HTTPClient overrides the transport used by BOTH planes. Optional; a nil value
	// uses the generated clients' default. Supply one to inject timeouts, a custom
	// CA pool (env ca_cert), or test doubles.
	HTTPClient *http.Client

	// Editors are extra request editors appended AFTER the auth editor (tracing,
	// idempotency keys, etc.). The auth editor always runs first so a caller editor
	// can observe/override the Authorization header if it must.
	Editors []RequestEditor
}

// credentials projects the auth-relevant subset of Config.
func (c Config) credentials() auth.Credentials {
	return auth.Credentials{
		BaseURL:             c.ControlBaseURL,
		IdentityName:        c.IdentityName,
		EnvironmentName:     c.EnvironmentName,
		InjectedBearerToken: c.InjectedBearerToken,
	}
}

// sessionEditor attaches the X-Jentic-Session-Id header. It is appended after the
// auth editor and only when Config.SessionID is non-empty (impl/5.0 §1). Kept as
// the raw editor signature so it converts to each generated package's
// RequestEditorFn at construction alongside the auth editor.
func sessionEditor(sessionID string) RequestEditor {
	return func(_ context.Context, req *http.Request) error {
		req.Header.Set("X-Jentic-Session-Id", sessionID)
		return nil
	}
}

// editorChain is the ordered request-editor list every plane shares: auth first
// (so a later editor can observe the Authorization header), then the optional
// session/telemetry editor, then any caller-supplied Editors.
func (c Config) editorChain() []RequestEditor {
	eds := make([]RequestEditor, 0, 2+len(c.Editors))
	eds = append(eds, auth.RequestEditor(c.credentials()))
	if c.SessionID != "" {
		eds = append(eds, sessionEditor(c.SessionID))
	}
	eds = append(eds, c.Editors...)
	return eds
}

// controlOptions / brokerOptions assemble each plane's option slice. They are
// nearly identical but the generated ClientOption types are distinct per package,
// so Go generics can't unify them without an adapter; two tiny builders are
// clearer than a reflective bridge.
func controlOptions(c Config) []control.ClientOption {
	eds := c.editorChain()
	opts := make([]control.ClientOption, 0, 1+len(eds))
	opts = append(opts, control.WithHTTPClient(c.httpClient()))
	for _, e := range eds {
		opts = append(opts, control.WithRequestEditorFn(control.RequestEditorFn(e)))
	}
	return opts
}

func brokerOptions(c Config) []broker.ClientOption {
	eds := c.editorChain()
	opts := make([]broker.ClientOption, 0, 1+len(eds))
	opts = append(opts, broker.WithHTTPClient(c.httpClient()))
	for _, e := range eds {
		opts = append(opts, broker.WithRequestEditorFn(broker.RequestEditorFn(e)))
	}
	return opts
}

// httpClient returns the *http.Client both planes share, with the SDK-level
// response policy (401 re-exchange, 429 Retry-After, bounded 5xx/transport
// backoff — 13 §5) wrapped around the caller's transport. A caller-supplied
// HTTPClient is preserved (timeouts, custom CA pool, test doubles); only its
// transport is decorated. When no client is supplied we default one but leave its
// Timeout zero so the generated per-call contexts govern deadlines.
func (c Config) httpClient() *http.Client {
	hc := c.HTTPClient
	if hc == nil {
		hc = &http.Client{}
	}
	wrapped := *hc
	wrapped.Transport = newRetryTransport(hc.Transport, c.credentials())
	return &wrapped
}

// NewControl builds the strictly-typed control-plane client, authenticated for the
// configured identity/environment.
func NewControl(c Config) (*control.ClientWithResponses, error) {
	if c.ControlBaseURL == "" {
		return nil, errors.New("control base URL is required")
	}
	cli, err := control.NewClientWithResponses(c.ControlBaseURL, controlOptions(c)...)
	if err != nil {
		return nil, fmt.Errorf("building control client: %w", err)
	}
	return cli, nil
}

// NewBroker builds the strictly-typed broker-plane (execution) client. It reuses
// the control-plane identity's bearer, since the broker validates the same token.
func NewBroker(c Config) (*broker.ClientWithResponses, error) {
	if c.BrokerBaseURL == "" {
		return nil, errors.New("broker base URL is required (set the environment's broker_url)")
	}
	// The auth editor derives its token endpoint from ControlBaseURL, so it must be
	// present even when the caller only wants the broker.
	if c.ControlBaseURL == "" {
		return nil, errors.New("control base URL is required to authenticate the broker client")
	}
	cli, err := broker.NewClientWithResponses(c.BrokerBaseURL, brokerOptions(c)...)
	if err != nil {
		return nil, fmt.Errorf("building broker client: %w", err)
	}
	return cli, nil
}
