package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// TestBadFlagKV pins ARCH-4: a malformed key=value flag (--path/--query/
// --header) is agent-causable INPUT and must surface a coded MISSING_ARGUMENT
// (exit 2) with an actionable hint, not a bare exit-1 string an agent can't
// branch on.
func TestBadFlagKV(t *testing.T) {
	err := badFlagKV("--query", "nope")
	var ce *ux.CodedError
	if !errors.As(err, &ce) {
		t.Fatalf("badFlagKV returned %T, want *ux.CodedError", err)
	}
	if ce.Code != ux.CodeMissingArgument {
		t.Errorf("code = %q, want %q", ce.Code, ux.CodeMissingArgument)
	}
	if ce.ExitCode() != 1 {
		t.Errorf("exit = %d, want 1 (MISSING_ARGUMENT is a generic input error)", ce.ExitCode())
	}
	if ce.Actionable == "" {
		t.Error("badFlagKV must carry an actionable hint")
	}
	if !strings.Contains(ce.Msg, "--query") || !strings.Contains(ce.Msg, "nope") {
		t.Errorf("msg %q should name the flag and offending value", ce.Msg)
	}
}

func TestExecuteCmdJSONEnvelope(t *testing.T) {
	// One server plays both roles: control plane (/inspect) and broker (the
	// catch-all that receives /{upstreamURL}). Inspect returns an upstream URL;
	// execute must route it through the broker host as /{upstreamURL}.
	var gotBrokerPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			_, _ = w.Write([]byte(`{"method":"GET","url":"https://upstream.example/v1/pets"}`))
			return
		}
		gotBrokerPath = r.URL.Path
		w.Header().Set("Jentic-Execution-Id", "exec-123")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`[{"id":1,"name":"Fido"}]`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "listPets",
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !strings.Contains(gotBrokerPath, "upstream.example/v1/pets") {
		t.Errorf("broker path = %q, want to contain upstream URL", gotBrokerPath)
	}

	var envelope map[string]any
	if err := json.Unmarshal(out.Bytes(), &envelope); err != nil {
		t.Fatalf("unmarshal: %v\nraw: %s", err, out.String())
	}
	// AGT-23: the success envelope carries a schema_version like every sanctioned
	// wrapper, so an agent can branch on the shape.
	if envelope["schema_version"] != apiEnvelopeSchemaVersion {
		t.Errorf("schema_version = %v, want %q", envelope["schema_version"], apiEnvelopeSchemaVersion)
	}
	if envelope["status"] != float64(200) {
		t.Errorf("status = %v, want 200", envelope["status"])
	}
	if envelope["execution_id"] != "exec-123" {
		t.Errorf("execution_id = %v", envelope["execution_id"])
	}
	body, ok := envelope["body"].([]any)
	if !ok || len(body) == 0 {
		t.Errorf("body = %v", envelope["body"])
	}
}

