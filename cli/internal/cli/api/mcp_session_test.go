package api

// mcp_session_test.go proves the §3.3 "always boots" contract in-process: an
// SDK client connects to the assembled server over an in-memory transport on
// a machine with NO configuration, lists the tools, and gets a usable
// get_started diagnosis with a degraded instance stamp — no network, no XDG
// state, no prompts.

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// connectTestClient wires an SDK client to s over in-memory pipes and returns
// the live client session.
func connectTestClient(t *testing.T, s *mcpServer) *mcp.ClientSession {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)

	clientT, serverT := mcp.NewInMemoryTransports()
	ss, err := s.server.Connect(ctx, serverT, nil)
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
	for _, want := range []string{"get_started", "whoami"} {
		tool, ok := names[want]
		if !ok {
			t.Fatalf("tools/list = %v, missing %q", keys(names), want)
		}
		if tool.Annotations == nil || !tool.Annotations.ReadOnlyHint {
			t.Errorf("tool %q must carry readOnlyHint", want)
		}
	}
	if len(names) != 2 {
		t.Errorf("PR 1-A registers exactly get_started and whoami, got %v", keys(names))
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
	if len(tools.Tools) != 1 {
		t.Errorf("tools = %d, want 1 after exclusion", len(tools.Tools))
	}
}

func keys(m map[string]*mcp.Tool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
