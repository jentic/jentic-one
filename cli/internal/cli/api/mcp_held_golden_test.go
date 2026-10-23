package api

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// heldPollGolden is one frozen held-poll case shared with the mounted app
// (tests/unit/mcp/test_held_poll_golden.py replays the same files): the
// server-side view, the control-plane read the CLI receives for it (detail,
// and the /result document once the call finished), and the poll payload
// both surfaces must answer.
type heldPollGolden struct {
	Detail json.RawMessage `json:"detail"`
	Result *struct {
		ContentType string          `json:"content_type"`
		Body        json.RawMessage `json:"body"`
	} `json:"result"`
	Payload map[string]any `json:"payload"`
}

func heldPollGoldenDir() string {
	return filepath.Join("..", "..", "..", "tests", "golden", "testdata", "held_poll")
}

// TestMCPGetExecutionResult_HeldPollPayloadMatchesSharedGoldens pins the
// get_execution_result held payload to the frozen goldens the mounted app's
// held_execution_poll_payload is pinned to as well: a drift on either side
// fails against the identical file.
func TestMCPGetExecutionResult_HeldPollPayloadMatchesSharedGoldens(t *testing.T) {
	files, err := filepath.Glob(filepath.Join(heldPollGoldenDir(), "*.json"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no held-poll goldens under %s (err=%v)", heldPollGoldenDir(), err)
	}
	for _, file := range files {
		name := strings.TrimSuffix(filepath.Base(file), ".json")
		t.Run(name, func(t *testing.T) {
			raw, err := os.ReadFile(file)
			if err != nil {
				t.Fatalf("read golden: %v", err)
			}
			var g heldPollGolden
			if err := json.Unmarshal(raw, &g); err != nil {
				t.Fatalf("decode golden: %v", err)
			}
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/executions/holds/held_1":
					w.Header().Set("Content-Type", "application/json")
					_, _ = w.Write(g.Detail)
				case "/executions/holds/held_1/result":
					if g.Result == nil {
						t.Errorf("unexpected result read for %s", name)
						w.WriteHeader(http.StatusConflict)
						return
					}
					w.Header().Set("Content-Type", g.Result.ContentType)
					_, _ = w.Write(g.Result.Body)
				default:
					t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
					w.WriteHeader(http.StatusNotFound)
					_, _ = io.WriteString(w, "{}")
				}
			}))
			t.Cleanup(srv.Close)

			s := stampedTestMCPServer(t)
			res, err := s.handleGetExecutionResult(activeCtxWithBroker(srv.URL, srv.URL),
				callToolRequest("get_execution_result", `{"held_execution_id":"held_1","timeout":0}`))
			if err != nil || res.IsError {
				t.Fatalf("get_execution_result: err=%v result=%s", err, toolResultText(res))
			}
			got := decodeToolJSON(t, res)
			delete(got, "instance")
			if !reflect.DeepEqual(got, g.Payload) {
				gotJSON, _ := json.MarshalIndent(got, "", "  ")
				wantJSON, _ := json.MarshalIndent(g.Payload, "", "  ")
				t.Errorf("held poll payload diverged from the shared golden %s.\n--- want ---\n%s\n--- got ---\n%s",
					name, wantJSON, gotJSON)
			}
		})
	}
}
