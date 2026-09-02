package api

// mcp_connect_test.go pins the relay's contract: an SDK client speaking MCP
// stdio through the pump reaches a live --http daemon and gets the SAME
// transcripts (tools/list, tool calls) as any other transport, while the
// relay holds no key material; plus the pump-level rules (bearer handling,
// plaintext refusal, error synthesis, SSE unwrap).

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// connectRelayedClient runs the relay in-process on a pipe pair and wires an
// SDK client to its stdio side — the harness shape the acceptance box
// prescribes (daemon → relay → transcripts), minus process spawning (the
// spawn variant lives in tests/mcpdaemon).
func connectRelayedClient(t *testing.T, relay *mcpRelay) *mcp.ClientSession {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)

	clientToRelay, relayIn := io.Pipe() // client writes → relay stdin
	relayOut, relayWrites := io.Pipe()  // relay stdout → client reads
	relay.out = relayWrites

	go func() { _ = relay.run(ctx, clientToRelay) }()
	t.Cleanup(func() { _ = relayIn.Close() })

	client := mcp.NewClient(&mcp.Implementation{Name: "jentic-test-client", Version: "1.0"}, nil)
	cs, err := client.Connect(ctx, &mcp.IOTransport{Reader: relayOut, Writer: relayIn}, nil)
	if err != nil {
		t.Fatalf("client connect through the relay: %v", err)
	}
	t.Cleanup(func() { _ = cs.Close() })
	return cs
}

// TestMCPRelay_TranscriptsThroughLiveDaemon drives the full stdio contract
// through relay → HTTP daemon: tools/list serves the stdio surface and
// get_started answers pre-auth — the §3.3 always-boots contract must survive
// two transport hops.
func TestMCPRelay_TranscriptsThroughLiveDaemon(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, nil)
	daemon := startHTTPDaemon(context.Background(), t, s, nil, nil)

	relay, err := newMCPRelay(&mcpRelayOptions{url: daemon.URL}, io.Discard, discardLogger())
	if err != nil {
		t.Fatalf("newMCPRelay: %v", err)
	}
	cs := connectRelayedClient(t, relay)
	ctx := context.Background()

	tools, err := cs.ListTools(ctx, nil)
	if err != nil {
		t.Fatalf("tools/list through the relay: %v", err)
	}
	if len(tools.Tools) != len(s.toolSpecs()) {
		t.Errorf("tools through the relay = %d, want the stdio surface's %d", len(tools.Tools), len(s.toolSpecs()))
	}

	res, err := cs.CallTool(ctx, &mcp.CallToolParams{Name: "get_started"})
	if err != nil {
		t.Fatalf("get_started through the relay: %v", err)
	}
	payload := decodeToolJSON(t, res)
	if payload["state"] != setupNoConfig {
		t.Errorf("state = %v, want %q (the diagnosis must survive the relay)", payload["state"], setupNoConfig)
	}
	if _, ok := payload["instance"]; !ok {
		t.Errorf("the instance stamp must survive the relay: %v", payload)
	}
}

// TestMCPRelay_UnixSocketHoldsNoKeyMaterial pins the credential-less local
// mode: the relay reaches a peer-cred unix-socket daemon with no bearer at
// all, and refuses a bearer that would be pointless key material.
func TestMCPRelay_UnixSocketHoldsNoKeyMaterial(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	sock := shortSocketPath(t)
	ln, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatalf("bind: %v", err)
	}
	s := newTestMCPServer(t, nil)
	handler := s.httpHandler(context.Background(), &mcpBindPosture{network: "unix"}, nil, newIdleWatchdog(0), discardLogger())
	srv := &http.Server{Handler: handler, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		_ = srv.Serve(&peerCredListener{Listener: ln, allowed: allowedPeerUIDSet(nil), logger: discardLogger()})
	}()
	t.Cleanup(func() { _ = srv.Close() })

	relay, err := newMCPRelay(&mcpRelayOptions{url: "unix://" + sock}, io.Discard, discardLogger())
	if err != nil {
		t.Fatalf("newMCPRelay: %v", err)
	}
	if relay.bearer != "" {
		t.Fatal("the unix-socket relay must hold no bearer")
	}
	cs := connectRelayedClient(t, relay)
	if _, err := cs.ListTools(context.Background(), nil); err != nil {
		t.Fatalf("tools/list over the peer-cred socket: %v", err)
	}

	t.Setenv(mcpBearerEnv, "tok_pointless")
	if _, err := newMCPRelay(&mcpRelayOptions{url: "unix://" + sock}, io.Discard, discardLogger()); err == nil {
		t.Error("a bearer against a unix socket target must be refused (nothing at rest)")
	}
}

