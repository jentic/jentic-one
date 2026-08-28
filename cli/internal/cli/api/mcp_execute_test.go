package api

// mcp_execute_test.go exercises the PR 1-C execute surface: envelope + stamp,
// the §3.7 error-mapping table (denial with directive passthrough, transport
// retryability, resolve → search_apis), the 202-held passthrough, the
// response-size cap, the stdin structural-safety guarantee, the GET/HEAD-only
// contract of execute_read, and the live-route get_execution_result poll.

import (
	"context"
	"encoding/pem"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/jsonrpc"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// activeCtxWithBroker is activeCtx with the environment's broker_url set —
// the only broker source on the MCP surface.
func activeCtxWithBroker(baseURL, brokerURL string) context.Context {
	return clictx.WithActiveState(context.Background(), &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:        "test-agent",
			EnvironmentName:     "test",
			BaseURL:             baseURL,
			BrokerURL:           brokerURL,
			InjectedBearerToken: "tok_abc",
		},
		Mode: clictx.ModeHuman,
	})
}

func TestMCPExecute_SuccessEnvelopeWithStamp(t *testing.T) {
	var got *http.Request
	var gotBody []byte
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Clone(context.Background())
		gotBody, _ = io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Jentic-Execution-Id", "exec_1")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"id":7,"name":"Bob"}`))
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets/{petId}/toys","inputs":{"petId":"42","verbose":true},"body":{"name":"Bob"},"headers":{"X-Extra":"1"}}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %v", res.Content)
	}

	// The wire request: path param substituted, the leftover input as query,
	// the explicit body, the custom header, and the agent bearer.
	if got.URL.Path != "/v1/pets/42/toys" {
		t.Errorf("broker path = %q, want the {petId} placeholder filled", got.URL.Path)
	}
	if got.URL.Query().Get("verbose") != "true" {
		t.Errorf("query = %q, want the non-placeholder input as a query param", got.URL.RawQuery)
	}
	if string(gotBody) != `{"name":"Bob"}` {
		t.Errorf("body on the wire = %q, want the explicit body argument", gotBody)
	}
	if got.Header.Get("X-Extra") != "1" || got.Header.Get("Authorization") != "Bearer tok_abc" {
		t.Errorf("headers on the wire = %v, want the custom header and the bearer", got.Header)
	}

	// The result: the exact CLI envelope keys as a strict superset with the
	// sibling instance stamp (§3.7.4).
	payload := decodeToolJSON(t, res)
	for _, key := range []string{"schema_version", "status", "headers", "body", "execution_id", "instance"} {
		if _, ok := payload[key]; !ok {
			t.Errorf("payload missing %q: %v", key, payload)
		}
	}
	if payload["schema_version"] != mcpSchemaVersion || payload["status"] != float64(http.StatusCreated) || payload["execution_id"] != "exec_1" {
		t.Errorf("envelope = %v, want schema_version/status/execution_id mirrored", payload)
	}
	if body, ok := payload["body"].(map[string]any); !ok || body["name"] != "Bob" {
		t.Errorf("body = %v, want the upstream JSON parsed", payload["body"])
	}
	if _, ok := payload["truncated"]; ok {
		t.Errorf("small body must not carry the truncation envelope")
	}
	if stamp, ok := payload["instance"].(map[string]any); !ok || stamp["backend"] != "local" {
		t.Errorf("instance stamp = %v, want the fresh identity", payload["instance"])
	}
}

