package api

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const heldBrokerBody = `{"job_id":"job_9","status":"held","approval":{"id":"exap_9",` +
	`"review_url":"https://jentic.example/app/agents/approvals/exap_9","expires_at":"2026-10-07T00:00:00Z"},` +
	`"agent_directive":"` + heldAgentDirective + `","_links":{"self":"https://jentic.example/jobs/job_9"}}`

// heldServers starts one httptest server answering as both the broker (any
// non-/jobs path → the held 202) and the control plane (/jobs/job_9 with the
// given status sequence, /jobs/job_9/result with a denial problem).
func heldServers(t *testing.T, statuses ...string) (*httptest.Server, *atomic.Int32, *atomic.Int32) {
	t.Helper()
	var executes, polls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/jobs/job_9":
			i := int(polls.Add(1)) - 1
			if i >= len(statuses) {
				i = len(statuses) - 1
			}
			_, _ = io.WriteString(w, `{"job_id":"job_9","kind":"execution","status":"`+statuses[i]+
				`","created_at":"2026-10-06T12:00:00Z","_links":{"self":"/jobs/job_9"}}`)
		case "/jobs/job_9/result":
			w.Header().Set("Content-Type", "application/problem+json")
			_, _ = io.WriteString(w, `{"type":"approval_denied","status":403}`)
		default:
			executes.Add(1)
			w.Header().Set("Jentic-Execution-Id", "exec_held")
			w.WriteHeader(http.StatusAccepted)
			_, _ = io.WriteString(w, heldBrokerBody)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, &executes, &polls
}

func heldTestServer(t *testing.T) *mcpServer {
	s := stampedTestMCPServer(t)
	s.heldWaitBudget = 50 * time.Millisecond
	s.heldWaitPoll = time.Millisecond
	return s
}

func executeRequestWithCaps(caps string) *mcp.CallToolRequest {
	req := callToolRequest("execute", `{"operation_id":"POST:/v1/charges","body":{"amount":5}}`)
	if caps != "" {
		var c map[string]any
		_ = json.Unmarshal([]byte(caps), &c)
		req.Params.Meta = mcp.Meta{mcp.MetaKeyClientCapabilities: c}
	}
	return req
}