func TestExecuteCmdDeniedSurfacesDirectiveAndExits2(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/problem+json")
		w.Header().Set("Jentic-Error-Origin", "broker")
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{
			"type": "no_toolkit_binding",
			"title": "No toolkit binding for this API",
			"status": 403,
			"error_origin": "broker",
			"agent_directive": {
				"strategy": "prompt_human",
				"parameters": {"suggested_command": "jentic access request --toolkit api.example.com --wait"},
				"human_readable_instruction": "You are not bound to a toolkit for this API."
			}
		}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	errBuf := new(bytes.Buffer)
	app.Out = out
	app.Err = errBuf
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(errBuf)
	root.SetArgs([]string{
		"execute", "GET:/v1/pets",
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	err := root.Execute()
	var ec interface{ ExitCode() int }
	if !errors.As(err, &ec) || ec.ExitCode() != 2 {
		t.Fatalf("expected exit code 2 on denial, got err=%v", err)
	}
	// The recovery directive must be surfaced on stderr, including the command.
	if !strings.Contains(errBuf.String(), "jentic access request --toolkit api.example.com --wait") {
		t.Errorf("stderr missing suggested_command; got: %s", errBuf.String())
	}
	if !strings.Contains(errBuf.String(), "not bound to a toolkit") {
		t.Errorf("stderr missing instruction; got: %s", errBuf.String())
	}
	// The 403 envelope is still emitted on stdout for machine parsing.
	var envelope map[string]any
	if err := json.Unmarshal(out.Bytes(), &envelope); err != nil {
		t.Fatalf("unmarshal stdout: %v\nraw: %s", err, out.String())
	}
	if envelope["status"] != float64(403) {
		t.Errorf("status = %v, want 403", envelope["status"])
	}
}

func TestExecuteCmdCredentialGapDirectiveExits2(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/problem+json")
		w.Header().Set("Jentic-Error-Origin", "broker")
		w.WriteHeader(http.StatusFailedDependency) // 424
		_, _ = w.Write([]byte(`{
			"type": "credential_not_provisioned",
			"title": "No credential provisioned",
			"status": 424,
			"error_origin": "broker",
			"agent_directive": {
				"strategy": "prompt_human",
				"parameters": {"provisioning_url": "https://console.example/connect/stripe"},
				"human_readable_instruction": "Ask your operator to connect an account."
			}
		}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	errBuf := new(bytes.Buffer)
	app.Out = new(bytes.Buffer)
	app.Err = errBuf
	root := newAPIRootCmd(app.App)
	root.SetOut(app.Out)
	root.SetErr(errBuf)
	root.SetArgs([]string{
		"execute", "GET:/v1/pets",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	err := root.Execute()
	var ec interface{ ExitCode() int }
	if !errors.As(err, &ec) || ec.ExitCode() != 2 {
		t.Fatalf("expected exit code 2 on credential gap, got err=%v", err)
	}
	if !strings.Contains(errBuf.String(), "https://console.example/connect/stripe") {
		t.Errorf("stderr missing provisioning_url; got: %s", errBuf.String())
	}
}

func TestExecuteCmdDirectivelessDenialExits2(t *testing.T) {
	// A broker denial with NO agent_directive (e.g. action_denied from a
	// permission rule) must still exit 2 in default mode — the exit code keys
	// off the denial status, not the presence of a directive. Gating on the
	// directive would let this silently exit 0 (the regression we removed).
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/problem+json")
		w.Header().Set("Jentic-Error-Origin", "broker")
		w.WriteHeader(http.StatusForbidden) // 403, no agent_directive
		_, _ = w.Write([]byte(`{
			"type": "action_denied",
			"title": "The requested operation is denied by a toolkit permission rule.",
			"status": 403,
			"error_origin": "broker"
		}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)
	app.Out = new(bytes.Buffer)
	app.Err = new(bytes.Buffer)
	root := newAPIRootCmd(app.App)
	root.SetOut(app.Out)
	root.SetErr(app.Err)
	root.SetArgs([]string{
		"execute", "GET:/v1/pets",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	err := root.Execute()
	var ec interface{ ExitCode() int }
	if !errors.As(err, &ec) || ec.ExitCode() != 2 {
		t.Fatalf("expected exit code 2 on directive-less denial, got err=%v", err)
	}
	// UX7: a directive-less denial must still hand the user a synthesized
	// next-step keyed off the 403 (whoami + access request), not a dead end.
	errOut := app.Err.(*bytes.Buffer).String()
	for _, want := range []string{"jentic access whoami", "jentic access request"} {
		if !strings.Contains(errOut, want) {
			t.Errorf("synthesized 403 recovery missing %q; stderr:\n%s", want, errOut)
		}
	}
}

func TestExecuteCmdReconnect401DirectiveExits2(t *testing.T) {
	// A 401 credential_needs_reconnect carries an agent_directive (the broker
	// always serializes exc.directive). The CLI must treat 401 as a recoverable
	// denial so the reconnect instruction is surfaced and the exit code is 2 —
	// not a silently-dropped directive (regression for the access-loop review).
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/problem+json")
		w.Header().Set("Jentic-Error-Origin", "broker")
		w.WriteHeader(http.StatusUnauthorized) // 401
		_, _ = w.Write([]byte(`{
			"type": "credential_needs_reconnect",
			"title": "Credential needs reconnect",
			"status": 401,
			"error_origin": "broker",
			"agent_directive": {
				"strategy": "prompt_human",
				"parameters": {},
				"human_readable_instruction": "The connected credential must be reconnected."
			}
		}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	errBuf := new(bytes.Buffer)
	app.Out = new(bytes.Buffer)
	app.Err = errBuf
	root := newAPIRootCmd(app.App)
	root.SetOut(app.Out)
	root.SetErr(errBuf)
	root.SetArgs([]string{
		"execute", "GET:/v1/pets",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	err := root.Execute()
	var ec interface{ ExitCode() int }
	if !errors.As(err, &ec) || ec.ExitCode() != 2 {
		t.Fatalf("expected exit code 2 on 401 reconnect, got err=%v", err)
	}
	if !strings.Contains(errBuf.String(), "must be reconnected") {
		t.Errorf("stderr missing reconnect instruction; got: %s", errBuf.String())
	}
}

func TestExecuteCmdUpstreamPassthrough4xxExitsZero(t *testing.T) {
	// The broker is a transparent forward proxy: an upstream API can return a
	// 401/403/409/424 on a call the broker SUCCESSFULLY proxied. The broker
	// stamps Jentic-Error-Origin: upstream on such mirrored responses. The CLI
	// must NOT treat these as broker denials — exit 0 and pass the body through,
	// not exit 2 with a misleading "recovery required". (Regression: keying the
	// exit code off status alone misclassified upstream 4xx as broker denials.)
	for _, status := range []int{
		http.StatusUnauthorized,
		http.StatusForbidden,
		http.StatusConflict,
		http.StatusFailedDependency,
	} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("Jentic-Error-Origin", "upstream")
				w.WriteHeader(status)
				_, _ = w.Write([]byte(`{"error":"upstream said no"}`))
			}))
			defer srv.Close()

			app := testApp(t)
			seedRegistered(t, app, "default", srv.URL)
			out := new(bytes.Buffer)
			errBuf := new(bytes.Buffer)
			app.Out = out
			app.Err = errBuf
			root := newAPIRootCmd(app.App)
			root.SetOut(out)
			root.SetErr(errBuf)
			root.SetArgs([]string{
				"execute", "GET:/v1/pets",
				"--json",
				"--broker-scheme", "http",
				"--broker-host", srv.Listener.Addr().String(),
			})

			if err := root.Execute(); err != nil {
				t.Fatalf("upstream %d pass-through should exit 0, got err=%v", status, err)
			}
			if strings.Contains(errBuf.String(), "recovery required") {
				t.Errorf("upstream %d wrongly surfaced a broker recovery directive: %s", status, errBuf.String())
			}
		})
	}
}

func TestExecuteCmdSuccessExitsZero(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)
	app.Out = new(bytes.Buffer)
	app.Err = new(bytes.Buffer)
	root := newAPIRootCmd(app.App)
	root.SetOut(app.Out)
	root.SetErr(app.Err)
	root.SetArgs([]string{
		"execute", "POST:/v1/pets",
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("expected nil error (exit 0) on 2xx, got: %v", err)
	}
}

func TestExecuteCmdPathSubstitution(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			_, _ = w.Write([]byte(`{"method":"GET","url":"https://upstream.example/v1/pets/{petId}"}`))
			return
		}
		gotPath = r.URL.Path
		_, _ = w.Write([]byte(`{"id":42}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "getPet",
		"--path", "petId=42",
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !strings.HasSuffix(gotPath, "/v1/pets/42") {
		t.Errorf("path = %q, want to end with /v1/pets/42", gotPath)
	}
}

// upstreamFromRequest returns the broker-relative request target as the broker
// would see it on the wire (RawPath when set, so percent-escapes survive, else
// Path), with the leading "/" trimmed. In main's design the full upstream URL is
// embedded after the broker host (…/https://api.example.com/…), so this is the
// embedded upstream the broker would forward.
func upstreamFromRequest(r *http.Request) string {
	p := r.URL.RawPath
	if p == "" {
		p = r.URL.Path
	}
	return strings.TrimPrefix(p, "/")
}

// Regression: a path parameter value must be percent-escaped before it is
// substituted into the URL template, so a value like "../admin" cannot
// traverse out of its path segment in the reconstructed upstream URL.
func TestExecuteCmdPathParamsEscapeTraversal(t *testing.T) {
	var gotUpstream string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			_, _ = w.Write([]byte(`{"method":"GET","url":"https://api.example.com/v1/items/{itemId}"}`))
			return
		}
		gotUpstream = upstreamFromRequest(r)
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "getItem",
		"--path", "itemId=../admin",
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if strings.Contains(gotUpstream, "../admin") {
		t.Errorf("path traversal not escaped: %q", gotUpstream)
	}
	if !strings.Contains(gotUpstream, "api.example.com/v1/items/..%2Fadmin") {
		t.Errorf("upstream = %q, want escaped itemId segment", gotUpstream)
	}
}

func TestExecuteCmdRawMode(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			_, _ = w.Write([]byte(`{"method":"GET","url":"https://upstream.example/data"}`))
			return
		}
		_, _ = w.Write([]byte("raw-bytes-here"))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "getData",
		"--raw",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if out.String() != "raw-bytes-here" {
		t.Errorf("raw output = %q", out.String())
	}
}

func TestExecuteCmdUpstream4xxExitsZero(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			_, _ = w.Write([]byte(`{"method":"GET","url":"https://upstream.example/fail"}`))
			return
		}
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"error":"not found"}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "missing",
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("expected exit 0 for upstream 4xx, got error: %v", err)
	}

	var envelope map[string]any
	if err := json.Unmarshal(out.Bytes(), &envelope); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if envelope["status"] != float64(404) {
		t.Errorf("status = %v, want 404", envelope["status"])
	}
}

func TestExecuteCmdBadOperationExitsCode2(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"not found"}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "badOp",
		"--json",
	})

	err := root.Execute()
	if err == nil {
		t.Fatal("expected error")
	}
	var ec *ux.CodedError
	if !errors.As(err, &ec) {
		t.Fatalf("error type = %T, want *ux.CodedError", err)
	}
	if ec.Code != ux.CodeResolveFailed || ec.ExitCode() != 2 {
		t.Errorf("code = %s (exit %d), want RESOLVE_FAILED (exit 2)", ec.Code, ec.ExitCode())
	}
}

