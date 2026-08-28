package api

// mcp_discovery_test.go exercises the PR 1-B tool handlers against an httptest
// control plane: envelope passthrough with the sibling instance stamp, alias/
// coercion tolerance on the wire, the 404 → RESOLVE_FAILED soft error with its
// search_apis recovery pointer, and the redaction funnel on inspect bodies.

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// stampedTestMCPServer is newTestMCPServer with a HEALTHY instance-stamp seam,
// for tests asserting the fresh stamp on success envelopes.
func stampedTestMCPServer(t *testing.T) *mcpServer {
	t.Helper()
	s := newTestMCPServer(t, nil)
	instanceID := "digest-test"
	s.instances.fetch = func(context.Context) (*control.InstanceIdentityResponse, error) {
		return &control.InstanceIdentityResponse{Backend: "local", Host: "127.0.0.1:8000", InstanceId: &instanceID}, nil
	}
	return s
}

// callToolRequest builds the raw request shape the SDK hands a ToolHandler.
func callToolRequest(name, arguments string) *mcp.CallToolRequest {
	params := &mcp.CallToolParamsRaw{Name: name}
	if arguments != "" {
		params.Arguments = json.RawMessage(arguments)
	}
	return &mcp.CallToolRequest{Params: params}
}

func TestMCPSearchAPIs_EnvelopePassthroughWithStamp(t *testing.T) {
	var gotBody control.SearchRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/search" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &gotBody)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"data": [{"type":"operation","api":{"vendor":"acme","name":"pets","version":"v1","host":"acme.com"},"operation_id":"op1","method":"GET","url":"/pets","name":"List Pets","relevance_score":0.9,"_links":{"inspect":"/inspect?id=GET%20/pets"}}],
			"has_more": true,
			"next_cursor": "page2"
		}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleSearchAPIs(activeCtx(srv.URL), callToolRequest("search_apis", `{"query":"pets","limit":5,"cursor":"c0"}`))
	if err != nil {
		t.Fatalf("handleSearchAPIs: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %v", res.Content)
	}
	if gotBody.Query != "pets" || gotBody.Limit == nil || *gotBody.Limit != 5 || gotBody.Cursor == nil || *gotBody.Cursor != "c0" {
		t.Errorf("wire body = %+v, want query/limit/cursor mirrored", gotBody)
	}

	payload := decodeToolJSON(t, res)
	if payload["schema_version"] != mcpSchemaVersion {
		t.Errorf("schema_version = %v, want %q", payload["schema_version"], mcpSchemaVersion)
	}
	data, ok := payload["data"].([]any)
	if !ok || len(data) != 1 {
		t.Fatalf("data = %v, want one hit", payload["data"])
	}
	hit := data[0].(map[string]any)
	if hit["operation_id"] != "op1" || hit["relevance_score"] != 0.9 {
		t.Errorf("hit = %v, want the searchE projection (operation_id, relevance_score)", hit)
	}
	if payload["has_more"] != true || payload["next_cursor"] != "page2" {
		t.Errorf("pagination = has_more %v next_cursor %v, want passthrough true/page2", payload["has_more"], payload["next_cursor"])
	}
	stamp, ok := payload["instance"].(map[string]any)
	if !ok {
		t.Fatalf("result has no top-level instance stamp: %v", payload)
	}
	if stamp["backend"] != "local" || stamp["instance_id"] != "digest-test" {
		t.Errorf("instance stamp = %v, want the fresh identity", stamp)
	}
}

func TestMCPSearchAPIs_EmptyResultsEmitEmptyArray(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":[],"has_more":false,"next_cursor":null}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleSearchAPIs(activeCtx(srv.URL), callToolRequest("search_apis", `{"query":"nothing"}`))
	if err != nil {
		t.Fatalf("handleSearchAPIs: %v", err)
	}
	text := res.Content[0].(*mcp.TextContent).Text
	if strings.Contains(text, `"data":null`) {
		t.Fatalf("data serialized as null, want []: %s", text)
	}
	payload := decodeToolJSON(t, res)
	if data, ok := payload["data"].([]any); !ok || len(data) != 0 {
		t.Errorf("data = %v, want an empty array", payload["data"])
	}
}

func TestMCPSearchAPIs_AliasAndStringCoercionReachTheWire(t *testing.T) {
	var gotBody control.SearchRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &gotBody)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":[],"has_more":false,"next_cursor":null}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	// `api` (singular alias) as a bare string, `limit` as a numeric string —
	// both must be normalized before the SDK call.
	res, err := s.handleSearchAPIs(activeCtx(srv.URL), callToolRequest("search_apis", `{"query":"q","api":"acme/pets/v1","limit":"3"}`))
	if err != nil {
		t.Fatalf("handleSearchAPIs: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %v", res.Content)
	}
	if gotBody.Apis == nil || len(*gotBody.Apis) != 1 || (*gotBody.Apis)[0] != "acme/pets/v1" {
		t.Errorf("apis on the wire = %v, want the coerced one-element list", gotBody.Apis)
	}
	if gotBody.Limit == nil || *gotBody.Limit != 3 {
		t.Errorf("limit on the wire = %v, want the parsed 3", gotBody.Limit)
	}
}