func TestMCPExecuteHeld_HeldEnvelopeAtOnce(t *testing.T) {
	srv, executes, polls := heldServers(t, "held", "completed")
	s := heldTestServer(t)
	res, err := s.handleExecute(activeCtxWithBroker(srv.URL, srv.URL), executeRequestWithCaps(""))
	if err != nil || res.IsError {
		t.Fatalf("handleExecute: err=%v result=%s", err, toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	body, _ := payload["body"].(map[string]any)
	if payload["status"] != float64(http.StatusAccepted) || body["status"] != "held" {
		t.Fatalf("payload = %v, want the held envelope relayed", payload)
	}
	if body["agent_directive"] != heldAgentDirective {
		t.Errorf("directive = %v, want the broker's held directive verbatim", body["agent_directive"])
	}
	if approval, _ := body["approval"].(map[string]any); approval["review_url"] == nil {
		t.Errorf("approval = %v, want the review_url", body["approval"])
	}
	if executes.Load() != 1 || polls.Load() != 0 {
		t.Errorf("executes=%d polls=%d, want one execute and no job poll before the held result", executes.Load(), polls.Load())
	}
}

func TestMCPExecuteHeld_FormOnlyElicitationGetsTheHeldResult(t *testing.T) {
	srv, _, polls := heldServers(t, "held", "failed")
	s := heldTestServer(t)
	res, err := s.handleExecute(activeCtxWithBroker(srv.URL, srv.URL), executeRequestWithCaps(`{"elicitation":{}}`))
	if err != nil || res.IsError {
		t.Fatalf("handleExecute: err=%v", err)
	}
	body, _ := decodeToolJSON(t, res)["body"].(map[string]any)
	if body["status"] != "held" || polls.Load() != 0 {
		t.Fatalf("body = %v polls = %d, want the held envelope at once (form-only elicitation)", body, polls.Load())
	}
}

func TestMCPExecuteHeld_URLElicitationRoundTrip(t *testing.T) {
	srv, executes, _ := heldServers(t, "held")
	s := heldTestServer(t)
	ctx := activeCtxWithBroker(srv.URL, srv.URL)
	res, err := s.handleExecute(ctx, executeRequestWithCaps(`{"elicitation":{"url":{}}}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	ir, ok := res.InputRequests[reviewInputKey].(*mcp.ElicitParams)
	if !ok || ir.Mode != "url" || ir.URL != "https://jentic.example/app/agents/approvals/exap_9" {
		t.Fatalf("input requests = %#v, want one URL elicitation of the review page", res.InputRequests)
	}
	if !strings.Contains(ir.Message, "POST /v1/charges") || res.RequestState == "" {
		t.Errorf("message=%q state=%q, want the operation summary and a sealed state", ir.Message, res.RequestState)
	}

	retry := executeRequestWithCaps(`{"elicitation":{"url":{}}}`)
	retry.Params.RequestState = res.RequestState
	again, err := s.handleExecute(ctx, retry)
	if err != nil || again.IsError {
		t.Fatalf("retry: err=%v", err)
	}
	body, _ := decodeToolJSON(t, again)["body"].(map[string]any)
	if body["status"] != "held" || body["job_id"] != "job_9" {
		t.Errorf("retry body = %v, want the still-held envelope", body)
	}
	if executes.Load() != 1 {
		t.Errorf("executes = %d, want the retry to never re-send the call", executes.Load())
	}
}

func TestMCPExecuteHeld_ForgedRequestStateIsRefused(t *testing.T) {
	srv, executes, _ := heldServers(t, "held")
	s := heldTestServer(t)
	good, err := sealHeldState(heldState{JobID: "job_9", Agent: "someone-else@test", Exp: time.Now().Add(time.Minute).Unix()})
	if err != nil {
		t.Fatal(err)
	}
	for _, token := range []string{"garbage", good, good + "x"} {
		req := executeRequestWithCaps("")
		req.Params.RequestState = token
		if _, err := s.handleExecute(activeCtxWithBroker(srv.URL, srv.URL), req); err == nil {
			t.Errorf("token %q accepted, want invalid params", token)
		}
	}
	if executes.Load() != 0 {
		t.Errorf("executes = %d, want no broker call on a refused retry", executes.Load())
	}
}

func TestHeldFrontDoor(t *testing.T) {
	if heldFrontDoor(nil) != frontDoorHeldResult {
		t.Error("no capabilities must get the held result")
	}
	if heldFrontDoor(&mcp.ClientCapabilities{Elicitation: &mcp.ElicitationCapabilities{}}) != frontDoorHeldResult {
		t.Error("form-only elicitation must not qualify")
	}
	caps := &mcp.ClientCapabilities{Elicitation: &mcp.ElicitationCapabilities{URL: &mcp.URLElicitationCapabilities{}}}
	if heldFrontDoor(caps) != frontDoorURLElicitation {
		t.Error("elicitation.url must get the URL elicitation")
	}
}

func TestMCPGetExecutionResult_NoWaitPollsOnce(t *testing.T) {
	srv, _, polls := heldServers(t, "held", "completed")
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtxWithBroker(srv.URL, srv.URL),
		callToolRequest("get_execution_result", `{"job_id":"job_9"}`))
	if err != nil || res.IsError {
		t.Fatalf("get_execution_result: err=%v result=%s", err, toolResultText(res))
	}
	if payload := decodeToolJSON(t, res); payload["status"] != "held" || polls.Load() != 1 {
		t.Fatalf("status=%v polls=%d, want the current status from exactly one poll", payload["status"], polls.Load())
	}
}

func TestMCPGetExecutionResult_WaitReturnsEarlyOnceTerminal(t *testing.T) {
	srv, _, polls := heldServers(t, "held", "queued", "completed")
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtxWithBroker(srv.URL, srv.URL),
		callToolRequest("get_execution_result", `{"job_id":"job_9","wait_seconds":30}`))
	if err != nil || res.IsError {
		t.Fatalf("get_execution_result: err=%v result=%s", err, toolResultText(res))
	}
	if payload := decodeToolJSON(t, res); payload["status"] != "completed" || polls.Load() != 3 {
		t.Fatalf("status=%v polls=%d, want completed after three polls", payload["status"], polls.Load())
	}
}

func TestMCPGetExecutionResult_NegativeWaitAnswersAtOnce(t *testing.T) {
	srv, _, polls := heldServers(t, "held")
	s := heldTestServer(t)
	ctx := activeCtxWithBroker(srv.URL, srv.URL)
	res, err := s.handleGetExecutionResult(ctx,
		callToolRequest("get_execution_result", `{"job_id":"job_9","wait_seconds":-5}`))
	if err != nil || res.IsError || polls.Load() != 1 {
		t.Fatalf("negative wait: err=%v polls=%d, want one poll and an immediate answer", err, polls.Load())
	}
}
