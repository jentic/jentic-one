package api

// mcp_session_test.go proves the §3.3 "always boots" contract in-process: an
// SDK client connects to the assembled server over an in-memory transport on
// a machine with NO configuration, lists the tools AND the skill resources,
// and gets a usable get_started diagnosis with a degraded instance stamp — no
// network, no XDG state, no prompts.

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
	"github.com/jentic/jentic-one/cli/internal/skillgen"
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
	for _, want := range []string{"get_started", "whoami", "search_apis", "inspect_operation", "execute_read", "get_execution_result"} {
		tool, ok := names[want]
		if !ok {
			t.Fatalf("tools/list = %v, missing %q", keys(names), want)
		}
		if tool.Annotations == nil || !tool.Annotations.ReadOnlyHint {
			t.Errorf("tool %q must carry readOnlyHint", want)
		}
	}
	// execute is the ONLY tool carrying destructiveHint (§3.2 annotations),
	// and it must not be read-only.
	execTool, ok := names["execute"]
	if !ok {
		t.Fatalf("tools/list = %v, missing execute", keys(names))
	}
	if execTool.Annotations == nil || execTool.Annotations.ReadOnlyHint {
		t.Errorf("execute must not carry readOnlyHint")
	}
	if execTool.Annotations == nil || execTool.Annotations.DestructiveHint == nil || !*execTool.Annotations.DestructiveHint {
		t.Errorf("execute must carry destructiveHint")
	}
	for name, tool := range names {
		if name == "execute" {
			continue
		}
		if tool.Annotations != nil && tool.Annotations.DestructiveHint != nil && *tool.Annotations.DestructiveHint {
			t.Errorf("tool %q must not carry destructiveHint (execute alone does)", name)
		}
	}
	if len(names) != 7 {
		t.Errorf("PR 1-C serves exactly the 7 phase-1 tools, got %v", keys(names))
	}
}

// TestMCPSession_ReadOnlyWithholdsExactlyExecute pins the --read-only contract
// on the 1-C surface: every tool except `execute` is annotated read-only, so
// the flag withholds exactly that one (execute_read and get_execution_result
// stay servable).
func TestMCPSession_ReadOnlyWithholdsExactlyExecute(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, &mcpOptions{readOnly: true})
	cs := connectTestClient(t, s)

	tools, err := cs.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatalf("tools/list: %v", err)
	}
	names := make([]string, 0, len(tools.Tools))
	for _, tool := range tools.Tools {
		names = append(names, tool.Name)
		if tool.Name == "execute" {
			t.Errorf("--read-only must withhold the execute tool")
		}
	}
	if len(tools.Tools) != 6 {
		t.Errorf("--read-only must serve the 6 read-only tools, got %v", names)
	}
}

// TestMCPSession_FullRoundTrip drives the complete §3.2 flow — whoami →
// search_apis → inspect_operation → execute, plus a resources/read of the
// hosted skill — through a real client session against httptest control and
// broker planes: state, alias tolerance, the broker leg, the stamped
// envelopes, and the resource provenance must survive the full JSON-RPC round
// trip, not just direct handler calls.
func TestMCPSession_FullRoundTrip(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || !strings.Contains(r.URL.Path, "acme.com/pets") {
			t.Errorf("broker got %s %s, want the resolved upstream GET", r.Method, r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer tok_abc" {
			t.Errorf("broker Authorization = %q, want the agent bearer", got)
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Jentic-Execution-Id", "exec_42")
		_, _ = w.Write([]byte(`{"pets":[{"id":1}]}`))
	}))
	defer broker.Close()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/me":
			_, _ = w.Write([]byte(`{"type":"agent","id":"agent_1","name":"pets-agent","scopes":["execute"],"status":"active","token_scopes":["execute"],"toolkit_bindings":[]}`))
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
		case "/skills/jentic.md":
			w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
			_, _ = w.Write([]byte(hostedJentic))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	s := newTestMCPServer(t, nil)
	s.instances.fetch = fetchInstance // the real GET /instance path, against the httptest plane
	cs := connectTestClientWithContext(activeCtxWithBroker(srv.URL, broker.URL), t, s)
	ctx := context.Background()

	// 1. whoami — identity before discovery.
	res, err := cs.CallTool(ctx, &mcp.CallToolParams{Name: "whoami"})
	if err != nil {
		t.Fatalf("whoami: %v", err)
	}
	if res.IsError {
		t.Fatalf("whoami soft-errored: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if payload["id"] != "agent_1" || payload["status"] != "active" {
		t.Fatalf("whoami payload = %v, want the agent identity", payload)
	}

	// 2. search_apis.
	res, err = cs.CallTool(ctx, &mcp.CallToolParams{
		Name:      "search_apis",
		Arguments: map[string]any{"query": "list pets"},
	})
	if err != nil {
		t.Fatalf("search_apis: %v", err)
	}
	if res.IsError {
		t.Fatalf("search_apis soft-errored: %v", res.Content)
	}
	payload = decodeToolJSON(t, res)
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

	// 3. inspect_operation — the hit's id under the `id` alias, as a drifting
	// model would send it.
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

	// 4. execute — the inspected operation, through the broker leg.
	res, err = cs.CallTool(ctx, &mcp.CallToolParams{
		Name:      "execute",
		Arguments: map[string]any{"operation_id": opID},
	})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if res.IsError {
		t.Fatalf("execute soft-errored: %v", res.Content)
	}
	payload = decodeToolJSON(t, res)
	if payload["status"] != float64(200) || payload["execution_id"] != "exec_42" {
		t.Fatalf("execute envelope = %v, want status 200 + execution_id", payload)
	}
	body, _ := payload["body"].(map[string]any)
	if _, ok := body["pets"]; !ok {
		t.Errorf("execute body = %v, want the upstream JSON parsed", payload["body"])
	}
	if stamp, ok := payload["instance"].(map[string]any); !ok || stamp["backend"] != "local" {
		t.Errorf("execute result stamp = %v, want the live identity", payload["instance"])
	}

	// 5. resources/read alongside the tool round trip: connected to a live
	// backend, skill://jentic serves the HOSTED copy (session source of
	// truth) with its provenance riding the wire _meta.
	rr, err := cs.ReadResource(ctx, &mcp.ReadResourceParams{URI: "skill://jentic"})
	if err != nil {
		t.Fatalf("resources/read: %v", err)
	}
	if len(rr.Contents) != 1 {
		t.Fatalf("contents = %d, want 1", len(rr.Contents))
	}
	if rr.Contents[0].Text != hostedJentic {
		t.Errorf("resource text = %q, want the backend's bytes verbatim", rr.Contents[0].Text)
	}
	if rr.Contents[0].Meta[skillMetaSource] != string(skillgen.SourceHosted) {
		t.Errorf("meta source = %v, want hosted (the backend answered)", rr.Contents[0].Meta[skillMetaSource])
	}
	if rr.Contents[0].Meta[skillMetaVersion] != "7" {
		t.Errorf("meta version = %v, want the hosted document's version", rr.Contents[0].Meta[skillMetaVersion])
	}
}

