// This file holds the top-level SDK constructors. The package-level
// documentation (surface, boundary, stability policy) lives in doc.go.
//
// Everything here and below (client/auth, client/config, client/generated,
// client/paginate) is import-safe for third-party consumers: it must NOT import
// internal/*, pkg/*, Cobra, or any UX/theme package. Arch test Test1A_SDKBoundary
// enforces this.

package client

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

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

// maxSessionIDLen bounds the sanitized X-Jentic-Session-Id value. Generous for
// any UUID/ULID/name scheme, small enough to never bloat server log lines.
const maxSessionIDLen = 128

// SanitizeSessionID normalizes a caller-chosen session id for use as the
// X-Jentic-Session-Id header value (SEC-5, log hygiene). The id is untrusted
// input (usually $JENTIC_SESSION_ID): Go's transport already rejects CR/LF
// smuggling at request time, but a hostile or fat-fingered value would then
// fail the WHOLE request, and exotic-but-legal header bytes would land verbatim
// in server logs. Instead of failing, keep only [A-Za-z0-9._:-] (covers UUIDs,
// ULIDs, and dotted/namespaced names), drop everything else, and truncate to
// 128 bytes. Returns "" when nothing survives — correlation is best-effort and
// an unusable id must never block a call.
func SanitizeSessionID(id string) string {
	var b strings.Builder
	for _, r := range id {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9',
			r == '.', r == '_', r == ':', r == '-':
			b.WriteRune(r)
		}
		if b.Len() >= maxSessionIDLen {
			break
		}
	}
	return b.String()
}

// editorChain is the ordered request-editor list every plane shares: auth first
// (so a later editor can observe the Authorization header), then the optional
// session/telemetry editor, then any caller-supplied Editors.
func (c Config) editorChain() []RequestEditor {
	eds := make([]RequestEditor, 0, 2+len(c.Editors))
	eds = append(eds, auth.RequestEditor(c.credentials()))
	if sid := SanitizeSessionID(c.SessionID); sid != "" {
		eds = append(eds, sessionEditor(sid))
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

// NewControlRaw builds a raw control-plane client (server + Doer + editor chain)
// for the `jentic api` passthrough, which issues arbitrary METHOD/PATH requests
// the generated per-operation methods do not cover. It shares the SAME transport
// (retry/backoff), auth editor (incl. requireSecureHost), and session editor as
// the typed client — the passthrough is a thin wrapper over this, never a second
// hand-rolled transport (impl/5.0 §6a).
func NewControlRaw(c Config) (*control.Client, error) {
	if c.ControlBaseURL == "" {
		return nil, errors.New("control base URL is required")
	}
	cli, err := control.NewClient(c.ControlBaseURL, controlOptions(c)...)
	if err != nil {
		return nil, fmt.Errorf("building control client: %w", err)
	}
	return cli, nil
}

// RawControlRequest issues an arbitrary request through raw's transport and editor
// chain and returns the response. path is a spec-relative path (e.g.
// "/credentials"), joined to the client's Server. extraHeaders (key=value) are
// applied after the editor chain so a caller can override defaults. It applies
// every configured RequestEditor (auth, session) exactly as the typed methods do,
// so the passthrough inherits auth/redaction/session for free. The caller owns the
// response body (close it).
func RawControlRequest(ctx context.Context, raw *control.Client, method, path string, body io.Reader, extraHeaders ...string) (*http.Response, error) {
	// oapi-codegen's NewClient normalizes Server with a trailing slash; join
	// without doubling it (path is always spec-absolute, starting with '/').
	target := strings.TrimRight(raw.Server, "/") + path
	req, err := http.NewRequestWithContext(ctx, method, target, body)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	for _, ed := range raw.RequestEditors {
		if err := ed(ctx, req); err != nil {
			return nil, err
		}
	}
	for _, kv := range extraHeaders {
		k, v, ok := splitKV(kv)
		if !ok {
			return nil, fmt.Errorf("invalid header %q; expected key=value", kv)
		}
		req.Header.Set(k, v)
	}
	return raw.Client.Do(req)
}

// splitKV splits "key=value" (value may contain further '=').
func splitKV(kv string) (key, value string, ok bool) {
	i := strings.IndexByte(kv, '=')
	if i < 1 {
		return "", "", false
	}
	return strings.TrimSpace(kv[:i]), strings.TrimSpace(kv[i+1:]), true
}

// ProbeServerVersion asks the control plane for its running app version
// (GET /system/version). It backs the backend version-negotiation path
// (impl/5.0 §6a, plan.md Phase 5 item 9): when the CLI's embedded spec advertises
// a route an OLDER self-hosted server doesn't serve yet, a bare 404 is
// indistinguishable from a typo, so the passthrough probes the version once to
// enrich the error. Returns the running version string; a probe failure returns
// "" and an error (the caller degrades to a plain 404 rather than fabricating a
// verdict).
func ProbeServerVersion(ctx context.Context, raw *control.Client) (string, error) {
	resp, err := RawControlRequest(ctx, raw, http.MethodGet, "/system/version", nil)
	if err != nil {
		return "", err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("version probe returned status %d", resp.StatusCode)
	}
	var vr control.VersionResponse
	if derr := json.NewDecoder(resp.Body).Decode(&vr); derr != nil {
		return "", derr
	}
	return vr.Current, nil
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

// BrokerTransport returns the *http.Client the broker plane uses — the caller's
// transport decorated with the SDK response policy (429 Retry-After and bounded
// 5xx/transport backoff for idempotent calls — 13 §5). It is the seam `jentic
// execute` re-plumbs onto (plan.md Phase 5 item 1): execute composes the broker
// catch-all URL itself ({scheme}://{host}/{upstreamURL}) to preserve its exact
// METHOD:url|operation_id|METHOD:/path contract and agent_directive/exit-2
// denial handling, but sends through THIS transport rather than a bare
// http.Client, so it inherits the same retry/backoff every generated broker call
// gets.
//
// The 401 re-exchange arm is deliberately DISABLED here (reExchange=false):
// execute forwards its OWN agent bearer and treats a broker 401 as a recoverable
// denial whose agent_directive body must reach the caller intact — a re-exchange
// attempt would both be meaningless (no disk-backed identity to refresh) and
// drain that body. 429/5xx idempotent backoff still applies.
func BrokerTransport(c Config) *http.Client {
	hc := c.HTTPClient
	if hc == nil {
		hc = &http.Client{}
	}
	wrapped := *hc
	wrapped.Transport = newRetryTransport(hc.Transport, auth.Credentials{})
	// Force CanReExchange=false without a real credential: execute owns its auth.
	wrapped.Transport.(*retryTransport).reExchange = false
	return &wrapped
}
