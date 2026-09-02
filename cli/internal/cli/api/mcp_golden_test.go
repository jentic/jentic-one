package api

// mcp_golden_test.go pins the PR 1-D shared-goldens contract: the MCP execute
// result is a strict superset of the CLI envelope (§3.7.4 — five envelope keys
// plus the sibling `instance` stamp), so with `instance` stripped it must
// reproduce the SAME frozen bytes the tests/golden suite pinned for the CLI
// and the agentops core. The goldens are read in place from
// cli/tests/golden/testdata — one frozen source of truth, never a re-pin: a
// drift in either surface fails against the identical file. Only the cases
// where the tool result IS the envelope are shareable (success, upstream-4xx
// passthrough); denials deliberately diverge into the soft-error taxonomy
// (1-C) and stay covered by mcp_execute_test.go field assertions.

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
)

// sharedGoldenStdout reads the stdout section of one frozen CLI golden from
// the tests/golden suite's testdata — the exact envelope bytes the phase-0
// contract recorded (regenerate there with `go test ./tests/golden -update`).
func sharedGoldenStdout(t *testing.T, name string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "golden", "testdata", "golden", "v2", name+".txt"))
	if err != nil {
		t.Fatalf("read shared golden %s: %v", name, err)
	}
	_, afterStdout, ok := strings.Cut(string(raw), "--- stdout ---\n")
	if !ok {
		t.Fatalf("golden %s has no stdout marker", name)
	}
	stdout, _, ok := strings.Cut(afterStdout, "--- stderr ---\n")
	if !ok {
		t.Fatalf("golden %s has no stderr marker", name)
	}
	return stdout
}

// envelopeWithoutStamp re-serializes a decoded tool payload minus the sibling
// `instance` key through the same WriteJSON path the CLI envelope golden was
// recorded with (indent-2, sorted keys, redaction backstop), so the comparison
// is byte-exact like for like.
func envelopeWithoutStamp(t *testing.T, payload map[string]any) string {
	t.Helper()
	if _, ok := payload["instance"]; !ok {
		t.Fatalf("tool payload carries no instance stamp to strip: %v", payload)
	}
	delete(payload, "instance")
	var buf bytes.Buffer
	if err := cmdcore.WriteJSON(&buf, payload); err != nil {
		t.Fatalf("WriteJSON: %v", err)
	}
	return buf.String()
}

func TestMCPExecute_EnvelopeMatchesSharedGoldens(t *testing.T) {
	cases := []struct {
		name   string // the shared golden case in tests/golden/testdata/golden/v2
		target string
		// inspect answers /inspect when the target is an opaque id; nil for
		// broker-relative METHOD:path targets that skip resolution.
		inspect http.HandlerFunc
		broker  http.HandlerFunc
	}{
		{
			// The same mock responses TestGolden_ExecuteContract recorded
			// execute_ok_json from (Date suppressed for byte-stable headers).
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
			// An upstream 4xx relayed by the broker is a normal envelope on
			// both surfaces (§3.7 row 1) — the passthrough golden is shared too.
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
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
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
			t.Cleanup(srv.Close)

			s := stampedTestMCPServer(t)
			res, err := s.handleExecute(activeCtxWithBroker(srv.URL, srv.URL),
				callToolRequest("execute", `{"operation_id":"`+tc.target+`"}`))
			if err != nil {
				t.Fatalf("handleExecute: %v", err)
			}
			if res.IsError {
				t.Fatalf("unexpected soft error: %v", res.Content)
			}

			payload := decodeToolJSON(t, res)
			got := envelopeWithoutStamp(t, payload)
			want := sharedGoldenStdout(t, tc.name)
			if got != want {
				t.Errorf("MCP envelope diverged from the shared CLI golden %s.\n"+
					"The tool result must stay a strict superset of the CLI envelope (§3.7.4).\n--- want ---\n%s\n--- got ---\n%s",
					tc.name, want, got)
			}
		})
	}
}

// TestMCPExecute_PayloadIsEnvelopePlusStampOnly locks the superset shape
// itself: exactly the golden envelope's keys plus `instance`, nothing else —
// a new sibling key is a contract change that must consciously touch both the
// CLI golden and this pin.
func TestMCPExecute_PayloadIsEnvelopePlusStampOnly(t *testing.T) {
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Jentic-Execution-Id", "exec-123")
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	t.Cleanup(broker.Close)

	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(activeCtxWithBroker("http://127.0.0.1:8000", broker.URL),
		callToolRequest("execute", `{"operation_id":"GET:/v1/pets"}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	payload := decodeToolJSON(t, res)

	var goldenEnv map[string]any
	if err := json.Unmarshal([]byte(sharedGoldenStdout(t, "execute_ok_json")), &goldenEnv); err != nil {
		t.Fatalf("golden stdout is not JSON: %v", err)
	}
	want := map[string]bool{"instance": true}
	for key := range goldenEnv {
		want[key] = true
	}
	for key := range payload {
		if !want[key] {
			t.Errorf("payload carries %q — not an envelope key from the shared golden and not the instance stamp", key)
		}
	}
	for key := range want {
		if _, ok := payload[key]; !ok {
			t.Errorf("payload is missing %q", key)
		}
	}
}
