package api

// mcp_execute_test.go exercises the PR 1-C execute surface: envelope + stamp,
// the §3.7 error-mapping table (denial with directive passthrough, transport
// retryability, resolve → search_apis), the 202-held passthrough, the
// response-size cap, the stdin structural-safety guarantee, the GET/HEAD-only
// contract of execute_read, and the live-route get_execution_result poll.

import (
	"bytes"
	"context"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

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

// TestMCPExecute_DenialRelaysUnknownDirectiveFields pins the VERBATIM half of
// the directive passthrough: the payload carries the RAW agent_directive JSON
// sub-object, so a field this CLI build has never heard of still reaches the
// model (a struct projection would silently drop it).
func TestMCPExecute_DenialRelaysUnknownDirectiveFields(t *testing.T) {
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/problem+json")
		w.Header().Set("Jentic-Error-Origin", "broker")
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"detail":"no toolkit binding","agent_directive":{"strategy":"prompt_human",` +
			`"future_field":"must-survive","parameters":{"nested_unknown":{"keep":"me"}},` +
			`"human_readable_instruction":"Ask your operator."}}`))
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	payload := decodeToolJSON(t, res)
	directive, ok := payload["agent_directive"].(map[string]any)
	if !ok {
		t.Fatalf("payload has no agent_directive: %v", payload)
	}
	if directive["future_field"] != "must-survive" {
		t.Errorf("directive = %v, want the unknown top-level field relayed verbatim", directive)
	}
	params, _ := directive["parameters"].(map[string]any)
	if nested, _ := params["nested_unknown"].(map[string]any); nested["keep"] != "me" {
		t.Errorf("directive.parameters = %v, want the nested unknown value intact", params)
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
	// the failure surfaces immediately. Connection refused is a PRE-SEND
	// failure — the upstream provably never saw the request — so even an
	// unkeyed POST earns retryable: true.
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
		t.Errorf("retryable = %v, want true (pre-send failure: the request never left)", payload["retryable"])
	}
	if payload["next_tool"] != "get_started" {
		t.Errorf("next_tool = %v, want get_started (diagnoses a down instance)", payload["next_tool"])
	}
}

// midflightBroker accepts the request in full and then kills the connection
// without writing a response — a transport failure that is NOT provably
// pre-send: the upstream may have received (and executed) the call.
func midflightBroker(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.ReadAll(r.Body)
		conn, _, err := http.NewResponseController(w).Hijack()
		if err != nil {
			t.Errorf("hijack: %v", err)
			return
		}
		_ = conn.Close()
	}))
	t.Cleanup(srv.Close)
	return srv
}

// TestMCPExecute_MidflightFailureUnkeyedPostIsNotRetryable pins the
// double-execution guard: a mid-flight failure on a POST with no
// Idempotency-Key may have ALREADY executed upstream, so the soft error must
// NOT carry retryable: true — it must say the request may have been
// delivered and route recovery through idempotency keys /
// get_execution_result instead of a blind re-send.
func TestMCPExecute_MidflightFailureUnkeyedPostIsNotRetryable(t *testing.T) {
	broker := midflightBroker(t)
	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets","body":{"name":"Bob"}}`))
	if err != nil {
		t.Fatalf("a transport failure must be a soft error, not a protocol error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeTransportError {
		t.Fatalf("error_code = %v, want %q", payload["error_code"], ux.CodeTransportError)
	}
	if payload["retryable"] != false {
		t.Errorf("retryable = %v, want false (mid-flight unkeyed POST: a retry may double-execute)", payload["retryable"])
	}
	if msg, _ := payload["error"].(string); !strings.Contains(msg, "may already have reached the upstream") {
		t.Errorf("error %q must say the request may have been delivered", msg)
	}
	step, _ := payload["actionable_step"].(string)
	for _, want := range []string{"idempotency_key", "get_execution_result"} {
		if !strings.Contains(step, want) {
			t.Errorf("actionable_step %q must point at %s", step, want)
		}
	}
}

// TestMCPExecute_MidflightFailureWithIdempotencyKeyIsRetryable: the same
// mid-flight failure IS safely retryable when the call carries an
// Idempotency-Key — the broker de-duplicates the re-send.
func TestMCPExecute_MidflightFailureWithIdempotencyKeyIsRetryable(t *testing.T) {
	broker := midflightBroker(t)
	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets","body":{"name":"Bob"},"idempotency_key":"key-1"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeTransportError || payload["retryable"] != true {
		t.Errorf("payload = {error_code: %v, retryable: %v}, want a retryable TRANSPORT_ERROR (keyed POST is de-duplicated)",
			payload["error_code"], payload["retryable"])
	}
}

