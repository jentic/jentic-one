package mcpdaemon

// daemon_e2e_test.go — the item-9 acceptance boxes over real processes.

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"os/exec"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
)

// TestRelayPumpsGoldenTranscriptsThroughLiveDaemon is the acceptance harness:
// spawn daemon → spawn relay → replay the stdio contract. The relay process
// holds no key material; the daemon (holding the context keys via the
// file-less env) produces byte-identical golden envelopes through both hops.
func TestRelayPumpsGoldenTranscriptsThroughLiveDaemon(t *testing.T) {
	requireUnixSockets(t)
	bin := jenticBin(t)

	plane := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header()["Date"] = nil
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/inspect":
			_, _ = w.Write([]byte(`{"method":"GET","url":"https://upstream.example/v1/pets"}`))
		case "/instance":
			_, _ = w.Write([]byte(`{"backend":"local","host":"127.0.0.1:8000","instance_id":"digest-1"}`))
		default:
			w.Header().Set("Jentic-Execution-Id", "exec-123")
			_, _ = w.Write([]byte(`[{"id":1,"name":"Fido"}]`))
		}
	}))
	t.Cleanup(plane.Close)

	sock := shortSocketPath(t)
	startDaemon(t, bin, sock, plane.URL, plane.URL)
	cs := connectViaRelay(t, bin, sock)
	ctx := context.Background()

	// tools/list: the full stdio surface, through both hops.
	tools, err := cs.ListTools(ctx, nil)
	if err != nil {
		t.Fatalf("tools/list through daemon+relay: %v", err)
	}
	names := map[string]bool{}
	for _, tool := range tools.Tools {
		names[tool.Name] = true
	}
	for _, want := range []string{"get_started", "whoami", "search_apis", "inspect_operation", "execute", "execute_read", "get_execution_result", "search_catalog", "import_api", "request_access"} {
		if !names[want] {
			t.Errorf("tools/list is missing %q through the relay", want)
		}
	}

	// The shared golden execute transcript, byte-identical minus the stamp.
	res, err := cs.CallTool(ctx, &mcp.CallToolParams{
		Name:      "execute",
		Arguments: map[string]any{"operation_id": "listPets"},
	})
	if err != nil {
		t.Fatalf("execute through daemon+relay: %v", err)
	}
	if res.IsError {
		t.Fatalf("execute soft-errored: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if _, ok := payload["instance"]; !ok {
		t.Fatalf("payload carries no instance stamp: %v", payload)
	}
	delete(payload, "instance")
	var buf bytes.Buffer
	if err := cmdcore.WriteJSON(&buf, payload); err != nil {
		t.Fatalf("WriteJSON: %v", err)
	}
	if got, want := buf.String(), sharedGoldenStdout(t, "execute_ok_json"); got != want {
		t.Errorf("daemon+relay envelope diverged from the shared CLI golden.\n--- want ---\n%s\n--- got ---\n%s", want, got)
	}
}

// TestDaemonIdleExitFires pins the socket-activation half the daemon owns:
// with a short --idle-timeout and no traffic, the process exits cleanly.
func TestDaemonIdleExitFires(t *testing.T) {
	requireUnixSockets(t)
	bin := jenticBin(t)

	sock := shortSocketPath(t)
	cmd := startDaemon(t, bin, sock, "http://127.0.0.1:1", "http://127.0.0.1:1", "--idle-timeout", "300ms")

	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case err := <-done:
		if err != nil {
			t.Errorf("idle-exit must be a clean exit, got %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("the daemon never idle-exited")
	}
}

// TestDaemonRefusesNonLoopbackWithoutTLSAndToken pins the acceptance box at
// the process level: the bind is refused before anything listens.
func TestDaemonRefusesNonLoopbackWithoutTLSAndToken(t *testing.T) {
	bin := jenticBin(t)

	cmd := exec.Command(bin, "mcp", "--http", "--listen", "0.0.0.0:0")
	cmd.Env = hermeticEnv(t)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err == nil {
		t.Fatal("a non-loopback bind without TLS+token must refuse to serve")
	}
	if out := stderr.String(); !bytes.Contains([]byte(out), []byte("allow-non-loopback")) {
		t.Errorf("the refusal must name the missing opt-in, got:\n%s", out)
	}
}
