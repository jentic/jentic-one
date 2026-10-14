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

const heldBrokerBody = `{"status":"held","held_execution_id":"exap_9",` +
	`"review_url":"https://jentic.example/app/agents/approvals/exap_9","expires_at":"2026-10-07T00:00:00Z",` +
	`"agent_directive":"` + heldAgentDirective + `","_links":{"self":"https://jentic.example/executions/holds/exap_9",` +
	`"result":"https://jentic.example/executions/holds/exap_9/result",` +
	`"withdraw":"https://jentic.example/executions/holds/exap_9:withdraw"}}`

// heldServers starts one httptest server answering as both the broker (any
// path outside /executions/holds and /jobs → the held 202) and the control
// plane: /executions/holds/exap_9 walks the given execution statuses (state
// "pending" while held, then "denied"), /executions/holds/exap_9/result
// answers a denial problem. /jobs is counted and refused — a held call is
// never read through it.
func heldServers(t *testing.T, statuses ...string) (*httptest.Server, *atomic.Int32, *atomic.Int32) {
	t.Helper()
	var executes, polls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/executions/holds/exap_9":
			i := int(polls.Add(1)) - 1
			if i >= len(statuses) {
				i = len(statuses) - 1
			}
			state := "denied"
			if statuses[i] == "held" {
				state = "pending"
			}
			_, _ = io.WriteString(w, `{"id":"exap_9","state":"`+state+`","execution_status":"`+statuses[i]+`",`+
				`"agent_id":"agt_1","api_vendor":"v","api_name":"n","api_version":"1","credential_id":"cred_1",`+
				`"method":"POST","path":"/v1/charges","expires_at":"2026-10-07T00:00:00Z",`+
				`"created_at":"2026-10-06T12:00:00Z","_links":{"self":"/executions/holds/exap_9"}}`)
		case r.URL.Path == "/executions/holds/exap_9/result":
			w.Header().Set("Content-Type", "application/problem+json")
			_, _ = io.WriteString(w, `{"type":"approval_denied","status":403,"held_execution":{"id":"exap_9","state":"denied"}}`)
		case strings.HasPrefix(r.URL.Path, "/jobs/"):
			t.Errorf("unexpected %s: a held call is never read through /jobs", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
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
	if body["review_url"] != "https://jentic.example/app/agents/approvals/exap_9" || body["held_execution_id"] != "exap_9" {
		t.Errorf("body = %v, want the held execution id and review_url", body)
	}
	if _, ok := body["job_id"]; ok {
		t.Errorf("body = %v, want no job_id", body)
	}
	if executes.Load() != 1 || polls.Load() != 0 {
		t.Errorf("executes=%d polls=%d, want one execute and no hold poll before the held result", executes.Load(), polls.Load())
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
	if body["status"] != "held" || body["held_execution_id"] != "exap_9" ||
		body["review_url"] != "https://jentic.example/app/agents/approvals/exap_9" ||
		body["expires_at"] != "2026-10-07T00:00:00Z" || body["agent_directive"] != heldAgentDirective {
		t.Errorf("retry body = %v, want the still-held envelope", body)
	}
	if links, _ := body["_links"].(map[string]any); links["result"] != "https://jentic.example/executions/holds/exap_9/result" {
		t.Errorf("retry _links = %v, want the broker's links", body["_links"])
	}
	if executes.Load() != 1 {
		t.Errorf("executes = %d, want the retry to never re-send the call", executes.Load())
	}
}

func TestMCPExecuteHeld_ForgedRequestStateIsRefused(t *testing.T) {
	srv, executes, _ := heldServers(t, "held")
	s := heldTestServer(t)
	good, err := sealHeldState(heldState{
		HeldExecutionID: "exap_9", Agent: "someone-else@test", ReviewURL: "https://jentic.example/r",
		Exp: time.Now().Add(time.Minute).Unix(),
	})
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

func TestMCPExecuteHeld_URLElicitationRetryReturnsTheFinishedResult(t *testing.T) {
	srv, executes, _ := heldServers(t, "failed")
	s := heldTestServer(t)
	ctx := activeCtxWithBroker(srv.URL, srv.URL)
	res, err := s.handleExecute(ctx, executeRequestWithCaps(`{"elicitation":{"url":{}}}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	retry := executeRequestWithCaps(`{"elicitation":{"url":{}}}`)
	retry.Params.RequestState = res.RequestState
	again, err := s.handleExecute(ctx, retry)
	if err != nil || again.IsError {
		t.Fatalf("retry: err=%v", err)
	}
	payload := decodeToolJSON(t, again)
	result, _ := payload["result"].(map[string]any)
	if payload["held_execution_id"] != "exap_9" || payload["state"] != "denied" || payload["status"] != "failed" ||
		result["type"] != "approval_denied" {
		t.Errorf("retry payload = %v, want the finished hold with its denial problem", payload)
	}
	if executes.Load() != 1 {
		t.Errorf("executes = %d, want the retry to never re-send the call", executes.Load())
	}
}

func TestMCPGetExecutionResult_NoWaitPollsOnce(t *testing.T) {
	srv, _, polls := heldServers(t, "held", "completed")
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtxWithBroker(srv.URL, srv.URL),
		callToolRequest("get_execution_result", `{"held_execution_id":"exap_9"}`))
	if err != nil || res.IsError {
		t.Fatalf("get_execution_result: err=%v result=%s", err, toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	if payload["status"] != "held" || payload["state"] != "pending" || polls.Load() != 1 {
		t.Fatalf("payload=%v polls=%d, want the current status from exactly one poll", payload, polls.Load())
	}
	if _, ok := payload["result"]; ok {
		t.Errorf("payload = %v, want no result while held", payload)
	}
}

func TestMCPGetExecutionResult_WaitReturnsEarlyOnceFinished(t *testing.T) {
	srv, _, polls := heldServers(t, "held", "queued", "failed")
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtxWithBroker(srv.URL, srv.URL),
		callToolRequest("get_execution_result", `{"held_execution":"exap_9","wait":30000}`))
	if err != nil || res.IsError {
		t.Fatalf("get_execution_result: err=%v result=%s", err, toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	result, _ := payload["result"].(map[string]any)
	if payload["status"] != "failed" || payload["held_execution_id"] != "exap_9" || result["type"] != "approval_denied" ||
		polls.Load() != 3 {
		t.Fatalf("payload=%v polls=%d, want the failed hold and its problem after three polls", payload, polls.Load())
	}
}

func TestMCPGetExecutionResult_NegativeWaitAnswersAtOnce(t *testing.T) {
	srv, _, polls := heldServers(t, "held")
	s := heldTestServer(t)
	ctx := activeCtxWithBroker(srv.URL, srv.URL)
	res, err := s.handleGetExecutionResult(ctx,
		callToolRequest("get_execution_result", `{"held_execution_id":"exap_9","wait":-5}`))
	if err != nil || res.IsError || polls.Load() != 1 {
		t.Fatalf("negative wait: err=%v polls=%d, want one poll and an immediate answer", err, polls.Load())
	}
}

func TestMCPGetExecutionResult_RequiresExactlyOneID(t *testing.T) {
	s := heldTestServer(t)
	for _, args := range []string{`{}`, `{"held_execution_id":"exap_9","job_id":"job_9"}`, `{"wait":1000}`} {
		if _, err := s.handleGetExecutionResult(activeCtx("http://127.0.0.1:1"),
			callToolRequest("get_execution_result", args)); err == nil ||
			!strings.Contains(err.Error(), "exactly one of") {
			t.Errorf("args %s: err = %v, want invalid params naming both ids", args, err)
		}
	}
}

func TestMCPGetExecutionResult_UnknownHeldExecutionIsResolveFailed(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/problem+json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"type":"held_execution_not_found","status":404}`)
	}))
	t.Cleanup(srv.Close)
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtx(srv.URL),
		callToolRequest("get_execution_result", `{"held_execution_id":"exap_gone"}`))
	if err != nil || !res.IsError {
		t.Fatalf("want a soft error, got err=%v", err)
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != "RESOLVE_FAILED" || payload["next_tool"] != "get_execution_result" {
		t.Errorf("payload = %v", payload)
	}
}

func TestMCPGetExecutionResult_JobWaitReadsTheJobRoutes(t *testing.T) {
	var polls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/jobs/job_9":
			status := "running"
			if polls.Add(1) >= 2 {
				status = "completed"
			}
			_, _ = io.WriteString(w, `{"job_id":"job_9","kind":"execution","status":"`+status+
				`","created_at":"2026-10-06T12:00:00Z","_links":{"self":"/jobs/job_9"}}`)
		case "/jobs/job_9/result":
			_, _ = io.WriteString(w, `{"ok":true}`)
		default:
			t.Errorf("unexpected %s: a job id reads only the /jobs routes", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtx(srv.URL),
		callToolRequest("get_execution_result", `{"job":"job_9","wait":30000}`))
	if err != nil || res.IsError {
		t.Fatalf("get_execution_result: err=%v result=%s", err, toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	result, _ := payload["result"].(map[string]any)
	if payload["job_id"] != "job_9" || payload["status"] != "completed" || result["ok"] != true || polls.Load() != 2 {
		t.Fatalf("payload=%v polls=%d, want the completed job and its result after two polls", payload, polls.Load())
	}
}