func TestMCPRelay_RefusesPlaintextNonLoopback(t *testing.T) {
	if _, err := newMCPRelay(&mcpRelayOptions{url: "http://daemon.internal:8200"}, io.Discard, discardLogger()); err == nil ||
		!strings.Contains(err.Error(), "https") {
		t.Errorf("a plaintext non-loopback target must be refused with the https hint, got %v", err)
	}
}

// TestMCPRelay_ForwardsCallerBearer pins the remote-daemon posture: the
// caller-supplied short-lived bearer rides every request (Authorization),
// sourced from the env, never persisted anywhere by the relay.
func TestMCPRelay_ForwardsCallerBearer(t *testing.T) {
	var sawAuth atomic.Value
	daemon := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sawAuth.Store(r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":1,"result":{}}`))
	}))
	t.Cleanup(daemon.Close)

	t.Setenv(mcpBearerEnv, "at_shortlived")
	relay, err := newMCPRelay(&mcpRelayOptions{url: daemon.URL}, io.Discard, discardLogger())
	if err != nil {
		t.Fatalf("newMCPRelay: %v", err)
	}
	out := &lineRecorder{}
	relay.out = out
	relay.relayFrame(context.Background(), []byte(`{"jsonrpc":"2.0","id":1,"method":"ping"}`))

	if got := sawAuth.Load(); got != "Bearer at_shortlived" {
		t.Errorf("Authorization = %v, want the env bearer forwarded", got)
	}
	if len(out.lines()) != 1 || !strings.Contains(out.lines()[0], `"result"`) {
		t.Errorf("relayed frames = %v, want the daemon's response", out.lines())
	}
}

// TestMCPRelay_SynthesizesErrorForFailedRequest pins recovery: a daemon
// failure resolves the pending request with a JSON-RPC error (same id),
// never a hung call; a failed notification produces no frame.
func TestMCPRelay_SynthesizesErrorForFailedRequest(t *testing.T) {
	daemon := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "boom", http.StatusInternalServerError)
	}))
	t.Cleanup(daemon.Close)

	relay, err := newMCPRelay(&mcpRelayOptions{url: daemon.URL}, io.Discard, discardLogger())
	if err != nil {
		t.Fatalf("newMCPRelay: %v", err)
	}
	out := &lineRecorder{}
	relay.out = out

	relay.relayFrame(context.Background(), []byte(`{"jsonrpc":"2.0","id":7,"method":"tools/list"}`))
	lines := out.lines()
	if len(lines) != 1 {
		t.Fatalf("frames = %v, want one synthesized error", lines)
	}
	var resp struct {
		ID    json.RawMessage `json:"id"`
		Error *struct {
			Code    int    `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal([]byte(lines[0]), &resp); err != nil {
		t.Fatalf("synthesized frame is not JSON: %v", err)
	}
	if string(resp.ID) != "7" || resp.Error == nil || !strings.Contains(resp.Error.Message, "500") {
		t.Errorf("synthesized error = %s, want id 7 + the HTTP status", lines[0])
	}

	relay.relayFrame(context.Background(), []byte(`{"jsonrpc":"2.0","method":"notifications/initialized"}`))
	if len(out.lines()) != 1 {
		t.Errorf("a failed notification must produce no frame, got %v", out.lines())
	}
}

// TestMCPRelay_UnwrapsSSE pins the streamed-response arm: each SSE data
// event becomes one stdio frame.
func TestMCPRelay_UnwrapsSSE(t *testing.T) {
	daemon := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("event: message\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\"}\n\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}\n\n"))
	}))
	t.Cleanup(daemon.Close)

	relay, err := newMCPRelay(&mcpRelayOptions{url: daemon.URL}, io.Discard, discardLogger())
	if err != nil {
		t.Fatalf("newMCPRelay: %v", err)
	}
	out := &lineRecorder{}
	relay.out = out
	relay.relayFrame(context.Background(), []byte(`{"jsonrpc":"2.0","id":1,"method":"tools/call"}`))

	lines := out.lines()
	if len(lines) != 2 {
		t.Fatalf("frames = %v, want the two SSE events unwrapped", lines)
	}
	if !strings.Contains(lines[0], "notifications/progress") || !strings.Contains(lines[1], `"result"`) {
		t.Errorf("frames = %v, want progress then result", lines)
	}
}

// lineRecorder captures relayed stdout frames.
type lineRecorder struct {
	buf []string
}

func (r *lineRecorder) Write(b []byte) (int, error) {
	if s := strings.TrimSuffix(string(b), "\n"); s != "" {
		r.buf = append(r.buf, s)
	}
	return len(b), nil
}

func (r *lineRecorder) lines() []string { return r.buf }
