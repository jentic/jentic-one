package api

// connect_test.go drives `jentic connect` and `jentic whoami` E2E against a
// mock control plane: wire shape (identity injected — no agent_id ever rides
// the connect body), the printed relay envelope (the poll_token is threaded
// in memory to the --wait loop but never printed — the redaction funnel
// scrubs *_token keys on every output surface), the --wait loop's
// terminal statuses, the coded failure mappings, and whoami's render-every-
// variant contract (unlike getMe, which rejects non-agent discriminators).

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// captureStdout redirects os.Stdout for fn's duration: the Audience Render
// contract writes machine JSON to the process stream directly (ux/agent.go),
// never through app.Out.
func captureStdout(t *testing.T, fn func()) string {
	t.Helper()
	old := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stdout = w
	defer func() { os.Stdout = old }()
	fn()
	_ = w.Close()
	var buf bytes.Buffer
	_, _ = io.Copy(&buf, r)
	return buf.String()
}

// runConnectTree executes the jentic root with args against srvURL, capturing
// the Render stream. Agent mode keeps Render as parseable JSON.
func runConnectTree(t *testing.T, srvURL string, args ...string) (string, error) {
	t.Helper()
	app := testApp(t)
	seedRegistered(t, app, "default", srvURL)
	t.Setenv("JENTIC_MODE", "agent")
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs(args)
	var err error
	out := captureStdout(t, func() { err = root.Execute() })
	return out, err
}

func TestConnect_PrintsRelayEnvelopeWithoutPollToken(t *testing.T) {
	withXDG(t)
	var seen []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/integrations:connect" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		seen, _ = io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"session_id":"cs_1","approval_url":"https://one.example/connect/cs_1",
			"poll_token":"pt_1","resolved_flow":"authorization_code"}`))
	}))
	defer srv.Close()

	out, err := runConnectTree(t, srv.URL, "connect", "github",
		"--scopes", "repo,read:org", "--reason", "read PRs")
	if err != nil {
		t.Fatalf("connect: %v\n%s", err, out)
	}

	var wire map[string]any
	if err := json.Unmarshal(seen, &wire); err != nil {
		t.Fatalf("decode wire body: %v", err)
	}
	if wire["vendor"] != "github" || wire["reason"] != "read PRs" {
		t.Errorf("wire body = %v, want vendor github + reason", wire)
	}
	if scopes, _ := wire["requested_scopes"].([]any); len(scopes) != 2 {
		t.Errorf("requested_scopes = %v, want two entries from --scopes", wire["requested_scopes"])
	}
	if _, has := wire["agent_id"]; has {
		t.Errorf("agent_id rode the wire (%v) — the control plane injects the caller's identity", wire["agent_id"])
	}

	// The relay envelope: approval_url + next_step teaching the loop. The
	// poll_token never rides it — the redaction funnel scrubs *_token keys on
	// every output surface, and --wait polls with the in-memory token.
	for _, want := range []string{
		"https://one.example/connect/cs_1", "cs_1",
		"authorization_code", "approval_url", "jentic whoami",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("connect output missing %q\n---\n%s", want, out)
		}
	}
	if strings.Contains(out, "pt_1") || strings.Contains(out, "poll_token") {
		t.Errorf("poll_token leaked into the rendered envelope\n---\n%s", out)
	}
}

func TestConnect_WaitPollsUntilConnected(t *testing.T) {
	withXDG(t)
	polls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/integrations:connect":
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"session_id":"cs_2","approval_url":"https://one.example/c/2","poll_token":"pt_2","resolved_flow":"device_authorization"}`))
		case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/connect-sessions/cs_2/status"):
			if r.URL.Query().Get("poll_token") != "pt_2" {
				w.WriteHeader(http.StatusForbidden)
				return
			}
			polls++
			if polls < 3 {
				_, _ = w.Write([]byte(`{"status":"pending"}`))
				return
			}
			_, _ = w.Write([]byte(`{"status":"connected","connected_as":"@octocat","credential_id":"cred_9","bound_scopes":["repo"]}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	out, err := runConnectTree(t, srv.URL, "connect", "github", "--wait")
	if err != nil {
		t.Fatalf("connect --wait: %v\n%s", err, out)
	}
	if polls < 3 {
		t.Errorf("polls = %d, want the loop to ride pending to the terminal status", polls)
	}
	// One stdout document per invocation (13 §1): the create envelope and the
	// terminal status are merged into a single final render.
	dec := json.NewDecoder(strings.NewReader(out))
	var doc map[string]any
	if err := dec.Decode(&doc); err != nil {
		t.Fatalf("decode stdout: %v\n---\n%s", err, out)
	}
	if dec.More() {
		t.Fatalf("--wait wrote more than one JSON document to stdout\n---\n%s", out)
	}
	for key, want := range map[string]string{
		"status": "connected", "connected_as": "@octocat", "credential_id": "cred_9",
		"session_id": "cs_2", "approval_url": "https://one.example/c/2",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("final render missing %s=%q\n---\n%s", key, want, out)
		}
	}
}

// An unhappy terminal (rejected/expired/cancelled) deletes the session row, and
// the status route answers a missing session with 403 invalid_poll_token (the
// no-enumeration posture) — never a {"status":"expired"} body.
func TestConnect_WaitEndedSessionIsResolveFailed(t *testing.T) {
	withXDG(t)
	polls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodPost {
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"session_id":"cs_3","approval_url":"https://one.example/c/3","poll_token":"pt_3","resolved_flow":"authorization_code"}`))
			return
		}
		polls++
		if polls < 2 {
			_, _ = w.Write([]byte(`{"status":"polling"}`))
			return
		}
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"type":"invalid_poll_token","detail":"invalid poll_token"}`))
	}))
	defer srv.Close()

	_, err := runConnectTree(t, srv.URL, "connect", "github", "--wait")
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("ended session returned %T (%v), want *ux.CodedError", err, err)
	}
	if coded.Code != ux.CodeResolveFailed {
		t.Errorf("code = %q, want %q (re-running connect mints a fresh session)", coded.Code, ux.CodeResolveFailed)
	}
	if !strings.Contains(coded.Actionable, "jentic connect github") {
		t.Errorf("actionable %q must name the exact re-run", coded.Actionable)
	}
}

// The route answers an unknown vendor with 404 unknown_vendor (errors.py
// _VENDOR_ERROR_MAP).
func TestConnect_UnknownVendor404IsResolveFailed(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"type":"unknown_vendor","detail":"unknown vendor: 'nope'"}`))
	}))
	defer srv.Close()

	_, err := runConnectTree(t, srv.URL, "connect", "nope")
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("unknown vendor returned %T (%v), want *ux.CodedError", err, err)
	}
	if coded.Code != ux.CodeResolveFailed {
		t.Errorf("code = %q, want %q", coded.Code, ux.CodeResolveFailed)
	}
	if !strings.Contains(coded.Actionable, "registry") || !strings.Contains(coded.Actionable, "operator") {
		t.Errorf("actionable %q must name the registry constraint and the operator fallback", coded.Actionable)
	}
}