func TestMCPExecute_DenialPassesDirectiveThrough(t *testing.T) {
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/problem+json")
		w.Header().Set("Jentic-Error-Origin", "broker")
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"detail":"no toolkit binding","agent_directive":{"strategy":"prompt_human","parameters":{"suggested_command":"jentic access request --toolkit acme/pets --wait"},"human_readable_instruction":"Ask your operator to bind this agent to acme/pets."}}`))
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("a broker denial must be a soft error, not a protocol error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result for a broker denial")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeBrokerDenied {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeBrokerDenied)
	}
	if payload["retryable"] != false {
		t.Errorf("retryable = %v, want false (non-retryable until access changes)", payload["retryable"])
	}
	if payload["next_tool"] != "whoami" {
		t.Errorf("next_tool = %v, want whoami", payload["next_tool"])
	}
	details, _ := payload["details"].(map[string]any)
	if details["http_status"] != float64(http.StatusForbidden) {
		t.Errorf("details = %v, want the denying http_status", payload["details"])
	}
	// The directive passes through VERBATIM.
	directive, ok := payload["agent_directive"].(map[string]any)
	if !ok {
		t.Fatalf("payload has no agent_directive: %v", payload)
	}
	if directive["strategy"] != "prompt_human" {
		t.Errorf("directive.strategy = %v, want prompt_human", directive["strategy"])
	}
	params, _ := directive["parameters"].(map[string]any)
	if params["suggested_command"] != "jentic access request --toolkit acme/pets --wait" {
		t.Errorf("directive.parameters = %v, want the suggested_command verbatim", directive["parameters"])
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "acme/pets") {
		t.Errorf("actionable_step %q must relay the directive instruction", step)
	}
}

func TestMCPExecute_DenialWithoutDirectiveSynthesizesHint(t *testing.T) {
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Jentic-Error-Origin", "broker")
		w.WriteHeader(http.StatusFailedDependency)
		_, _ = w.Write([]byte(`{"detail":"no credential"}`))
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeBrokerDenied {
		t.Fatalf("error_code = %v, want BROKER_DENIED", payload["error_code"])
	}
	if _, ok := payload["agent_directive"]; ok {
		t.Errorf("no directive was sent; none must be fabricated")
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "credential") {
		t.Errorf("actionable_step %q must synthesize the 424 recovery (UX7)", step)
	}
}

func TestMCPExecute_UpstreamErrorIsNormalResult(t *testing.T) {
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		// The broker relaying an upstream 403 stamps origin=upstream: NOT a
		// denial — the call ran and this is the upstream's answer (§3.7 row 1).
		w.Header().Set("Jentic-Error-Origin", "upstream")
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"error":"upstream says no"}`))
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	if res.IsError {
		t.Fatalf("an upstream 4xx is a normal result, got soft error: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if payload["status"] != float64(http.StatusForbidden) {
		t.Errorf("status = %v, want the upstream 403 relayed", payload["status"])
	}
}