// TestMCPSession_MissingQueryIsProtocolError proves the invalid-params
// contract over a real client session, not just a direct handler call: the
// *jsonrpc.Error a handler returns must reach the CLIENT as a protocol error
// (the SDK's error propagation), never be flattened into a tool result.
func TestMCPSession_MissingQueryIsProtocolError(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, nil)
	cs := connectTestClient(t, s)

	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{
		Name:      "search_apis",
		Arguments: map[string]any{},
	})
	if err == nil {
		t.Fatalf("want a protocol error at the client, got a result: %v", res)
	}
	// The invalid-params message must survive the round trip so the model can
	// correct the call.
	if !strings.Contains(err.Error(), "query") {
		t.Errorf("client-side error %q must name the missing parameter", err.Error())
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
	if len(tools.Tools) != 6 {
		t.Errorf("tools = %d, want 6 after exclusion", len(tools.Tools))
	}
}

// TestMCPSession_ResourcesWorkPreAuth extends the §3.3 always-boots contract
// to the PR 1-D resource surface over the real wire: with NO configuration,
// resources/list serves the full bundled set (+ the index) and resources/read
// answers with the embedded copy stamped source=bundled — never an error.
func TestMCPSession_ResourcesWorkPreAuth(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, nil)
	cs := connectTestClient(t, s)
	ctx := context.Background()

	list, err := cs.ListResources(ctx, nil)
	if err != nil {
		t.Fatalf("resources/list must work with no config: %v", err)
	}
	uris := make(map[string]*mcp.Resource, len(list.Resources))
	for _, r := range list.Resources {
		uris[r.URI] = r
	}
	names := skillgen.BundledNames()
	wantURIs := make([]string, 0, len(names)+1)
	wantURIs = append(wantURIs, skillIndexURI)
	for _, name := range names {
		wantURIs = append(wantURIs, skillURIScheme+name)
	}
	for _, want := range wantURIs {
		r, ok := uris[want]
		if !ok {
			t.Fatalf("resources/list is missing %q (got %d resources)", want, len(list.Resources))
		}
		if r.Description == "" || r.MIMEType == "" {
			t.Errorf("resource %q must carry a description and MIME type", want)
		}
	}
	if len(uris) != len(wantURIs) {
		t.Errorf("resources = %d, want exactly the bundled set + index (%d)", len(uris), len(wantURIs))
	}

	res, err := cs.ReadResource(ctx, &mcp.ReadResourceParams{URI: "skill://jentic"})
	if err != nil {
		t.Fatalf("resources/read must work pre-auth: %v", err)
	}
	if len(res.Contents) != 1 {
		t.Fatalf("contents = %d, want 1", len(res.Contents))
	}
	c := res.Contents[0]
	want, err := skillgen.RawBundled("jentic")
	if err != nil {
		t.Fatalf("RawBundled: %v", err)
	}
	if c.Text != string(want) {
		t.Errorf("pre-auth read must serve the bundled bytes verbatim (len %d vs %d)", len(c.Text), len(want))
	}
	if c.Meta[skillMetaSource] != string(skillgen.SourceBundled) {
		t.Errorf("meta source = %v, want bundled (survives the wire round trip)", c.Meta[skillMetaSource])
	}
	if c.Meta[skillMetaVersion] == nil {
		t.Errorf("meta must carry the content version")
	}

	// An unregistered URI is a protocol-level resource-not-found, never a
	// handler panic or a silent empty read.
	if _, err := cs.ReadResource(ctx, &mcp.ReadResourceParams{URI: "skill://no-such-skill"}); err == nil {
		t.Errorf("reading an unregistered skill URI must fail")
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
