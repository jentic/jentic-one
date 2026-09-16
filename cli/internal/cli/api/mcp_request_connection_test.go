package api

// mcp_request_connection_test.go exercises the request_connection tool handler
// against an httptest control plane, following the per-tool patterns of the
// catalog suite: wire-shape assertions (identity is injected — no agent_id
// ever rides the body), the create-only result shape (poll_token withheld),
// alias tolerance, and the coded soft-error mappings of the route's failure
// surface (400/403/429/503).

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// connectControlPlane serves POST /integrations:connect and records the raw
// request body for wire assertions.
type connectControlPlane struct {
	status  int
	body    string
	headers map[string]string
	seen    []byte
}

func (c *connectControlPlane) handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/integrations:connect" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		c.seen, _ = io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		for k, v := range c.headers {
			w.Header().Set(k, v)
		}
		w.WriteHeader(c.status)
		_, _ = w.Write([]byte(c.body))
	})
}

func TestMCPRequestConnection_SuccessWithholdsPollToken(t *testing.T) {
	cp := &connectControlPlane{
		status: http.StatusCreated,
		body: `{"session_id":"cs_1","approval_url":"https://one.example/connect/cs_1",
			"poll_token":"pt_secret","resolved_flow":"authorization_code"}`,
	}
	srv := httptest.NewServer(cp.handler())
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleRequestConnection(activeCtx(srv.URL), callToolRequest("request_connection",
		`{"vendor":"github","requested_scopes":["repo"],"reason":"read PRs"}`))
	if err != nil {
		t.Fatalf("handleRequestConnection: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %s", toolResultText(res))
	}

	var wire map[string]any
	if err := json.Unmarshal(cp.seen, &wire); err != nil {
		t.Fatalf("decode wire body: %v", err)
	}
	if wire["vendor"] != "github" || wire["reason"] != "read PRs" {
		t.Errorf("wire body = %v, want vendor github + reason", wire)
	}
	if scopes, _ := wire["requested_scopes"].([]any); len(scopes) != 1 || scopes[0] != "repo" {
		t.Errorf("requested_scopes = %v, want [repo]", wire["requested_scopes"])
	}
	if _, has := wire["agent_id"]; has {
		t.Errorf("agent_id rode the wire (%v) — the control plane injects the caller's identity", wire["agent_id"])
	}

	payload := decodeToolJSON(t, res)
	if payload["session_id"] != "cs_1" || payload["approval_url"] != "https://one.example/connect/cs_1" {
		t.Errorf("payload = %v, want the created session projected", payload)
	}
	if payload["resolved_flow"] != "authorization_code" {
		t.Errorf("resolved_flow = %v, want authorization_code", payload["resolved_flow"])
	}
	if instruction, _ := payload["instruction"].(string); !strings.Contains(instruction, "approval_url") ||
		!strings.Contains(instruction, "whoami") {
		t.Errorf("instruction %q must teach the relay → whoami → retry loop", instruction)
	}
	// THE create-only invariant: no poll leg on this surface, so the
	// capability token must never reach the model.
	if _, has := payload["poll_token"]; has {
		t.Errorf("poll_token leaked into the tool result: %v", payload["poll_token"])
	}
	if strings.Contains(toolResultText(res), "pt_secret") {
		t.Errorf("the poll token's value leaked into the rendered result")
	}
}

func TestMCPRequestConnection_ScopesAliasNormalizes(t *testing.T) {
	cp := &connectControlPlane{
		status: http.StatusCreated,
		body:   `{"session_id":"cs_2","approval_url":"https://one.example/c/2","poll_token":"pt","resolved_flow":"device_authorization"}`,
	}
	srv := httptest.NewServer(cp.handler())
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleRequestConnection(activeCtx(srv.URL), callToolRequest("request_connection",
		`{"vendor":"github","scopes":["repo","read:org"]}`))
	if err != nil {
		t.Fatalf("handleRequestConnection: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %s", toolResultText(res))
	}
	var wire map[string]any
	if err := json.Unmarshal(cp.seen, &wire); err != nil {
		t.Fatalf("decode wire body: %v", err)
	}
	if scopes, _ := wire["requested_scopes"].([]any); len(scopes) != 2 {
		t.Errorf(`requested_scopes = %v, want the "scopes" alias normalized to two entries`, wire["requested_scopes"])
	}
}

func TestMCPRequestConnection_MissingVendorIsInvalidParams(t *testing.T) {
	s := stampedTestMCPServer(t)
	res, err := s.handleRequestConnection(activeCtx("http://127.0.0.1:0"), callToolRequest("request_connection", `{}`))
	if res != nil {
		t.Fatalf("want a protocol error, got a result: %v", res)
	}
	if err == nil || !strings.Contains(err.Error(), "vendor") {
		t.Fatalf("err = %v, want an invalid-params error naming vendor", err)
	}
}