func TestMCPExecute_HeldEnvelopePassesThrough(t *testing.T) {
	// The ask-tier 202-held broker behavior has not shipped; this mocks its
	// envelope (§3.4) and pins the passthrough: NOT an error, directive and
	// job pointer intact, so the model can poll with get_execution_result.
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Jentic-Execution-Id", "exec_held")
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"job_id":"job_9","status":"held","agent_directive":{"strategy":"wait","parameters":{"job_id":"job_9","retry_after_seconds":30},"human_readable_instruction":"This call is held for human approval; poll the job for the outcome."}}`))
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	if res.IsError {
		t.Fatalf("a held (202) envelope is a normal result, got soft error: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if payload["status"] != float64(http.StatusAccepted) || payload["execution_id"] != "exec_held" {
		t.Fatalf("envelope = %v, want the 202 + execution id relayed", payload)
	}
	body, _ := payload["body"].(map[string]any)
	if body["job_id"] != "job_9" {
		t.Errorf("body = %v, want the job pointer intact", payload["body"])
	}
	directive, _ := body["agent_directive"].(map[string]any)
	if directive["strategy"] != "wait" {
		t.Errorf("held directive = %v, want the wait strategy passed through", body["agent_directive"])
	}
}

func TestMCPExecute_TransportFailureIsRetryableSoftError(t *testing.T) {
	// A broker that is down: bind a listener, note the address, close it.
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	brokerURL := broker.URL
	broker.Close()

	s := stampedTestMCPServer(t)
	// POST without an idempotency key: never retried by the SDK policy, so
	// the failure surfaces immediately.
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", brokerURL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("a transport failure must be a soft error, not a protocol error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeTransportError {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeTransportError)
	}
	if payload["retryable"] != true {
		t.Errorf("retryable = %v, want true (the broker may come back)", payload["retryable"])
	}
	if payload["next_tool"] != "get_started" {
		t.Errorf("next_tool = %v, want get_started (diagnoses a down instance)", payload["next_tool"])
	}
}

func TestMCPExecute_ResolveFailurePointsAtSearch(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"operation not found"}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker(srv.URL, "http://127.0.0.1:1"),
		callToolRequest("execute", `{"operation_id":"op_missing"}`))
	if err != nil {
		t.Fatalf("a resolve failure must be a soft error, not a protocol error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeResolveFailed {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeResolveFailed)
	}
	if payload["next_tool"] != "search_apis" {
		t.Errorf("next_tool = %v, want search_apis (the id is wrong, not the setup)", payload["next_tool"])
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "search_apis") {
		t.Errorf("actionable_step %q must route through search_apis", step)
	}
}

func TestMCPExecute_NoContextIsSoftResolveFailed(t *testing.T) {
	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(context.Background(), callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("a diagnosable state must be a soft error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeResolveFailed || payload["next_tool"] != "get_started" {
		t.Errorf("payload = %v, want the canonical no-context RESOLVE_FAILED → get_started", payload)
	}
}

func TestMCPExecute_MissingTargetIsInvalidParams(t *testing.T) {
	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", "http://127.0.0.1:1"),
		callToolRequest("execute", `{}`))
	if res != nil {
		t.Fatalf("want a protocol error, got a result: %v", res)
	}
	var wireErr *jsonrpc.Error
	if !errors.As(err, &wireErr) || wireErr.Code != jsonrpc.CodeInvalidParams {
		t.Fatalf("err = %v, want a JSON-RPC invalid-params error", err)
	}
}

func TestMCPExecute_TruncatesOversizedBody(t *testing.T) {
	big := strings.Repeat("x", 4096)
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Jentic-Execution-Id", "exec_big")
		_, _ = w.Write([]byte(`{"blob":"` + big + `"}`))
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	s.maxResultBytes = 1024
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	if res.IsError {
		t.Fatalf("truncation is not an error: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if payload["truncated"] != true {
		t.Fatalf("truncated = %v, want true", payload["truncated"])
	}
	if payload["total_bytes"] != float64(len(big)+11) {
		t.Errorf("total_bytes = %v, want the full body size %d", payload["total_bytes"], len(big)+11)
	}
	if payload["execution_id"] != "exec_big" {
		t.Errorf("execution_id = %v, must survive truncation (it is the retrieval pointer)", payload["execution_id"])
	}
	body, _ := payload["body"].(string)
	if int64(len(body)) > s.maxResultBytes {
		t.Errorf("body length = %d, want <= the %d cap", len(body), s.maxResultBytes)
	}
	if !strings.HasPrefix(body, `{"blob":"xxx`) {
		t.Errorf("body must be the leading prefix of the raw bytes, got %q...", body[:16])
	}
}