func TestConnect_403IsOperatorScopeGrant(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"detail":"requires one of: credentials:connect"}`))
	}))
	defer srv.Close()

	_, err := runConnectTree(t, srv.URL, "connect", "github")
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("403 returned %T (%v), want *ux.CodedError", err, err)
	}
	if coded.Code != ux.CodeBrokerDenied {
		t.Errorf("code = %q, want %q", coded.Code, ux.CodeBrokerDenied)
	}
	if !strings.Contains(coded.Actionable, "credentials:connect") || !strings.Contains(coded.Actionable, "jentic logout") {
		t.Errorf("actionable %q must name the credentials:connect scope and the token re-mint", coded.Actionable)
	}
}

func TestConnect_TooManyScopesIsArgumentError(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("an over-cap --scopes must never reach the wire")
	}))
	defer srv.Close()

	many := make([]string, connectScopesMax+1)
	for i := range many {
		many[i] = fmt.Sprintf("s%d", i)
	}
	_, err := runConnectTree(t, srv.URL, "connect", "github", "--scopes", strings.Join(many, ","))
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeMissingArgument {
		t.Fatalf("over-cap scopes returned %T (%v), want MISSING_ARGUMENT", err, err)
	}
}

func TestConnect_OverlongReasonIsArgumentError(t *testing.T) {
	// Review L2: the route's reason bound (max_length=1024) is enforced
	// client-side — a clear argument error before any request, never a route
	// 422 rendered as a retryable transport failure.
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("an overlong --reason must never reach the wire")
	}))
	defer srv.Close()

	_, err := runConnectTree(t, srv.URL, "connect", "github", "--reason", strings.Repeat("x", 1025))
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("overlong reason returned %T (%v), want *ux.CodedError", err, err)
	}
	if coded.Code != ux.CodeMissingArgument {
		t.Errorf("code = %q, want %q", coded.Code, ux.CodeMissingArgument)
	}
	if !strings.Contains(coded.Msg, "1024") {
		t.Errorf("msg %q must name the 1024 bound", coded.Msg)
	}
}

func TestConnect_WaitWithNonPositiveTimeoutIsArgumentError(t *testing.T) {
	// A zero/negative --timeout with --wait is a contradiction, not a request
	// for the default — it must error before any session is created.
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("--wait with a non-positive --timeout must never reach the wire")
	}))
	defer srv.Close()

	for _, timeout := range []string{"0", "-1s"} {
		_, err := runConnectTree(t, srv.URL, "connect", "github", "--wait", "--timeout", timeout)
		var coded *ux.CodedError
		if !errors.As(err, &coded) {
			t.Fatalf("--timeout %s returned %T (%v), want *ux.CodedError", timeout, err, err)
		}
		if coded.Code != ux.CodeMissingArgument {
			t.Errorf("--timeout %s: code = %q, want %q", timeout, coded.Code, ux.CodeMissingArgument)
		}
	}
}

// --- whoami -------------------------------------------------------------------

func TestWhoami_RendersAgentVariant(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/me" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"type":"agent","id":"agnt_1","name":"test-agent","status":"active",
			"scopes":["apis:read"],"token_scopes":["apis:read"],"toolkit_bindings":[],
			"credential_bindings":[{"credential_id":"cred_1","name":"github main","bound_at":"2026-09-01T00:00:00Z",
			"serves":[{"vendor":"github-com","name":"github-com-api-github-com","version":"1.0.0"}]}]}`))
	}))
	defer srv.Close()

	out, err := runConnectTree(t, srv.URL, "whoami")
	if err != nil {
		t.Fatalf("whoami: %v\n%s", err, out)
	}
	// The verbatim union: identity, live scopes, and — the load-bearing part —
	// the credential bindings with the APIs they serve (the redaction funnel
	// must not swallow credential_bindings; it is binding metadata, no secret).
	for _, want := range []string{"agnt_1", "apis:read", "cred_1", "github-com-api-github-com"} {
		if !strings.Contains(out, want) {
			t.Errorf("whoami output missing %q\n---\n%s", want, out)
		}
	}
}