func TestExecuteCmdSendsBody(t *testing.T) {
	var gotBody string
	var gotContentType string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			_, _ = w.Write([]byte(`{"method":"POST","url":"https://upstream.example/users"}`))
			return
		}
		gotContentType = r.Header.Get("Content-Type")
		data, _ := io.ReadAll(r.Body)
		gotBody = string(data)
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"id":"u1"}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "createUser",
		"-d", `{"name":"Alice"}`,
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if gotBody != `{"name":"Alice"}` {
		t.Errorf("body = %q", gotBody)
	}
	if gotContentType != "application/json" {
		t.Errorf("content-type = %q", gotContentType)
	}
}

func TestExecuteCmdQueryParams(t *testing.T) {
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			_, _ = w.Write([]byte(`{"method":"GET","url":"https://upstream.example/items"}`))
			return
		}
		gotQuery = r.URL.RawQuery
		_, _ = w.Write([]byte(`[]`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "listItems",
		"--query", "limit=10",
		"--query", "offset=5",
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !strings.Contains(gotQuery, "limit=10") || !strings.Contains(gotQuery, "offset=5") {
		t.Errorf("query = %q", gotQuery)
	}
}

func TestExecuteCmdQueryParamsEncoded(t *testing.T) {
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			_, _ = w.Write([]byte(`{"method":"GET","url":"https://upstream.example/items"}`))
			return
		}
		gotQuery = r.URL.RawQuery
		_, _ = w.Write([]byte(`[]`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "listItems",
		"--query", "name=foo bar",
		"--query", "tag=a&b",
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !strings.Contains(gotQuery, "name=foo+bar") || !strings.Contains(gotQuery, "tag=a%26b") {
		t.Errorf("query not properly encoded: %q", gotQuery)
	}
}

func TestExecuteCmdMethodPathDirect(t *testing.T) {
	var gotMethod, gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotPath = r.URL.Path
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "POST:/v1/users",
		"-d", `{"name":"Alice"}`,
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if gotMethod != "POST" {
		t.Errorf("method = %q, want POST", gotMethod)
	}
	if gotPath != "/v1/users" {
		t.Errorf("path = %q, want /v1/users", gotPath)
	}

	var envelope map[string]any
	if err := json.Unmarshal(out.Bytes(), &envelope); err != nil {
		t.Fatalf("unmarshal: %v\nraw: %s", err, out.String())
	}
	if envelope["status"] != float64(200) {
		t.Errorf("status = %v, want 200", envelope["status"])
	}
}

func TestExecuteCmdMethodPathWithPathParams(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		_, _ = w.Write([]byte(`{"id":42}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "GET:/v1/pets/{petId}",
		"--path", "petId=42",
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if gotPath != "/v1/pets/42" {
		t.Errorf("path = %q, want /v1/pets/42", gotPath)
	}
}

func TestExecuteCmdMethodURLDirect(t *testing.T) {
	var gotMethod, gotPath, inspectQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			inspectQuery = r.URL.RawQuery
			_, _ = w.Write([]byte(`{"method":"GET","url":"https://upstream.example/v3/markets"}`))
			return
		}
		gotMethod = r.Method
		gotPath = r.URL.Path
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "GET:https://upstream.example/v3/markets",
		"--json",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	// The METHOD:URL target must be resolved via inspect's id= param, not
	// operation_id=, then routed through the broker as /{upstreamURL}.
	if !strings.Contains(inspectQuery, "id=") || strings.Contains(inspectQuery, "operation_id=") {
		t.Errorf("inspect query = %q, want id= (not operation_id=)", inspectQuery)
	}
	if gotMethod != "GET" {
		t.Errorf("method = %q, want GET", gotMethod)
	}
	if !strings.HasSuffix(gotPath, "/v3/markets") {
		t.Errorf("broker path = %q, want to end with /v3/markets", gotPath)
	}
}

func TestParseMethodPath(t *testing.T) {
	tests := []struct {
		input      string
		wantMethod string
		wantPath   string
	}{
		{"GET:/v1/pets", "GET", "/v1/pets"},
		{"post:/v1/users", "POST", "/v1/users"},
		{"DELETE:/v1/items/{id}", "DELETE", "/v1/items/{id}"},
		{"PATCH:/v1/pets/42", "PATCH", "/v1/pets/42"},
		{"listPets", "", ""},
		{"createUser", "", ""},
		{"notamethod:/foo", "", ""},
		{"GET:", "", ""},
		{"GET:noslash", "", ""},
		// Absolute METHOD:URL forms must NOT match (they resolve via inspect).
		{"GET:https://rest.coincap.io/v3/markets", "", ""},
		{"POST:http://localhost/v1/x", "", ""},
	}
	for _, tt := range tests {
		method, path := parseMethodPath(tt.input)
		if method != tt.wantMethod || path != tt.wantPath {
			t.Errorf("parseMethodPath(%q) = (%q, %q), want (%q, %q)",
				tt.input, method, path, tt.wantMethod, tt.wantPath)
		}
	}
}

// TestExecuteSendsCorrelationAndIdempotencyHeaders proves execute attaches the
// P5.2 correlation headers (F8-6) and the --idempotency-key passthrough (F8-13)
// on the broker request, since it builds the request outside the SDK editor
// chain that would otherwise add them.
func TestExecuteSendsCorrelationAndIdempotencyHeaders(t *testing.T) {
	t.Setenv("JENTIC_SESSION_ID", "sess-batch-7")

	var got http.Header
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			_, _ = w.Write([]byte(`{"method":"POST","url":"https://upstream.example/v1/things"}`))
			return
		}
		got = r.Header.Clone()
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "createThing",
		"--json",
		"--data", `{"name":"x"}`,
		"--idempotency-key", "idem-abc-123",
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	if v := got.Get("X-Jentic-Session-Id"); v != "sess-batch-7" {
		t.Errorf("X-Jentic-Session-Id = %q, want sess-batch-7", v)
	}
	if v := got.Get("Idempotency-Key"); v != "idem-abc-123" {
		t.Errorf("Idempotency-Key = %q, want idem-abc-123", v)
	}
	tp := got.Get("traceparent")
	if !strings.HasPrefix(tp, "00-") || len(tp) != len("00-")+32+1+16+1+2 {
		t.Errorf("traceparent = %q, want a W3C version-00 value", tp)
	}
}

// TestExecuteDryRunDoesNotCallBroker proves --dry-run spreads to execute (F8-15):
// it emits a plan and stops before firing the broker request.
func TestExecuteDryRunDoesNotCallBroker(t *testing.T) {
	var brokerHit bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/inspect" {
			_, _ = w.Write([]byte(`{"method":"POST","url":"https://upstream.example/v1/things"}`))
			return
		}
		brokerHit = true
		t.Error("dry-run must not reach the broker")
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"execute", "createThing",
		"--json", "--dry-run",
		"--data", `{"name":"x"}`,
		"--broker-scheme", "http",
		"--broker-host", srv.Listener.Addr().String(),
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute --dry-run: %v", err)
	}
	if brokerHit {
		t.Fatal("broker was called despite --dry-run")
	}
}

// TestExecuteAgentModeBrokerHostPinned pins SEC-21: in agent mode an explicit
// --broker-host that differs from the environment's broker_url host is rejected
// with a coded RESOLVE_FAILED — an agent must not redirect its bearer + injected
// upstream context at an arbitrary host. A human keeps the override.
func TestExecuteAgentModeBrokerHostPinned(t *testing.T) {
	agentCtx := clictx.WithActiveState(context.Background(), &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:        "bot",
			EnvironmentName:     "prod",
			BaseURL:             "https://ctl.example",
			BrokerURL:           "https://broker.jentic.example",
			InjectedBearerToken: "tok_abc",
		},
		Mode: clictx.ModeAgent,
	})

	app := testApp(t)
	cmd := newExecuteCmd(app)
	// Simulate an agent passing --broker-host at a DIFFERENT host than broker_url.
	if err := cmd.Flags().Set("broker-host", "evil.attacker.example"); err != nil {
		t.Fatal(err)
	}
	cmd.SetContext(agentCtx)

	err := app.executeE(cmd, &executeOptions{brokerHost: "evil.attacker.example", brokerScheme: "https"}, "someOp")
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("agent-mode broker-host override returned %T (%v), want *ux.CodedError", err, err)
	}
	if coded.Code != ux.CodeResolveFailed {
		t.Errorf("code = %q, want RESOLVE_FAILED", coded.Code)
	}
	if !strings.Contains(coded.Msg, "broker.jentic.example") {
		t.Errorf("error should name the pinned broker host: %q", coded.Msg)
	}
}

// TestExecuteHumanModeBrokerHostAllowed is the SEC-21 negative: a HUMAN keeps the
// --broker-host override even against a broker_url env (they own the machine).
// It only needs to get past the SEC-21 gate; we assert the failure (if any) is
// NOT the SEC-21 pin error.
func TestExecuteHumanModeBrokerHostAllowed(t *testing.T) {
	humanCtx := clictx.WithActiveState(context.Background(), &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:        "me",
			EnvironmentName:     "prod",
			BaseURL:             "https://ctl.example",
			BrokerURL:           "https://broker.jentic.example",
			InjectedBearerToken: "tok_abc",
		},
		Mode: clictx.ModeHuman,
	})
	app := testApp(t)
	cmd := newExecuteCmd(app)
	if err := cmd.Flags().Set("broker-host", "localhost:9999"); err != nil {
		t.Fatal(err)
	}
	cmd.SetContext(humanCtx)
	err := app.executeE(cmd, &executeOptions{brokerHost: "localhost:9999", brokerScheme: "http"}, "someOp")
	// It will fail later (no server), but must NOT be the SEC-21 pin error.
	if err != nil && strings.Contains(err.Error(), "not allowed in") {
		t.Errorf("human mode must keep the --broker-host override, got SEC-21 pin error: %v", err)
	}
}
