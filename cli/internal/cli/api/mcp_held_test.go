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
	`"review_url":"https://jentic.example/app/approvals/exap_9","expires_at":"2026-10-07T00:00:00Z"},` +
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

func TestMCPExecuteHeld_ShortWaitThenHeldEnvelope(t *testing.T) {
	srv, executes, polls := heldServers(t, "held")
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
	if executes.Load() != 1 || polls.Load() < 1 {
		t.Errorf("executes=%d polls=%d, want one execute and the short-wait polls", executes.Load(), polls.Load())
	}
}

func TestMCPExecuteHeld_ShortWaitReturnsTheDecision(t *testing.T) {
	srv, _, _ := heldServers(t, "held", "failed")
	s := heldTestServer(t)
	s.heldWaitBudget = time.Second
	res, err := s.handleExecute(activeCtxWithBroker(srv.URL, srv.URL), executeRequestWithCaps(`{"elicitation":{}}`))
	if err != nil || res.IsError {
		t.Fatalf("handleExecute: err=%v", err)
	}
	payload := decodeToolJSON(t, res)
	if payload["status"] != "failed" {
		t.Fatalf("payload = %v, want the terminal job poll (form-only elicitation gets the short wait)", payload)
	}
	result, _ := payload["result"].(map[string]any)
	if result["type"] != "approval_denied" {
		t.Errorf("result = %v, want the failed job's problem body", payload["result"])
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
	if !ok || ir.Mode != "url" || ir.URL != "https://jentic.example/app/approvals/exap_9" {
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
	if heldFrontDoor(nil) != frontDoorShortWait {
		t.Error("no capabilities must get the short wait")
	}
	if heldFrontDoor(&mcp.ClientCapabilities{Elicitation: &mcp.ElicitationCapabilities{}}) != frontDoorShortWait {
		t.Error("form-only elicitation must not qualify")
	}
	caps := &mcp.ClientCapabilities{Elicitation: &mcp.ElicitationCapabilities{URL: &mcp.URLElicitationCapabilities{}}}
	if heldFrontDoor(caps) != frontDoorURLElicitation {
		t.Error("elicitation.url must get the URL elicitation")
	}
}