func TestWhoami_RendersNonAgentVariantsVerbatim(t *testing.T) {
	// The contract that separates whoami from getMe: a user (or
	// service-account) token must get its own /me variant rendered, never the
	// wrong-discriminator refusal the agent-only data commands raise.
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"type":"user","id":"usr_7","name":"Op","email":"op@example.com",
			"admin":true,"status":"active","scopes":["credentials:write"],"must_change_password":false}`))
	}))
	defer srv.Close()

	out, err := runConnectTree(t, srv.URL, "whoami")
	if err != nil {
		t.Fatalf("whoami (user token): %v\n%s", err, out)
	}
	for _, want := range []string{"usr_7", "op@example.com", `"user"`} {
		if !strings.Contains(out, want) {
			t.Errorf("whoami output missing %q\n---\n%s", want, out)
		}
	}
}

func TestWhoami_RendersServiceAccountVariant(t *testing.T) {
	// The third /me discriminator (MeServiceAccount): whoami must render it
	// verbatim too — its live scopes vs token_scopes split is exactly the
	// "re-mint to pick up a grant" signal the caller reads (#673).
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"type":"service_account","id":"sva_9","name":"ci-runner","status":"active",
			"scopes":["apis:read","credentials:connect"],"token_scopes":["apis:read"],
			"registered_by":"usr_7","approved_by":"usr_1"}`))
	}))
	defer srv.Close()

	out, err := runConnectTree(t, srv.URL, "whoami")
	if err != nil {
		t.Fatalf("whoami (service-account token): %v\n%s", err, out)
	}
	for _, want := range []string{"sva_9", "ci-runner", `"service_account"`, "credentials:connect", "token_scopes"} {
		if !strings.Contains(out, want) {
			t.Errorf("whoami output missing %q\n---\n%s", want, out)
		}
	}
}