// TestMCPRequestConnection_ExplicitAgentIDIsDroppedNeverForwarded is the
// adversarial twin of the wire assertion in the success test (review L4): a
// caller supplying agent_id in the tool arguments must never impersonate —
// the normalizer drops the unknown key and the control plane injects the
// caller's own identity (the route refuses a supplied agent_id with 403; the
// tool surface simply has no such parameter to refuse).
func TestMCPRequestConnection_ExplicitAgentIDIsDroppedNeverForwarded(t *testing.T) {
	cp := &connectControlPlane{
		status: http.StatusCreated,
		body:   `{"session_id":"cs_3","approval_url":"https://one.example/c/3","poll_token":"pt","resolved_flow":"authorization_code"}`,
	}
	srv := httptest.NewServer(cp.handler())
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleRequestConnection(activeCtx(srv.URL), callToolRequest("request_connection",
		`{"vendor":"github","agent_id":"agnt_other"}`))
	if err != nil {
		t.Fatalf("handleRequestConnection: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %s", toolResultText(res))
	}
	var wire map[string]any
	if err := json.Unmarshal(cp.seen, &wire); err != nil {
		t.Fatalf("decode wire body: %v", err)
	}
	if _, has := wire["agent_id"]; has {
		t.Errorf("agent_id rode the wire (%v) — the explicit argument must be dropped", wire["agent_id"])
	}
	if strings.Contains(string(cp.seen), "agnt_other") {
		t.Errorf("the supplied agent id leaked into the wire body: %s", cp.seen)
	}
}

// TestMCPRequestConnection_OverlongReasonIsInvalidParams pins the client-side
// reason bound (review L2): the route's max_length=1024 must surface as a
// clear invalid-params error here, never as a route 422 rendered as a
// retryable transport failure.
func TestMCPRequestConnection_OverlongReasonIsInvalidParams(t *testing.T) {
	s := stampedTestMCPServer(t)
	res, err := s.handleRequestConnection(activeCtx("http://127.0.0.1:0"), callToolRequest("request_connection",
		`{"vendor":"github","reason":"`+strings.Repeat("x", 1025)+`"}`))
	if res != nil {
		t.Fatalf("want a protocol error, got a result: %v", res)
	}
	if err == nil || !strings.Contains(err.Error(), "1024") {
		t.Fatalf("err = %v, want an invalid-params error naming the 1024 bound", err)
	}
}

func TestMCPRequestConnection_400IsResolveFailedPointingAtSearchCatalog(t *testing.T) {
	cp := &connectControlPlane{status: http.StatusBadRequest, body: `{"detail":"unknown vendor: 'nope'"}`}
	srv := httptest.NewServer(cp.handler())
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleRequestConnection(activeCtx(srv.URL), callToolRequest("request_connection", `{"vendor":"nope"}`))
	if err != nil {
		t.Fatalf("an unknown vendor must be a soft error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeResolveFailed {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeResolveFailed)
	}
	if payload["next_tool"] != "search_catalog" {
		t.Errorf("next_tool = %v, want search_catalog (off-registry APIs go through discovery + the operator)", payload["next_tool"])
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "registry") || !strings.Contains(step, "operator") {
		t.Errorf("actionable_step %q must name the registry constraint and the operator fallback", step)
	}
}

func TestMCPRequestConnection_403IsOperatorScopeGrant(t *testing.T) {
	cp := &connectControlPlane{status: http.StatusForbidden, body: `{"detail":"requires one of: credentials:connect"}`}
	srv := httptest.NewServer(cp.handler())
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleRequestConnection(activeCtx(srv.URL), callToolRequest("request_connection", `{"vendor":"github"}`))
	if err != nil {
		t.Fatalf("handleRequestConnection: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeBrokerDenied {
		t.Errorf("error_code = %v, want %q (a missing scope is an access gap, not a revoked identity)", payload["error_code"], ux.CodeBrokerDenied)
	}
	if _, has := payload["next_tool"]; has {
		t.Errorf("next_tool = %v, want none (the scope grant is an operator action, not a tool call)", payload["next_tool"])
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "credentials:connect") || !strings.Contains(step, "operator") {
		t.Errorf("actionable_step %q must name the credentials:connect scope and route to the operator", step)
	}
}

func TestMCPRequestConnection_429IsRetryableTransportError(t *testing.T) {
	cp := &connectControlPlane{
		status:  http.StatusTooManyRequests,
		body:    `{"detail":"rate limit exceeded"}`,
		headers: map[string]string{"Retry-After": "2"},
	}
	srv := httptest.NewServer(cp.handler())
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleRequestConnection(activeCtx(srv.URL), callToolRequest("request_connection", `{"vendor":"github"}`))
	if err != nil {
		t.Fatalf("handleRequestConnection: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeTransportError {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeTransportError)
	}
	if payload["retryable"] != true {
		t.Errorf("retryable = %v, want true (the per-actor limit clears by itself)", payload["retryable"])
	}
	// Cross-mount alignment (review L1): the Retry-After the route stamps
	// rides the envelope as retry_after_s, like the Python mount's.
	if payload["retry_after_s"] != float64(2) {
		t.Errorf("retry_after_s = %v, want 2 (the route's Retry-After header)", payload["retry_after_s"])
	}
}

func TestMCPRequestConnection_503IsOperatorConfigAction(t *testing.T) {
	cp := &connectControlPlane{status: http.StatusServiceUnavailable, body: `{"detail":"vendor 'github' flow 'authorization_code' not configured: no client_id"}`}
	srv := httptest.NewServer(cp.handler())
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleRequestConnection(activeCtx(srv.URL), callToolRequest("request_connection", `{"vendor":"github"}`))
	if err != nil {
		t.Fatalf("handleRequestConnection: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeBrokerDenied {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeBrokerDenied)
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "operator") || !strings.Contains(step, "github") {
		t.Errorf("actionable_step %q must route the vendor's OAuth config to the operator", step)
	}
}