// TestMCPExecute_NeverReadsStdin pins the §3.7 structural guarantee: under
// stdio MCP, stdin is the JSON-RPC wire — a tool call with no body argument
// must send NO body and must not consume a single byte from stdin, even when
// stdin is a non-TTY pipe with data pending (the exact condition that trips
// executeE's stdin fallback).
func TestMCPExecute_NeverReadsStdin(t *testing.T) {
	sentinel := `{"protocol":"frames that must survive"}`
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	if _, err := w.WriteString(sentinel); err != nil {
		t.Fatalf("write: %v", err)
	}
	_ = w.Close()
	orig := os.Stdin
	os.Stdin = r
	t.Cleanup(func() { os.Stdin = orig; _ = r.Close() })

	var gotBody []byte
	var hadBody bool
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		gotBody, _ = io.ReadAll(req.Body)
		hadBody = len(gotBody) > 0
		_, _ = w.Write([]byte(`{}`))
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	// No "body" argument at all — the case executeE would resolve from stdin.
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %v", res.Content)
	}
	if hadBody {
		t.Errorf("broker received a body (%q); a bodyless tool call must send none", gotBody)
	}
	// Every stdin byte must still be there: nothing on the MCP path read it.
	leftover, err := io.ReadAll(r)
	if err != nil {
		t.Fatalf("read stdin pipe: %v", err)
	}
	if string(leftover) != sentinel {
		t.Fatalf("stdin was consumed: %d of %d bytes remain — the MCP path must never touch the JSON-RPC wire",
			len(leftover), len(sentinel))
	}
}

func TestMCPExecuteRead_RejectsNonGetOperations(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"method":"POST","url":"https://acme.com/pets"}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleExecuteRead(activeCtxWithBroker(srv.URL, "http://127.0.0.1:1"),
		callToolRequest("execute_read", `{"operation_id":"op_post"}`))
	if res != nil {
		t.Fatalf("want a protocol error, got a result: %v", res)
	}
	var wireErr *jsonrpc.Error
	if !errors.As(err, &wireErr) || wireErr.Code != jsonrpc.CodeInvalidParams {
		t.Fatalf("err = %v, want a JSON-RPC invalid-params error", err)
	}
	if !strings.Contains(wireErr.Message, "execute") {
		t.Errorf("message %q must point at the execute tool", wireErr.Message)
	}
}

func TestMCPExecuteRead_RejectsBodyArgument(t *testing.T) {
	s := stampedTestMCPServer(t)
	_, err := s.handleExecuteRead(activeCtxWithBroker("http://127.0.0.1:8000", "http://127.0.0.1:1"),
		callToolRequest("execute_read", `{"operation_id":"GET:/v1/pets","body":{"x":1}}`))
	var wireErr *jsonrpc.Error
	if !errors.As(err, &wireErr) || wireErr.Code != jsonrpc.CodeInvalidParams {
		t.Fatalf("err = %v, want a JSON-RPC invalid-params error", err)
	}
}

func TestMCPExecuteRead_GetRoundTrip(t *testing.T) {
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method = %s, want GET", r.Method)
		}
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleExecuteRead(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute_read", `{"operation_id":"GET:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecuteRead: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if payload["status"] != float64(http.StatusOK) {
		t.Errorf("status = %v, want 200", payload["status"])
	}
}

