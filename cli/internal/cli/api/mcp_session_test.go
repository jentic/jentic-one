package api

// mcp_session_test.go proves the §3.3 "always boots" contract in-process: an
// SDK client connects to the assembled server over an in-memory transport on
// a machine with NO configuration, lists the tools, and gets a usable
// get_started diagnosis with a degraded instance stamp — no network, no XDG
// state, no prompts.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// connectTestClient wires an SDK client to s over in-memory pipes and returns
// the live client session.
func connectTestClient(t *testing.T, s *mcpServer) *mcp.ClientSession {
	t.Helper()
	return connectTestClientWithContext(context.Background(), t, s)
}

// connectTestClientCtx is connectTestClient with a caller-supplied SERVER
// session context — kept as an alias of connectTestClientWithContext so both
// naming generations of the session tests share one implementation.
func connectTestClientCtx(ctx context.Context, t *testing.T, s *mcpServer) *mcp.ClientSession {
	t.Helper()
	return connectTestClientWithContext(ctx, t, s)
}

// connectTestClientWithContext is connectTestClient with a caller-supplied
// server-side context — the in-process stand-in for mcpE's run(ctx): values on
// it (the interceptor's ActiveState, the transport hook) must reach the tool
// handlers; the tests pin that they survive the SDK's jsonrpc2 plumbing into
// the tool-handler contexts.
func connectTestClientWithContext(serverCtx context.Context, t *testing.T, s *mcpServer) *mcp.ClientSession {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)

	clientT, serverT := mcp.NewInMemoryTransports()
	ss, err := s.server.Connect(serverCtx, serverT, nil)
	if err != nil {
		t.Fatalf("server connect: %v", err)
	}
	t.Cleanup(func() { _ = ss.Wait() })

	client := mcp.NewClient(&mcp.Implementation{Name: "jentic-test-client", Version: "1.0"}, nil)
	cs, err := client.Connect(ctx, clientT, nil)
	if err != nil {
		t.Fatalf("client connect: %v", err)
	}
	t.Cleanup(func() { _ = cs.Close() })
	return cs
}

// decodeToolJSON parses the single text content of a tool result as JSON.
func decodeToolJSON(t *testing.T, res *mcp.CallToolResult) map[string]any {
	t.Helper()
	if len(res.Content) != 1 {
		t.Fatalf("content items = %d, want 1", len(res.Content))
	}
	text, ok := res.Content[0].(*mcp.TextContent)
	if !ok {
		t.Fatalf("content = %T, want *mcp.TextContent", res.Content[0])
	}
	var payload map[string]any
	if err := json.Unmarshal([]byte(text.Text), &payload); err != nil {
		t.Fatalf("tool result is not JSON: %v\n%s", err, text.Text)
	}
	return payload
}

func TestMCPSession_ToolsListWorksWithNoConfig(t *testing.T) {
	// A hermetic machine: empty XDG trees, no file-less env session. The
	// handlers read the context's ActiveState (none is injected here — exactly
	// the interceptor's degraded no-config resolution).
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, nil)
	cs := connectTestClient(t, s)
	ctx := context.Background()

	tools, err := cs.ListTools(ctx, nil)
	if err != nil {
		t.Fatalf("tools/list must work with no config: %v", err)
	}
	names := make(map[string]*mcp.Tool, len(tools.Tools))
	for _, tool := range tools.Tools {
		names[tool.Name] = tool
	}
	for _, want := range []string{"get_started", "whoami", "search_apis", "inspect_operation"} {
		tool, ok := names[want]
		if !ok {
			t.Fatalf("tools/list = %v, missing %q", keys(names), want)
		}
		if tool.Annotations == nil || !tool.Annotations.ReadOnlyHint {
			t.Errorf("tool %q must carry readOnlyHint", want)
		}
	}
	if len(names) != 4 {
		t.Errorf("PR 1-B serves exactly get_started, whoami, search_apis, and inspect_operation, got %v", keys(names))
	}
}

// TestMCPSession_ReadOnlyServesDiscoveryTools pins the --read-only contract on
// the 1-B surface: every discovery tool is annotated read-only, so the flag
// must not withhold any of them (it starts biting with 1-C's execute).
func TestMCPSession_ReadOnlyServesDiscoveryTools(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, &mcpOptions{readOnly: true})
	cs := connectTestClient(t, s)

	tools, err := cs.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatalf("tools/list: %v", err)
	}
	if len(tools.Tools) != 4 {
		names := make([]string, 0, len(tools.Tools))
		for _, tool := range tools.Tools {
			names = append(names, tool.Name)
		}
		t.Errorf("--read-only must serve all 4 read-only tools, got %v", names)
	}
}

