// Package mcpdaemon spawns the REAL binaries for the phase-3 item-9
// acceptance boxes that in-process tests cannot honestly cover: a live
// `jentic mcp --http` daemon process on a unix socket, the credential-less
// `jentic mcp --connect` relay process pumping its stdio, the golden
// transcripts replayed through both hops, idle-exit, and the non-loopback
// refusal — the daemon→relay→transcripts harness.
package mcpdaemon

import (
	"bytes"
	"context"
	"encoding/json"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var (
	buildOnce sync.Once
	builtBin  string
	errBuild  error
)

// TestMain removes the built binary's temp dir when the run ends —
// buildOnce outlives any single test's t.Cleanup, so without this every
// `go test` run leaked one dir with a ~30MB binary on CI runners.
func TestMain(m *testing.M) {
	code := m.Run()
	if builtBin != "" {
		_ = os.RemoveAll(filepath.Dir(builtBin))
	}
	os.Exit(code)
}

// jenticBin builds the real `jentic` binary once per test run.
func jenticBin(t *testing.T) string {
	t.Helper()
	buildOnce.Do(func() {
		dir, err := os.MkdirTemp("", "jentic-e2e")
		if err != nil {
			errBuild = err
			return
		}
		builtBin = filepath.Join(dir, "jentic")
		cmd := exec.Command("go", "build", "-o", builtBin, "../../cmd/jentic")
		cmd.Env = append(os.Environ(), "GOWORK=off")
		if out, err := cmd.CombinedOutput(); err != nil {
			errBuild = err
			t.Logf("go build output:\n%s", out)
		}
	})
	if errBuild != nil {
		t.Fatalf("building jentic: %v", errBuild)
	}
	return builtBin
}

// requireUnixSockets skips where the platform cannot serve the peer-cred
// unix-socket mode (the daemon refuses it there by design).
func requireUnixSockets(t *testing.T) {
	t.Helper()
	if runtime.GOOS != "linux" && runtime.GOOS != "darwin" {
		t.Skipf("peer-cred unix sockets are unsupported on %s", runtime.GOOS)
	}
}

// shortSocketPath keeps the path under the platform's ~104-byte limit.
func shortSocketPath(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("", "jmcp")
	if err != nil {
		t.Fatalf("mkdtemp: %v", err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	return filepath.Join(dir, "s.sock")
}

// hermeticEnv is the spawned processes' minimal environment: temp XDG trees,
// no inherited JENTIC_* state.
func hermeticEnv(t *testing.T) []string {
	t.Helper()
	return []string{
		"HOME=" + t.TempDir(),
		"PATH=" + os.Getenv("PATH"),
		"XDG_CONFIG_HOME=" + t.TempDir(),
		"XDG_STATE_HOME=" + t.TempDir(),
		"TERM=dumb",
	}
}

// startDaemon spawns `jentic mcp --http --socket …` pointed (file-lessly) at
// the given control/broker doubles and waits until the socket accepts.
func startDaemon(t *testing.T, bin, sock, baseURL, brokerURL string, extraArgs ...string) *exec.Cmd {
	t.Helper()
	args := append([]string{"mcp", "--http", "--socket", sock, "--log-file", filepath.Join(t.TempDir(), "d.log")}, extraArgs...)
	cmd := exec.Command(bin, args...)
	cmd.Env = append(hermeticEnv(t),
		"JENTIC_BASE_URL="+baseURL,
		"JENTIC_BEARER_TOKEN=tok_abc",
		"JENTIC_BROKER_URL="+brokerURL,
	)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		t.Fatalf("starting the daemon: %v", err)
	}
	t.Cleanup(func() {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	})

	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		if conn, err := net.DialTimeout("unix", sock, time.Second); err == nil {
			_ = conn.Close()
			return cmd
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("the daemon never came up on %s; stderr:\n%s", sock, stderr.String())
	return nil
}

// connectViaRelay spawns the credential-less relay process against the
// daemon's socket and returns a live SDK client session over its stdio. The
// relay's environment carries NO token of any kind — that absence IS the
// acceptance criterion.
func connectViaRelay(t *testing.T, bin, sock string) *mcp.ClientSession {
	t.Helper()
	relay := exec.Command(bin, "mcp", "--connect", "unix://"+sock, "--log-file", filepath.Join(t.TempDir(), "r.log"))
	relay.Env = hermeticEnv(t)
	for _, kv := range relay.Env {
		if strings.Contains(kv, "TOKEN") || strings.Contains(kv, "BEARER") {
			t.Fatalf("the relay environment must hold no key material, found %q", kv)
		}
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)
	client := mcp.NewClient(&mcp.Implementation{Name: "jentic-e2e-client", Version: "1.0"}, nil)
	cs, err := client.Connect(ctx, &mcp.CommandTransport{Command: relay}, nil)
	if err != nil {
		t.Fatalf("connecting through the relay process: %v", err)
	}
	t.Cleanup(func() { _ = cs.Close() })
	return cs
}

// sharedGoldenStdout mirrors the reader in mcp_golden_test.go / the Python
// twin: the frozen CLI envelope bytes from tests/golden/testdata.
func sharedGoldenStdout(t *testing.T, name string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "golden", "testdata", "golden", "v2", name+".txt"))
	if err != nil {
		t.Fatalf("read shared golden %s: %v", name, err)
	}
	_, after, ok := strings.Cut(string(raw), "--- stdout ---\n")
	if !ok {
		t.Fatalf("golden %s has no stdout marker", name)
	}
	stdout, _, ok := strings.Cut(after, "--- stderr ---\n")
	if !ok {
		t.Fatalf("golden %s has no stderr marker", name)
	}
	return stdout
}

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
