package api

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

const explainedHeldBody = `{"id":"held_x","agent_id":"agnt_1","credential_id":"cred_1",` +
	`"api_vendor":"api.example.com","api_name":"payments","api_version":"1","method":"POST",` +
	`"path":"/v1/charges","state":"pending","execution_status":"held",` +
	`"expires_at":"2026-10-10T12:00:00Z","created_at":"2026-10-09T12:00:00Z",` +
	`"review_url":"https://console.example/app/agents/approvals/held_x",` +
	`"agent_reason":"Customer 4821 asked for a refund",` +
	`"_links":{"self":"https://console.example/executions/holds/held_x"}}`

// explainServer answers POST /executions/holds/{id}:explain with status and
// body, recording the reason it was sent and how many requests arrived.
func explainServer(t *testing.T, status int, body string) (*httptest.Server, *atomic.Value, *atomic.Int32) {
	t.Helper()
	var reason atomic.Value
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != http.MethodPost || !strings.HasSuffix(r.URL.Path, "/executions/holds/held_x:explain") {
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
		}
		raw, _ := io.ReadAll(r.Body)
		var req struct {
			Reason string `json:"reason"`
		}
		_ = json.Unmarshal(raw, &req)
		reason.Store(req.Reason)
		if status == http.StatusOK {
			w.Header().Set("Content-Type", "application/json")
		} else {
			w.Header().Set("Content-Type", "application/problem+json")
		}
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
	}))
	t.Cleanup(srv.Close)
	return srv, &reason, &calls
}

func TestMCPExplainHeldCall_RecordsTheReason(t *testing.T) {
	srv, reason, _ := explainServer(t, http.StatusOK, explainedHeldBody)
	s := heldTestServer(t)
	res, err := s.handleExplainHeldCall(activeCtx(srv.URL), callToolRequest("explain_held_call",
		`{"held_execution": "held_x", "reason": "Customer 4821 asked for a refund"}`))
	if err != nil || res.IsError {
		t.Fatalf("explain_held_call: err=%v isError=%v", err, res != nil && res.IsError)
	}
	if got := reason.Load(); got != "Customer 4821 asked for a refund" {
		t.Errorf("sent reason = %v", got)
	}
	payload := decodeToolJSON(t, res)
	for key, want := range map[string]any{
		"held_execution_id": "held_x",
		"state":             "pending",
		"status":            "held",
		"agent_reason":      "Customer 4821 asked for a refund",
		"review_url":        "https://console.example/app/agents/approvals/held_x",
		"agent_directive":   explainedAgentDirective,
	} {
		if payload[key] != want {
			t.Errorf("payload[%q] = %v, want %v", key, payload[key], want)
		}
	}
}

func TestMCPExplainHeldCall_Refusals(t *testing.T) {
	cases := []struct {
		name   string
		status int
		body   string
		want   string
	}{
		{"not found", http.StatusNotFound, `{"type":"held_execution_not_found","status":404}`, "not found"},
		{
			"already explained", http.StatusConflict,
			`{"type":"held_execution_already_explained","status":409,"detail":"already explained"}`, "already explained",
		},
		{
			"settled", http.StatusConflict,
			`{"type":"held_execution_already_settled","status":409,"detail":"already denied"}`, "no longer waiting",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv, _, _ := explainServer(t, tc.status, tc.body)
			s := heldTestServer(t)
			res, err := s.handleExplainHeldCall(activeCtx(srv.URL), callToolRequest("explain_held_call",
				`{"held_execution_id": "held_x", "reason": "why"}`))
			if err != nil || !res.IsError {
				t.Fatalf("want a soft error, got err=%v", err)
			}
			payload := decodeToolJSON(t, res)
			if payload["error_code"] != ux.CodeResolveFailed || payload["next_tool"] != "get_execution_result" {
				t.Errorf("payload = %v", payload)
			}
			if msg, _ := payload["error"].(string); !strings.Contains(msg, tc.want) {
				t.Errorf("error = %q, want it to mention %q", msg, tc.want)
			}
		})
	}
}

func TestMCPExplainHeldCall_ABlankReasonSendsNothing(t *testing.T) {
	srv, _, calls := explainServer(t, http.StatusOK, explainedHeldBody)
	s := heldTestServer(t)
	res, err := s.handleExplainHeldCall(activeCtx(srv.URL), callToolRequest("explain_held_call",
		`{"held_execution_id": "held_x", "reason": "   "}`))
	if err != nil || !res.IsError {
		t.Fatalf("want a soft error, got err=%v", err)
	}
	if payload := decodeToolJSON(t, res); payload["error_code"] != ux.CodeMissingArgument {
		t.Errorf("payload = %v", payload)
	}
	if calls.Load() != 0 {
		t.Errorf("a blank reason must not reach the control plane")
	}
}