// TestMCPSession_DiscoveryRoundTrip drives search_apis → inspect_operation
// through a real client session against an httptest control plane: the state
// and pagination/alias tolerance must survive the full JSON-RPC round trip,
// not just a direct handler call.
func TestMCPSession_DiscoveryRoundTrip(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/search":
			_, _ = w.Write([]byte(`{
				"data": [{"type":"operation","api":{"vendor":"acme","name":"pets","version":"v1","host":"acme.com"},"operation_id":"op1","method":"GET","url":"/pets","name":"List Pets","relevance_score":0.9,"_links":{"inspect":"/inspect?id=GET%20/pets"}}],
				"has_more": false,
				"next_cursor": ""
			}`))
		case "/inspect":
			_, _ = w.Write([]byte(`{"method":"GET","url":"https://acme.com/pets","parameters":[]}`))
		case "/instance":
			_, _ = w.Write([]byte(`{"backend":"local","host":"127.0.0.1:8000","instance_id":"digest-1"}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	s := newTestMCPServer(t, nil)
	s.instances.fetch = fetchInstance // the real GET /instance path, against the httptest plane
	cs := connectTestClientWithContext(activeCtx(srv.URL), t, s)
	ctx := context.Background()

	res, err := cs.CallTool(ctx, &mcp.CallToolParams{
		Name:      "search_apis",
		Arguments: map[string]any{"query": "list pets"},
	})
	if err != nil {
		t.Fatalf("search_apis: %v", err)
	}
	if res.IsError {
		t.Fatalf("search_apis soft-errored: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	hits, ok := payload["data"].([]any)
	if !ok || len(hits) != 1 {
		t.Fatalf("data = %v, want one hit", payload["data"])
	}
	opID, _ := hits[0].(map[string]any)["operation_id"].(string)
	if opID == "" {
		t.Fatalf("hit carries no operation_id: %v", hits[0])
	}
	if stamp, ok := payload["instance"].(map[string]any); !ok || stamp["backend"] != "local" {
		t.Errorf("instance stamp = %v, want the live identity", payload["instance"])
	}

	// Feed the hit's operation_id back — under the `id` alias, as a drifting
	// model would.
	res, err = cs.CallTool(ctx, &mcp.CallToolParams{
		Name:      "inspect_operation",
		Arguments: map[string]any{"id": opID},
	})
	if err != nil {
		t.Fatalf("inspect_operation: %v", err)
	}
	if res.IsError {
		t.Fatalf("inspect_operation soft-errored: %v", res.Content)
	}
	payload = decodeToolJSON(t, res)
	if payload["method"] != "GET" || payload["url"] != "https://acme.com/pets" {
		t.Errorf("inspect payload = %v, want the raw contract passed through", payload)
	}
}

func TestMCPSession_GetStartedDiagnosesNoConfig(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, nil)
	cs := connectTestClient(t, s)

	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "get_started"})
	if err != nil {
		t.Fatalf("get_started must answer pre-auth: %v", err)
	}
	if res.IsError {
		t.Fatalf("get_started is a diagnosis, not an error result")
	}
	payload := decodeToolJSON(t, res)
	if payload["state"] != setupNoConfig {
		t.Errorf("state = %v, want %q", payload["state"], setupNoConfig)
	}
	instruction, _ := payload["instruction"].(string)
	if instruction != instructionNoConfig {
		t.Errorf("instruction = %q, want the no-config operator instruction verbatim", instruction)
	}

	// Every tool result carries the top-level sibling `instance` stamp; with
	// no reachable control plane it is the degraded form.
	stamp, ok := payload["instance"].(map[string]any)
	if !ok {
		t.Fatalf("result has no top-level instance stamp: %v", payload)
	}
	if stamp["backend"] != backendUnreachable {
		t.Errorf("instance.backend = %v, want %q", stamp["backend"], backendUnreachable)
	}
}

func TestMCPSession_ExcludeToolsFilters(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, &mcpOptions{excludeTools: []string{"whoami"}})
	cs := connectTestClient(t, s)

	tools, err := cs.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatalf("tools/list: %v", err)
	}
	for _, tool := range tools.Tools {
		if tool.Name == "whoami" {
			t.Errorf("--exclude-tools=whoami must withhold the tool")
		}
	}
	if len(tools.Tools) != 3 {
		t.Errorf("tools = %d, want 3 after exclusion", len(tools.Tools))
	}
}

func keys(m map[string]*mcp.Tool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

// --- configured-session contracts (SDK context propagation + attribution) ----

// sessionCtx builds the server session context exactly the way mcpE does —
// ActiveState (file-less token, so no mint dial) + the server's transport
// hook — pointed at baseURL.
func sessionCtx(s *mcpServer, baseURL string) context.Context {
	ctx := clictx.WithTransportHook(context.Background(), s.transportHook())
	return clictx.WithActiveState(ctx, &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:        "test-agent",
			EnvironmentName:     "test",
			BaseURL:             baseURL,
			InjectedBearerToken: "tok_abc",
		},
		Mode: clictx.ModeAgent,
	})
}

// newSessionMCPServer is newTestMCPServer WITHOUT the fetch stub: the
// configured-session tests exercise the production fetchInstance path
// (clictx.GetControlClient + the context's transport hook) against a local
// control-plane double.
func newSessionMCPServer(t *testing.T) *mcpServer {
	t.Helper()
	return newMCPServer(&app{App: &cmdcore.App{}}, "0.0.0-test", &mcpOptions{}, discardLogger())
}

// TestMCPSession_ContextValuesReachHandlersAndTransport pins the load-bearing
// SDK behavior the whole configured surface depends on: the values `jentic
// mcp` puts on the session context (clictx.ActiveState, the transport hook)
// survive the SDK's jsonrpc2 plumbing into tool-handler contexts. If an SDK
// upgrade stopped propagating them, get_started would diagnose no_config on a
// configured machine and backend calls would lose their attribution headers —
// this test turns that silent regression into a failure.
func TestMCPSession_ContextValuesReachHandlersAndTransport(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	type attribution struct{ userAgent, sessionID string }
	seen := make(chan attribution, 8)
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/instance" {
			http.NotFound(w, r)
			return
		}
		seen <- attribution{userAgent: r.Header.Get("User-Agent"), sessionID: r.Header.Get("X-Jentic-Session-Id")}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"backend":"local","host":"127.0.0.1:8000","canonical_base_url":"http://127.0.0.1:8000"}`))
	}))
	t.Cleanup(ts.Close)

	s := newSessionMCPServer(t)
	cs := connectTestClientCtx(sessionCtx(s, ts.URL), t, s)

	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "get_started"})
	if err != nil {
		t.Fatalf("get_started: %v", err)
	}
	if res.IsError {
		t.Fatalf("get_started errored: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	// State "ready" is only reachable when the handler saw the session's
	// ActiveState (file-less identity resolves) AND the GET /instance probe
	// went through the context-built client to the double.
	if payload["state"] != setupReady {
		t.Errorf("state = %v, want %q (the session ActiveState must reach the handler context)", payload["state"], setupReady)
	}
	stamp, _ := payload["instance"].(map[string]any)
	if stamp == nil || stamp["backend"] != "local" {
		t.Errorf("instance stamp = %#v, want the double's identity (fresh, not degraded)", payload["instance"])
	}

	select {
	case a := <-seen:
		// The in-process client's clientInfo rides the call, so the hook's
		// User-Agent carries the upgraded form — prefix intact for the
		// backend's derive_origin.
		if a.userAgent != "jentic-mcp/0.0.0-test (jentic-test-client/1.0)" {
			t.Errorf("User-Agent = %q, want the attribution hook's client-upgraded jentic-mcp form", a.userAgent)
		}
		if a.sessionID != s.sessionID {
			t.Errorf("X-Jentic-Session-Id = %q, want the per-process fallback %q", a.sessionID, s.sessionID)
		}
	default:
		t.Fatal("the control plane never saw a request through the hooked transport")
	}
}

// TestMCPSession_RevokedIdentity401MapsToNotAuthenticated pins the §3.7
// error-table row this server exists for: a wire 401 from GET /me (token
// rejected post-mint — revoked/kill-switched identity) surfaces as a soft
// error coded NOT_AUTHENTICATED with the get_started pointer, never the
// INTERNAL_ERROR "report a CLI bug" catch-all.
func TestMCPSession_RevokedIdentity401MapsToNotAuthenticated(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/me":
			w.Header().Set("Content-Type", "application/problem+json")
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"detail":"identity revoked"}`))
		case "/instance":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"backend":"local","host":"127.0.0.1:8000","canonical_base_url":"http://127.0.0.1:8000"}`))
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(ts.Close)

	s := newSessionMCPServer(t)
	cs := connectTestClientCtx(sessionCtx(s, ts.URL), t, s)

	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "whoami"})
	if err != nil {
		t.Fatalf("whoami must soft-error, not protocol-error: %v", err)
	}
	if !res.IsError {
		t.Fatal("a revoked identity must surface as an isError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeNotAuthenticated {
		t.Errorf("error_code = %v, want %q (§3.7: revoked identity is an auth failure)", payload["error_code"], ux.CodeNotAuthenticated)
	}
	if payload["next_tool"] != "get_started" {
		t.Errorf("next_tool = %v, want get_started (the recovery-loop pointer must fire)", payload["next_tool"])
	}
	msg, _ := payload["error"].(string)
	if !strings.Contains(msg, "identity revoked") {
		t.Errorf("error %q should carry the control plane's detail", msg)
	}
}
