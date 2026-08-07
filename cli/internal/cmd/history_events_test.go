package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/client/generated/control"
)

func TestFilterFailures(t *testing.T) {
	ok := 200
	bad := 500
	recs := []control.ExecutionResponse{
		{ExecutionId: "a", HttpStatus: &ok},
		{ExecutionId: "b", HttpStatus: &bad},
		{ExecutionId: "c", HttpStatus: nil}, // nil status => not a failure
	}
	got := filterFailures(recs, false)
	if len(got) != 2 || got[0].ExecutionId != "a" || got[1].ExecutionId != "c" {
		t.Errorf("filterFailures(exclude) = %+v", ids(got))
	}
	if len(filterFailures(recs, true)) != 3 {
		t.Error("filterFailures(include) should keep everything")
	}
}

func ids(recs []control.ExecutionResponse) []string {
	out := make([]string, len(recs))
	for i, r := range recs {
		out[i] = r.ExecutionId
	}
	return out
}

func TestParseTimeWindow(t *testing.T) {
	f, to, err := parseTimeWindow("2026-08-01T00:00:00Z", "")
	if err != nil || f == nil || to != nil {
		t.Fatalf("from-only: f=%v to=%v err=%v", f, to, err)
	}
	if _, _, err := parseTimeWindow("not-a-time", ""); err == nil {
		t.Error("expected a parse error for a bad --from")
	}
}

// TestHistoryExport_WalksAndFilters drives `jentic history export` E2E against a
// mock control plane that returns two pages, one of which contains a failure. The
// full walk must follow the cursor and drop the failure by default.
func TestHistoryExport_WalksAndFilters(t *testing.T) {
	withXDG(t)

	page1 := control.ExecutionListResponse{
		Data:       []control.ExecutionResponse{{ExecutionId: "e1", TraceId: "T"}},
		HasMore:    true,
		NextCursor: strptr("c2"),
	}
	bad := 502
	page2 := control.ExecutionListResponse{
		Data: []control.ExecutionResponse{
			{ExecutionId: "e2", TraceId: "T"},
			{ExecutionId: "e3", TraceId: "T", HttpStatus: &bad},
		},
		HasMore: false,
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/executions") {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Query().Get("cursor") == "c2" {
			_ = json.NewEncoder(w).Encode(page2)
			return
		}
		_ = json.NewEncoder(w).Encode(page1)
	}))
	defer srv.Close()

	seedActiveContextTo(t, srv.URL)

	out := filepath.Join(t.TempDir(), "export.json")
	if err := runJentic(t, "history", "export", "--trace", "T", "-o", out); err != nil {
		t.Fatalf("history export: %v", err)
	}
	data, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	var got struct {
		TraceID string `json:"trace_id"`
		Count   int    `json:"count"`
		Items   []struct {
			ExecutionID string `json:"execution_id"`
		} `json:"items"`
	}
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("export not valid JSON: %v\n%s", err, data)
	}
	if got.TraceID != "T" || got.Count != 2 {
		t.Errorf("export = trace %q count %d (want T/2)\n%s", got.TraceID, got.Count, data)
	}
	for _, it := range got.Items {
		if it.ExecutionID == "e3" {
			t.Error("failed execution e3 should have been filtered")
		}
	}
}

func TestForwardSSE_EmitsNDJSONWithEventID(t *testing.T) {
	stream := strings.Join([]string{
		": keep-alive",
		"id: ev-1",
		`data: {"type":"execution.started","summary":"go"}`,
		"",
		`data: {"type":"execution.finished"}`,
		"",
	}, "\n")

	var buf bytes.Buffer
	if err := forwardSSE(context.Background(), strings.NewReader(stream), &buf); err != nil {
		t.Fatalf("forwardSSE: %v", err)
	}
	lines := strings.Split(strings.TrimSpace(buf.String()), "\n")
	if len(lines) != 2 {
		t.Fatalf("expected 2 NDJSON lines, got %d:\n%s", len(lines), buf.String())
	}
	var first map[string]any
	if err := json.Unmarshal([]byte(lines[0]), &first); err != nil {
		t.Fatalf("line 1 not JSON: %v", err)
	}
	if first["_event_id"] != "ev-1" || first["type"] != "execution.started" {
		t.Errorf("first event = %+v", first)
	}
}

func TestForwardSSE_MalformedPayloadForwardedAsRaw(t *testing.T) {
	stream := "data: not-json\n\n"
	var buf bytes.Buffer
	if err := forwardSSE(context.Background(), strings.NewReader(stream), &buf); err != nil {
		t.Fatal(err)
	}
	var obj map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(buf.String())), &obj); err != nil {
		t.Fatal(err)
	}
	if obj["raw"] != "not-json" {
		t.Errorf("malformed payload not preserved: %+v", obj)
	}
}

func strptr(s string) *string { return &s }

// seedActiveContextTo points the file-less path at baseURL with an injected
// bearer, so the SDK talks to the mock server with no key exchange or on-disk
// context. Loopback http is permitted by the transport guard. Mode is forced to
// agent so nothing tries to prompt.
func seedActiveContextTo(t *testing.T, baseURL string) {
	t.Helper()
	t.Setenv("JENTIC_BASE_URL", baseURL)
	t.Setenv("JENTIC_BEARER_TOKEN", "test-token")
	t.Setenv("JENTIC_MODE", "agent")
}