// TestMCPExecute_BrokerLegHonorsCAPinAndHook pins §3.7.2: the broker leg is
// built by clictx (CA-pinned to the environment's ca_cert_path, fail-closed)
// with the session's attribution hook composed over it — the User-Agent
// reaches the BROKER, not just the control plane.
func TestMCPExecute_BrokerLegHonorsCAPinAndHook(t *testing.T) {
	var gotUA string
	broker := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotUA = r.Header.Get("User-Agent")
		_, _ = w.Write([]byte(`{}`))
	}))
	defer broker.Close()

	// Write the test server's own CA cert as the environment's bundle.
	pemPath := filepath.Join(t.TempDir(), "ca.pem")
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: broker.Certificate().Raw})
	if err := os.WriteFile(pemPath, pemBytes, 0o600); err != nil {
		t.Fatalf("write ca bundle: %v", err)
	}

	s := stampedTestMCPServer(t)
	state := &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:        "test-agent",
			EnvironmentName:     "test",
			BaseURL:             "https://cp.example.com",
			BrokerURL:           broker.URL,
			CACertPath:          pemPath,
			InjectedBearerToken: "tok_abc",
		},
		Mode: clictx.ModeHuman,
	}
	ctx := clictx.WithTransportHook(clictx.WithActiveState(context.Background(), state), s.transportHook())

	res, err := s.handleExecute(ctx, callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	if res.IsError {
		t.Fatalf("CA-pinned broker call must succeed against the pinned cert: %v", res.Content)
	}
	if !strings.HasPrefix(gotUA, "jentic-mcp/") {
		t.Errorf("broker User-Agent = %q, want the attribution hook composed onto the broker leg", gotUA)
	}

	// SEC-20 fail-closed: a set-but-broken bundle is an error, never a silent
	// fallback to system roots.
	state.CACertPath = filepath.Join(t.TempDir(), "missing.pem")
	res, err = s.handleExecute(ctx, callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	if !res.IsError {
		t.Fatalf("a broken ca_cert_path must fail closed")
	}
	payload := decodeToolJSON(t, res)
	if msg, _ := payload["error"].(string); !strings.Contains(msg, "ca_cert_path") {
		t.Errorf("error %q must name the broken ca_cert_path", msg)
	}
}

// TestMCPGetExecutionResult_LiveRoutesRoundTrip drives the poll tool against
// httptest versions of the LIVE routes it wires (§3.4): GET /jobs/{id} and
// GET /jobs/{id}/result.
func TestMCPGetExecutionResult_LiveRoutesRoundTrip(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/jobs/job_9":
			_, _ = w.Write([]byte(`{"job_id":"job_9","kind":"execution","status":"completed","execution_id":"exec_9","created_at":"2026-08-28T12:00:00Z","_links":{"self":"/jobs/job_9"}}`))
		case "/jobs/job_9/result":
			_, _ = w.Write([]byte(`{"status":201,"body":{"id":7}}`))
		default:
			t.Errorf("unexpected control-plane call %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	// `id` alias for job_id must fold before the wire call.
	res, err := s.handleGetExecutionResult(activeCtx(srv.URL), callToolRequest("get_execution_result", `{"id":"job_9"}`))
	if err != nil {
		t.Fatalf("handleGetExecutionResult: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if payload["job_id"] != "job_9" || payload["status"] != "completed" || payload["execution_id"] != "exec_9" {
		t.Errorf("payload = %v, want the job projection", payload)
	}
	result, ok := payload["result"].(map[string]any)
	if !ok {
		t.Fatalf("completed job must include its result document: %v", payload)
	}
	if result["status"] != float64(201) {
		t.Errorf("result = %v, want the /result body passed through", result)
	}
	if stamp, ok := payload["instance"].(map[string]any); !ok || stamp["backend"] != "local" {
		t.Errorf("instance stamp = %v, want the fresh identity", payload["instance"])
	}
}

func TestMCPGetExecutionResult_PendingJobHasNoResult(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(r.URL.Path, "/result") {
			t.Errorf("a pending job's result must not be fetched")
		}
		_, _ = w.Write([]byte(`{"job_id":"job_9","kind":"execution","status":"pending","created_at":"2026-08-28T12:00:00Z","_links":{"self":"/jobs/job_9"}}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleGetExecutionResult(activeCtx(srv.URL), callToolRequest("get_execution_result", `{"job_id":"job_9"}`))
	if err != nil {
		t.Fatalf("handleGetExecutionResult: %v", err)
	}
	if res.IsError {
		t.Fatalf("a pending job is a normal result the model polls again: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if payload["status"] != "pending" {
		t.Errorf("status = %v, want pending", payload["status"])
	}
	if _, ok := payload["result"]; ok {
		t.Errorf("pending job must carry no result key")
	}
}

func TestMCPGetExecutionResult_UnknownJobIsSoftResolveFailed(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/problem+json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"job not found"}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleGetExecutionResult(activeCtx(srv.URL), callToolRequest("get_execution_result", `{"job_id":"job_gone"}`))
	if err != nil {
		t.Fatalf("an unknown job must be a soft error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeResolveFailed {
		t.Errorf("error_code = %v, want RESOLVE_FAILED", payload["error_code"])
	}
	// The identity is fine — the pointer is this tool with the corrected id,
	// never get_started.
	if payload["next_tool"] != "get_execution_result" {
		t.Errorf("next_tool = %v, want get_execution_result", payload["next_tool"])
	}
}

func TestMCPGetExecutionResult_MissingJobIDIsInvalidParams(t *testing.T) {
	s := stampedTestMCPServer(t)
	_, err := s.handleGetExecutionResult(activeCtx("http://127.0.0.1:1"), callToolRequest("get_execution_result", `{}`))
	var wireErr *jsonrpc.Error
	if !errors.As(err, &wireErr) || wireErr.Code != jsonrpc.CodeInvalidParams {
		t.Fatalf("err = %v, want a JSON-RPC invalid-params error", err)
	}
}

func TestMCPGetExecutionResult_TruncatesOversizedResult(t *testing.T) {
	big := strings.Repeat("y", 4096)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(r.URL.Path, "/result") {
			_, _ = w.Write([]byte(`{"blob":"` + big + `"}`))
			return
		}
		_, _ = w.Write([]byte(`{"job_id":"job_9","kind":"execution","status":"completed","created_at":"2026-08-28T12:00:00Z","_links":{"self":"/jobs/job_9"}}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	s.maxResultBytes = 512
	res, err := s.handleGetExecutionResult(activeCtx(srv.URL), callToolRequest("get_execution_result", `{"job_id":"job_9"}`))
	if err != nil {
		t.Fatalf("handleGetExecutionResult: %v", err)
	}
	payload := decodeToolJSON(t, res)
	if payload["truncated"] != true {
		t.Errorf("truncated = %v, want true", payload["truncated"])
	}
	if result, _ := payload["result"].(string); int64(len(result)) > s.maxResultBytes {
		t.Errorf("result length = %d, want <= the %d cap", len(result), s.maxResultBytes)
	}
}

func TestResolveMCPBrokerTarget(t *testing.T) {
	state := func(baseURL, brokerURL string) *clictx.ActiveState {
		return &clictx.ActiveState{ResolvedState: &sdkconfig.ResolvedState{
			EnvironmentName: "test", BaseURL: baseURL, BrokerURL: brokerURL,
		}}
	}

	t.Run("environment broker_url wins", func(t *testing.T) {
		scheme, host, err := resolveMCPBrokerTarget(state("https://cp.example.com", "https://broker.example.com:8100"))
		if err != nil || scheme != "https" || host != "broker.example.com:8100" {
			t.Fatalf("got %s://%s, %v", scheme, host, err)
		}
	})
	t.Run("local default without broker_url", func(t *testing.T) {
		scheme, host, err := resolveMCPBrokerTarget(state("http://127.0.0.1:8000", ""))
		if err != nil || scheme != "https" || host != "127.0.0.1:8100" {
			t.Fatalf("got %s://%s, %v", scheme, host, err)
		}
	})
	t.Run("malformed broker_url fails closed", func(t *testing.T) {
		_, _, err := resolveMCPBrokerTarget(state("http://127.0.0.1:8000", "not a url"))
		if asCoded(err).Code != ux.CodeResolveFailed {
			t.Fatalf("err = %v, want coded RESOLVE_FAILED", err)
		}
	})
	t.Run("remote control plane without broker_url fails closed", func(t *testing.T) {
		_, _, err := resolveMCPBrokerTarget(state("https://cp.example.com", ""))
		if asCoded(err).Code != ux.CodeResolveFailed {
			t.Fatalf("err = %v, want coded RESOLVE_FAILED (never dial the loopback default with a remote CP)", err)
		}
		if !strings.Contains(err.Error(), "broker") {
			t.Errorf("error %q must name the missing broker", err.Error())
		}
	})
}