func TestMCPSearchAPIs_MissingQueryIsInvalidParams(t *testing.T) {
	s := stampedTestMCPServer(t)
	res, err := s.handleSearchAPIs(activeCtx("http://127.0.0.1:0"), callToolRequest("search_apis", `{}`))
	if res != nil {
		t.Fatalf("want a protocol error, got a result: %v", res)
	}
	var wireErr *jsonrpc.Error
	if !errors.As(err, &wireErr) || wireErr.Code != jsonrpc.CodeInvalidParams {
		t.Fatalf("err = %v, want a JSON-RPC invalid-params error", err)
	}
	if !strings.Contains(wireErr.Message, "query") {
		t.Errorf("message %q must name the missing parameter", wireErr.Message)
	}
}

func TestMCPSearchAPIs_NoContextIsSoftResolveFailed(t *testing.T) {
	s := newTestMCPServer(t, nil)
	// No active state in the context: the canonical no-context RESOLVE_FAILED,
	// pointed at get_started (the default mapping).
	res, err := s.handleSearchAPIs(context.Background(), callToolRequest("search_apis", `{"query":"pets"}`))
	if err != nil {
		t.Fatalf("a diagnosable state must be a soft error, not a protocol error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeResolveFailed {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeResolveFailed)
	}
	if payload["next_tool"] != "get_started" {
		t.Errorf("next_tool = %v, want get_started", payload["next_tool"])
	}
	if stamp, ok := payload["instance"].(map[string]any); !ok || stamp["backend"] != backendUnreachable {
		t.Errorf("soft errors carry the (degraded) instance stamp too, got %v", payload["instance"])
	}
}

func TestMCPInspectOperation_PassesBodyThroughWithStamp(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/inspect" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		if got := r.URL.Query().Get("operation_id"); got != "op_abc" {
			t.Errorf("operation_id on the wire = %q, want op_abc", got)
		}
		if got := r.URL.Query().Get("revision_id"); got != "rev1" {
			t.Errorf("revision_id on the wire = %q, want rev1", got)
		}
		if got := r.Header.Get("Accept"); got != "application/json" {
			t.Errorf("Accept = %q, want application/json (format is fixed to json)", got)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"method":"GET","url":"https://api.acme.com/v1/pets","parameters":[{"name":"limit","in":"query"}]}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	// `id` alias for operation_id must fold before the wire call.
	res, err := s.handleInspectOperation(activeCtx(srv.URL), callToolRequest("inspect_operation", `{"id":"op_abc","revision":"rev1"}`))
	if err != nil {
		t.Fatalf("handleInspectOperation: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if payload["method"] != "GET" || payload["url"] != "https://api.acme.com/v1/pets" {
		t.Errorf("payload = %v, want the raw inspect document passed through", payload)
	}
	if payload["schema_version"] != mcpSchemaVersion {
		t.Errorf("schema_version = %v, want %q stamped as a sibling", payload["schema_version"], mcpSchemaVersion)
	}
	if stamp, ok := payload["instance"].(map[string]any); !ok || stamp["backend"] != "local" {
		t.Errorf("instance stamp = %v, want the fresh identity", payload["instance"])
	}
}

func TestMCPInspectOperation_404IsSoftResolveFailedPointingAtSearch(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"operation not found"}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleInspectOperation(activeCtx(srv.URL), callToolRequest("inspect_operation", `{"operation_id":"op_missing"}`))
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
	if msg, _ := payload["error"].(string); !strings.Contains(msg, "op_missing") {
		t.Errorf("error %q must name the unresolved target", msg)
	}
	// The identity resolved fine — the recovery is rediscovery, not setup.
	if payload["next_tool"] != "search_apis" {
		t.Errorf("next_tool = %v, want search_apis", payload["next_tool"])
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "search_apis") {
		t.Errorf("actionable_step %q must route through search_apis", step)
	}
}

func TestMCPInspectOperation_MissingTargetIsInvalidParams(t *testing.T) {
	s := stampedTestMCPServer(t)
	res, err := s.handleInspectOperation(activeCtx("http://127.0.0.1:0"), callToolRequest("inspect_operation", `{}`))
	if res != nil {
		t.Fatalf("want a protocol error, got a result: %v", res)
	}
	var wireErr *jsonrpc.Error
	if !errors.As(err, &wireErr) || wireErr.Code != jsonrpc.CodeInvalidParams {
		t.Fatalf("err = %v, want a JSON-RPC invalid-params error", err)
	}
	for _, spelling := range []string{"operation_id", "id", "uuid"} {
		if !strings.Contains(wireErr.Message, spelling) {
			t.Errorf("message %q must name the accepted spelling %q", wireErr.Message, spelling)
		}
	}
}

func TestMCPInspectOperation_RedactsSecretsInBody(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		// An inspect document can embed example secrets/auth blocks — the
		// SEC guarantee inspectE carries (round-3 P0) must hold here too.
		_, _ = w.Write([]byte(`{"method":"GET","url":"https://api.acme.com/v1/pets","examples":{"api_key":"sk-verysecret"}}`))
	}))
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleInspectOperation(activeCtx(srv.URL), callToolRequest("inspect_operation", `{"operation_id":"op_abc"}`))
	if err != nil {
		t.Fatalf("handleInspectOperation: %v", err)
	}
	text := res.Content[0].(*mcp.TextContent).Text
	if strings.Contains(text, "sk-verysecret") {
		t.Fatalf("secret leaked through the tool result: %s", text)
	}
	if !strings.Contains(text, "[REDACTED]") {
		t.Errorf("want the redaction marker in the result, got: %s", text)
	}
}
