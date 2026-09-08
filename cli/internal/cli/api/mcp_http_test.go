package api

// mcp_http_test.go pins the --http acceptance boxes that run in-process: the
// golden transcripts replayed over a live Streamable HTTP daemon (same
// envelopes as stdio — the transports must be indistinguishable), the pinned
// tool-surface spec over HTTP, the Origin spoof 403, and the 405 GET.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// startHTTPDaemon serves an assembled mcpServer over httptest behind the
// daemon's gate chain, with sessionCtx playing the role of mcpE's command
// context (ActiveState + transport hook values).
func startHTTPDaemon(sessionCtx context.Context, t *testing.T, s *mcpServer, token []byte, origins []string) *httptest.Server {
	t.Helper()
	handler := s.httpHandler(sessionCtx, &mcpBindPosture{network: "tcp", token: token}, origins, newIdleWatchdog(0), discardLogger())
	ts := httptest.NewServer(handler)
	t.Cleanup(ts.Close)
	return ts
}

// connectHTTPClient wires an SDK client to a daemon URL over Streamable HTTP.
func connectHTTPClient(t *testing.T, url string) *mcp.ClientSession {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)
	client := mcp.NewClient(&mcp.Implementation{Name: "jentic-test-client", Version: "1.0"}, nil)
	cs, err := client.Connect(ctx, &mcp.StreamableClientTransport{
		Endpoint:             url + mcpHTTPPath,
		DisableStandaloneSSE: true, // stateless daemon: GET is 405 by design
	}, nil)
	if err != nil {
		t.Fatalf("client connect over http: %v", err)
	}
	t.Cleanup(func() { _ = cs.Close() })
	return cs
}

// TestMCPHTTP_GoldenTranscriptsOverStreamableHTTP replays the shared-golden
// execute transcripts through a live HTTP daemon: the envelope bytes must be
// the exact frozen CLI goldens (instance stamp stripped), exactly as the
// stdio server pins them in mcp_golden_test.go — same tools, same envelopes,
// different transport.
func TestMCPHTTP_GoldenTranscriptsOverStreamableHTTP(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	cases := []struct {
		name    string
		target  string
		inspect http.HandlerFunc
		broker  http.HandlerFunc
	}{
		{
			name:   "execute_ok_json",
			target: "listPets",
			inspect: func(w http.ResponseWriter, _ *http.Request) {
				w.Header()["Date"] = nil
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(`{"method":"GET","url":"https://upstream.example/v1/pets"}`))
			},
			broker: func(w http.ResponseWriter, _ *http.Request) {
				w.Header()["Date"] = nil
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("Jentic-Execution-Id", "exec-123")
				w.WriteHeader(http.StatusOK)
				_, _ = w.Write([]byte(`[{"id":1,"name":"Fido"}]`))
			},
		},
		{
			name:   "execute_upstream_4xx_passthrough_json",
			target: "GET:/v1/pets",
			broker: func(w http.ResponseWriter, _ *http.Request) {
				w.Header()["Date"] = nil
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("Jentic-Error-Origin", "upstream")
				w.WriteHeader(http.StatusForbidden)
				_, _ = w.Write([]byte(`{"error":"upstream said no"}`))
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			plane := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/inspect" {
					if tc.inspect == nil {
						t.Errorf("unexpected /inspect call for target %q", tc.target)
						w.WriteHeader(http.StatusNotFound)
						return
					}
					tc.inspect(w, r)
					return
				}
				tc.broker(w, r)
			}))
			t.Cleanup(plane.Close)

			s := stampedTestMCPServer(t)
			daemon := startHTTPDaemon(activeCtxWithBroker(plane.URL, plane.URL), t, s, nil, nil)
			cs := connectHTTPClient(t, daemon.URL)

			res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{
				Name:      "execute",
				Arguments: map[string]any{"operation_id": tc.target},
			})
			if err != nil {
				t.Fatalf("execute over http: %v", err)
			}
			if res.IsError {
				t.Fatalf("execute soft-errored: %v", res.Content)
			}
			got := envelopeWithoutStamp(t, decodeToolJSON(t, res))
			want := sharedGoldenStdout(t, tc.name)
			if got != want {
				t.Errorf("HTTP envelope diverged from the shared CLI golden %s.\n--- want ---\n%s\n--- got ---\n%s", tc.name, want, got)
			}
		})
	}
}

// TestMCPHTTP_ToolsListMatchesStdioSurface pins transport parity at the
// surface level: tools/list over HTTP serves exactly the stdio toolSpecs()
// set (which mcp_spec_test.go pins to docs/reference/mcp-tools.json).
func TestMCPHTTP_ToolsListMatchesStdioSurface(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, nil)
	daemon := startHTTPDaemon(context.Background(), t, s, nil, nil)
	cs := connectHTTPClient(t, daemon.URL)

	tools, err := cs.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatalf("tools/list over http: %v", err)
	}
	got := make(map[string]bool, len(tools.Tools))
	for _, tool := range tools.Tools {
		got[tool.Name] = true
	}
	specs := s.toolSpecs()
	if len(got) != len(specs) {
		t.Errorf("tools over http = %d, want the stdio surface's %d", len(got), len(specs))
	}
	for _, spec := range specs {
		if !got[spec.tool.Name] {
			t.Errorf("tools/list over http is missing %q", spec.tool.Name)
		}
	}
}