func TestMCPExplainHeldCall_ANonStringReasonIsInvalidParams(t *testing.T) {
	srv, _, calls := explainServer(t, http.StatusOK, explainedHeldBody)
	s := heldTestServer(t)
	for _, reason := range []string{`123`, `4.5`, `true`, `["why"]`, `{"text":"why"}`} {
		_, err := s.handleExplainHeldCall(activeCtx(srv.URL), callToolRequest("explain_held_call",
			`{"held_execution_id": "held_x", "reason": `+reason+`}`))
		if err == nil || !strings.Contains(err.Error(), `parameter "reason": expected a string`) {
			t.Errorf("reason %s: err = %v, want an invalid-params error", reason, err)
		}
	}
	if calls.Load() != 0 {
		t.Errorf("a non-string reason must not reach the control plane")
	}
}

func TestMCPExplainHeldCall_RequiresTheHeldExecutionID(t *testing.T) {
	s := heldTestServer(t)
	if _, err := s.handleExplainHeldCall(activeCtx("http://127.0.0.1:1"),
		callToolRequest("explain_held_call", `{"reason": "why"}`)); err == nil {
		t.Fatal("want an invalid-params error without held_execution_id")
	}
}

func TestExecutionsExplain_SendsTheReason(t *testing.T) {
	withXDG(t)
	srv, reason, calls := explainServer(t, http.StatusOK, explainedHeldBody)
	setupContext(t, srv.URL)

	if _, err := runJenticCapture(t, "executions", "explain", "held_x", "Customer 4821 asked for a refund"); err != nil {
		t.Fatalf("executions explain: %v", err)
	}
	if calls.Load() != 1 {
		t.Errorf("requests = %d, want one POST", calls.Load())
	}
	if got := reason.Load(); got != "Customer 4821 asked for a refund" {
		t.Errorf("sent reason = %v", got)
	}
}

func TestExecutionsExplain_AlreadyExplainedIsResolveFailed(t *testing.T) {
	withXDG(t)
	srv, _, _ := explainServer(t, http.StatusConflict,
		`{"type":"held_execution_already_explained","status":409,"detail":"already explained"}`)
	setupContext(t, srv.URL)

	_, err := runJenticCapture(t, "executions", "explain", "held_x", "why")
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeResolveFailed || coded.ExitCode() != ux.ExitDenied {
		t.Fatalf("err = %v, want RESOLVE_FAILED (exit 2)", err)
	}
	if !strings.Contains(coded.Actionable, "jentic executions poll") {
		t.Errorf("actionable = %q, want the CLI's next step", coded.Actionable)
	}
}

func TestMCPExplainHeldCall_AnOverLongReasonIsRefusedLocally(t *testing.T) {
	srv, _, calls := explainServer(t, http.StatusOK, explainedHeldBody)
	s := heldTestServer(t)
	long := strings.Repeat("é", maxExplainReasonRunes+1)
	res, err := s.handleExplainHeldCall(activeCtx(srv.URL), callToolRequest("explain_held_call",
		`{"held_execution_id": "held_x", "reason": "`+long+`"}`))
	if err != nil || !res.IsError {
		t.Fatalf("want a soft error, got err=%v", err)
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeMissingArgument {
		t.Errorf("payload = %v", payload)
	}
	if msg, _ := payload["error"].(string); msg != "the reason was rejected: at most 4096 characters, got 4097" {
		t.Errorf("error = %q (same text as the Python lane)", msg)
	}
	if calls.Load() != 0 {
		t.Errorf("an over-long reason must not reach the control plane")
	}
}

// A blank reason is checked before any setup, so a machine with no context
// answers MISSING_ARGUMENT like the Python lane, not a config error.
func TestMCPExplainHeldCall_ABlankReasonWinsOverMissingSetup(t *testing.T) {
	withXDG(t)
	s := heldTestServer(t)
	res, err := s.handleExplainHeldCall(t.Context(), callToolRequest("explain_held_call",
		`{"held_execution_id": "held_x", "reason": ""}`))
	if err != nil || !res.IsError {
		t.Fatalf("want a soft error, got err=%v", err)
	}
	if payload := decodeToolJSON(t, res); payload["error_code"] != ux.CodeMissingArgument {
		t.Errorf("payload = %v", payload)
	}
}