// TestMCPExecuteRead_MidflightFailureIsRetryable: a GET cannot mutate, so a
// mid-flight failure on the read variant keeps the retryable hint.
func TestMCPExecuteRead_MidflightFailureIsRetryable(t *testing.T) {
	broker := midflightBroker(t)
	s := stampedTestMCPServer(t)
	res, err := s.handleExecuteRead(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute_read", `{"operation_id":"GET:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecuteRead: %v", err)
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeTransportError || payload["retryable"] != true {
		t.Errorf("payload = {error_code: %v, retryable: %v}, want a retryable TRANSPORT_ERROR (GET is idempotent)",
			payload["error_code"], payload["retryable"])
	}
}

// TestMCPCallContexts_DeadlineSplit pins the deadline split: the execute
// family's per-call deadline must sit ABOVE the 60s broker-leg ceiling the
// CLI's execute carries (agentops.DoWith's default client timeout) — the 30s
// control-plane deadline would make the advertised ceiling unreachable —
// while control-plane-only tools keep the tight 30s bound.
func TestMCPCallContexts_DeadlineSplit(t *testing.T) {
	s := newTestMCPServer(t, nil)
	now := time.Now()

	cctx, cancel := s.callContext(context.Background())
	defer cancel()
	ectx, ecancel := s.executeCallContext(context.Background())
	defer ecancel()

	controlDeadline, ok := cctx.Deadline()
	if !ok {
		t.Fatalf("callContext must carry a deadline")
	}
	executeDeadline, ok := ectx.Deadline()
	if !ok {
		t.Fatalf("executeCallContext must carry a deadline")
	}
	if got := executeDeadline.Sub(now); got < 60*time.Second {
		t.Errorf("execute deadline = %v, want >= the 60s CLI execute ceiling", got.Round(time.Second))
	}
	if got := controlDeadline.Sub(now); got > 31*time.Second {
		t.Errorf("control-plane deadline = %v, want the tight 30s bound", got.Round(time.Second))
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

// TestMCPExecute_TruncationDropsRuneSplitAtCap pins the UTF-8 boundary: when
// the cap lands mid-rune, the split rune's leading byte is dropped rather
// than relayed as garbage (a tool result must stay valid UTF-8).
func TestMCPExecute_TruncationDropsRuneSplitAtCap(t *testing.T) {
	// 1023 ASCII bytes, then two-byte runes: byte 1024 (the cap) falls in the
	// middle of the first "é".
	payloadBody := strings.Repeat("a", 1023) + strings.Repeat("é", 64)
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(payloadBody))
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	s.maxResultBytes = 1024
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	payload := decodeToolJSON(t, res)
	if payload["truncated"] != true {
		t.Fatalf("truncated = %v, want true", payload["truncated"])
	}
	body, _ := payload["body"].(string)
	if !utf8.ValidString(body) {
		t.Errorf("truncated body must be valid UTF-8")
	}
	if body != strings.Repeat("a", 1023) {
		t.Errorf("body = %q..., want the 1023 leading bytes with the split rune dropped", body[:16])
	}
}

// TestMCPExecute_TruncationSanitizesBinaryBody pins the docstring's "leading
// bytes, sanitized to valid UTF-8": a binary body's invalid sequences are
// stripped EVERYWHERE, not only at the cap boundary, so the relayed string is
// always valid UTF-8 (and therefore not byte-identical to the raw prefix).
func TestMCPExecute_TruncationSanitizesBinaryBody(t *testing.T) {
	chunk := append([]byte("data"), 0xFF, 0xFE, 0x00)
	raw := bytes.Repeat(chunk, 300) // 2100 bytes, invalid UTF-8 throughout
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write(raw)
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	s.maxResultBytes = 1024
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	payload := decodeToolJSON(t, res)
	if payload["truncated"] != true || payload["total_bytes"] != float64(len(raw)) {
		t.Fatalf("payload = {truncated: %v, total_bytes: %v}, want the truncation envelope for %d bytes",
			payload["truncated"], payload["total_bytes"], len(raw))
	}
	body, _ := payload["body"].(string)
	if !utf8.ValidString(body) {
		t.Errorf("binary body must be sanitized to valid UTF-8")
	}
	if !strings.HasPrefix(body, "data") || strings.ContainsRune(body, 0xFFFD) {
		t.Errorf("body %q..., want the readable bytes kept and invalid sequences dropped (not replaced)", body[:8])
	}
}

// TestMCPExecute_CapsOversizedResponseHeaders pins the header half of the
// §3.7 context-protection cap: response headers ride under their own
// aggregate budget, so a hostile/buggy upstream cannot push megabytes into
// the model's context THROUGH headers while the body cap holds. Small
// headers survive a fat sibling; the marker names the truncation.
func TestMCPExecute_CapsOversizedResponseHeaders(t *testing.T) {
	fat := strings.Repeat("h", 64<<10) // 64 KiB in ONE header, body tiny
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Bloat", fat)
		w.Header().Set("Jentic-Execution-Id", "exec_fat")
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer broker.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"POST:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	if res.IsError {
		t.Fatalf("header truncation is not an error: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if payload["headers_truncated"] != true {
		t.Fatalf("headers_truncated = %v, want true", payload["headers_truncated"])
	}
	headers, _ := payload["headers"].(map[string]any)
	if _, ok := headers["X-Bloat"]; ok {
		t.Errorf("the over-budget header must be dropped")
	}
	if headers["Content-Type"] != "application/json" {
		t.Errorf("headers = %v, want the small headers to survive the fat sibling", headers)
	}
	serialized, _ := json.Marshal(headers)
	if int64(len(serialized)) > s.headerBytesBudget()+256 {
		t.Errorf("serialized headers = %d bytes, want them under the %d budget", len(serialized), s.headerBytesBudget())
	}
	// The BODY cap is untouched: the small body is relayed whole.
	if _, ok := payload["truncated"]; ok {
		t.Errorf("a small body must not carry the body-truncation envelope")
	}
	if body, ok := payload["body"].(map[string]any); !ok || body["ok"] != true {
		t.Errorf("body = %v, want the upstream JSON intact", payload["body"])
	}
	if payload["execution_id"] != "exec_fat" {
		t.Errorf("execution_id = %v, must survive header truncation", payload["execution_id"])
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

// TestMCPExecuteRead_AcceptsLowercaseRegistryMethod pins the resolve-time
// method normalization: a registry inspect document carrying "get" (allowed —
// the document is data, not our canon) must pass the GET/HEAD gate and go on
// the wire as canonical GET, not be rejected with "resolves to get".
func TestMCPExecuteRead_AcceptsLowercaseRegistryMethod(t *testing.T) {
	var gotMethod string
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer broker.Close()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"method":"get","url":"https://acme.com/pets"}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleExecuteRead(activeCtxWithBroker(srv.URL, broker.URL),
		callToolRequest("execute_read", `{"operation_id":"op_lower"}`))
	if err != nil {
		t.Fatalf("a lowercase registry method must not fail the GET/HEAD gate: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %v", res.Content)
	}
	if gotMethod != http.MethodGet {
		t.Errorf("method on the wire = %q, want the canonical GET", gotMethod)
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

// TestMCPGetExecutionResult_TransportFailureIsRetryable pins the §3.7
// transport row on the POLL tool: its most likely transient failure is
// "control plane briefly down, poll again", so a transport failure must come
// back as a retryable TRANSPORT_ERROR with the get_started pointer — not as
// INTERNAL_ERROR ("stop, CLI bug") with no hints. The poll is a GET, so
// re-polling is always safe.
func TestMCPGetExecutionResult_TransportFailureIsRetryable(t *testing.T) {
	// A control plane that is down: bind a listener, note the address, close it.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	baseURL := srv.URL
	srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleGetExecutionResult(activeCtx(baseURL), callToolRequest("get_execution_result", `{"job_id":"job_9"}`))
	if err != nil {
		t.Fatalf("a transport failure must be a soft error, not a protocol error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeTransportError {
		t.Errorf("error_code = %v, want %q (never INTERNAL_ERROR for a down control plane)",
			payload["error_code"], ux.CodeTransportError)
	}
	if payload["retryable"] != true {
		t.Errorf("retryable = %v, want true (polling again is the recovery)", payload["retryable"])
	}
	if payload["next_tool"] != "get_started" {
		t.Errorf("next_tool = %v, want get_started (diagnoses a down instance)", payload["next_tool"])
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
