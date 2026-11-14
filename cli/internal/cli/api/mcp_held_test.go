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

const heldBrokerBody = `{"status":"held","held_execution_id":"held_9",` +
	`"review_url":"https://jentic.example/app/agents/approvals/held_9","expires_at":"2026-10-07T00:00:00Z",` +
	`"agent_directive":"` + heldAgentDirective + `","_links":{"self":"https://jentic.example/executions/holds/held_9",` +
	`"result":"https://jentic.example/executions/holds/held_9/result",` +
	`"withdraw":"https://jentic.example/executions/holds/held_9:withdraw"}}`

const mcpReviewURL = "https://jentic.example/app/agents/approvals/held_9"

// heldServers starts one httptest server answering as both the broker (any
// path outside /executions/holds and /jobs → the held 202) and the control
// plane: /executions/holds/held_9 walks the given execution statuses (state
// "pending" while held, then "denied"), /executions/holds/held_9/result
// answers a denial problem. /jobs is counted and refused — a held call is
// never read through it.
type heldResponseOptions struct {
	upstreamStatus bool
	foreignReview  bool
}

func mcpReviewURLFor(srv *httptest.Server) string {
	return srv.URL + "/app/agents/approvals/held_9"
}

func heldServers(t *testing.T, statuses ...string) (*httptest.Server, *atomic.Int32, *atomic.Int32) {
	return heldServersWithResponse(t, heldResponseOptions{}, statuses...)
}

func heldServersWithResponse(t *testing.T, response heldResponseOptions, statuses ...string) (*httptest.Server, *atomic.Int32, *atomic.Int32) {
	t.Helper()
	var executes, polls atomic.Int32
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		reviewOrigin := srv.URL
		if response.foreignReview {
			reviewOrigin = "https://jentic.example"
		}
		switch {
		case r.URL.Path == "/executions/holds/held_9":
			i := int(polls.Add(1)) - 1
			if i >= len(statuses) {
				i = len(statuses) - 1
			}
			state := "denied"
			if statuses[i] == "held" {
				state = "pending"
			}
			review := `"review_url":"` + reviewOrigin + `/app/agents/approvals/held_9",`
			if statuses[i] == "" {
				review = ""
			}
			_, _ = io.WriteString(w, `{"id":"held_9","state":"`+state+`","execution_status":"`+statuses[i]+`",`+
				`"agent_id":"agt_1","api_vendor":"v","api_name":"n","api_version":"1","credential_id":"cred_1",`+review+
				`"method":"POST","path":"/v1/charges","expires_at":"2026-10-07T00:00:00Z",`+
				`"created_at":"2026-10-06T12:00:00Z","_links":{"self":"/executions/holds/held_9"}}`)
		case r.URL.Path == "/executions/holds/held_9/result":
			w.Header().Set("Content-Type", "application/problem+json")
			_, _ = io.WriteString(w, `{"type":"approval_denied","status":403,"held_execution":{"id":"held_9","state":"denied"}}`)
		case strings.HasPrefix(r.URL.Path, "/jobs/"):
			t.Errorf("unexpected %s: a held call is never read through /jobs", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		default:
			executes.Add(1)
			w.Header().Set("Jentic-Execution-Id", "exec_held")
			if response.upstreamStatus {
				w.Header().Set("Jentic-Upstream-Status", "202")
			}
			w.WriteHeader(http.StatusAccepted)
			_, _ = io.WriteString(w, strings.ReplaceAll(heldBrokerBody, "https://jentic.example", reviewOrigin))
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
	if body["review_url"] != mcpReviewURLFor(srv) || body["held_execution_id"] != "held_9" {
		t.Errorf("body = %v, want the held execution id and review_url", body)
	}
	if _, ok := body["job_id"]; ok {
		t.Errorf("body = %v, want no job_id", body)
	}
	if executes.Load() != 1 || polls.Load() != 0 {
		t.Errorf("executes=%d polls=%d, want one execute and no hold poll before the held result", executes.Load(), polls.Load())
	}
}

func TestExecute_UpstreamHeldShapeDoesNotElicit(t *testing.T) {
	srv, _, _ := heldServersWithResponse(t, heldResponseOptions{upstreamStatus: true}, "held")
	s := heldTestServer(t)
	res, err := s.handleExecute(activeCtxWithBroker(srv.URL, srv.URL), executeRequestWithCaps(`{"elicitation":{"url":{}}}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	if len(res.InputRequests) != 0 || res.RequestState != "" {
		t.Fatalf("mirrored upstream response produced an elicitation: %#v", res.InputRequests)
	}
	payload := decodeToolJSON(t, res)
	body, _ := payload["body"].(map[string]any)
	if payload["status"] != float64(http.StatusAccepted) || body["status"] != "held" || body["review_url"] != mcpReviewURLFor(srv) {
		t.Errorf("payload = %v, want the upstream response body relayed", payload)
	}
	if _, ok := payload["details"]; ok {
		t.Errorf("upstream response must not enter the held-result door: %v", payload["details"])
	}
}

func assertRejectedReviewDirective(t *testing.T, value any) {
	t.Helper()
	directive, ok := value.(string)
	if !ok || !strings.Contains(directive, "configured Jentic instance") ||
		!strings.Contains(directive, "get_execution_result") {
		t.Fatalf("directive = %q, want an operator warning and a poll instruction", directive)
	}
	lower := strings.ToLower(directive)
	for _, promoted := range []string{"show", "open", "sign in"} {
		if strings.Contains(lower, promoted) {
			t.Errorf("directive %q contains rejected approval-link promotion %q", directive, promoted)
		}
	}
}

func TestExecute_ForeignReviewURLNotElicited(t *testing.T) {
	srv, _, _ := heldServersWithResponse(t, heldResponseOptions{foreignReview: true}, "held")
	s := heldTestServer(t)
	res, err := s.handleExecute(activeCtxWithBroker(srv.URL, srv.URL), executeRequestWithCaps(`{"elicitation":{"url":{}}}`))
	if err != nil {
		t.Fatalf("handleExecute: %v", err)
	}
	if len(res.InputRequests) != 0 || res.RequestState != "" {
		t.Fatalf("foreign review URL produced an elicitation: %#v", res.InputRequests)
	}
	payload := decodeToolJSON(t, res)
	details, _ := payload["details"].(map[string]any)
	if trusted, ok := details["review_url_trusted"].(bool); !ok || trusted {
		t.Errorf("details = %v, want review_url_trusted=false", details)
	}
	body, _ := payload["body"].(map[string]any)
	assertRejectedReviewDirective(t, body["agent_directive"])
	assertRejectedReviewDirective(t, payload["actionable_step"])
}

func TestMCPExecuteHeld_TrustsEffectiveOrigins(t *testing.T) {
	for _, tc := range []struct {
		name      string
		baseURL   string
		brokerURL string
		reviewURL string
	}{
		{
			name:      "default loopback broker",
			baseURL:   "http://127.0.0.1:8000",
			reviewURL: "https://127.0.0.1:8100/app/agents/approvals/held_9",
		},
		{
			name:      "split control and broker",
			baseURL:   "https://control.example",
			brokerURL: "https://broker.example",
			reviewURL: "https://broker.example/app/agents/approvals/held_9",
		},
		{
			name:      "control origin survives broker resolution failure",
			baseURL:   "https://control.example",
			brokerURL: "://bad",
			reviewURL: "https://control.example/app/agents/approvals/held_9",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := heldTestServer(t)
			envelope := map[string]any{"held_execution_id": "held_9", "review_url": tc.reviewURL}
			res, err := s.answerHeld(activeCtxWithBroker(tc.baseURL, tc.brokerURL),
				executeRequestWithCaps(`{"elicitation":{"url":{}}}`), "test-agent@test", envelope,
				map[string]any{"body": envelope}, http.MethodPost, "/v1/charges")
			if err != nil {
				t.Fatalf("answerHeld: %v", err)
			}
			elicitation, ok := res.InputRequests[reviewInputKey].(*mcp.ElicitParams)
			if !ok || elicitation.URL != tc.reviewURL {
				t.Fatalf("input requests = %#v, want an elicitation of the effective configured origin", res.InputRequests)
			}
		})
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
	if !ok || ir.Mode != "url" || ir.URL != mcpReviewURLFor(srv) {
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
	if body["status"] != "held" || body["held_execution_id"] != "held_9" ||
		body["review_url"] != mcpReviewURLFor(srv) ||
		body["expires_at"] != "2026-10-07T00:00:00Z" || body["agent_directive"] != heldAgentDirective {
		t.Errorf("retry body = %v, want the still-held envelope", body)
	}
	if links, _ := body["_links"].(map[string]any); links["result"] != srv.URL+"/executions/holds/held_9/result" {
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
		HeldExecutionID: "held_9", Agent: "someone-else@test", ReviewURL: "https://jentic.example/r",
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

func TestHeldReviewMessage_StripsControlChars(t *testing.T) {
	message := heldReviewMessage("P\x1bOST", "/v1/\x1b[2J\rcharges\n")
	for _, r := range message {
		if r < 0x20 || r == 0x7f {
			t.Fatalf("message contains terminal control character %U: %q", r, message)
		}
	}
	if !strings.Contains(message, "POST /v1/[2Jcharges") {
		t.Errorf("message = %q, want controls stripped from method and path", message)
	}
}

func TestHeldReviewMessage_CapsPathAt200Runes(t *testing.T) {
	path := "/" + strings.Repeat("é", 250)
	wantPath := "/" + strings.Repeat("é", 199)
	message := heldReviewMessage("POST", path)
	if !strings.Contains(message, wantPath+", which needs human approval.") {
		t.Errorf("message = %q, want a 200-rune path", message)
	}
	if strings.Contains(message, path) {
		t.Errorf("message contains the unbounded path")
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
	if payload["held_execution_id"] != "held_9" || payload["state"] != "denied" || payload["status"] != "failed" ||
		result["type"] != "approval_denied" {
		t.Errorf("retry payload = %v, want the finished hold with its denial problem", payload)
	}
	if executes.Load() != 1 {
		t.Errorf("executes = %d, want the retry to never re-send the call", executes.Load())
	}
}

func TestMCPGetExecutionResult_ZeroTimeoutPollsOnce(t *testing.T) {
	srv, _, polls := heldServers(t, "held", "completed")
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtxWithBroker(srv.URL, srv.URL),
		callToolRequest("get_execution_result", `{"held_execution_id":"held_9","timeout":0}`))
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

func TestMCPGetExecutionResult_ForeignReviewURLHeldPollStaysRejected(t *testing.T) {
	srv, _, _ := heldServersWithResponse(t, heldResponseOptions{foreignReview: true}, "held")
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtxWithBroker(srv.URL, srv.URL),
		callToolRequest("get_execution_result", `{"held_execution_id":"held_9","timeout":0}`))
	if err != nil || res.IsError {
		t.Fatalf("get_execution_result: err=%v result=%s", err, toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	details, _ := payload["details"].(map[string]any)
	if trusted, ok := details["review_url_trusted"].(bool); !ok || trusted {
		t.Errorf("details = %v, want review_url_trusted=false", details)
	}
	if payload["review_url"] != mcpReviewURL {
		t.Errorf("review_url = %v, want the rejected URL retained as response data", payload["review_url"])
	}
	assertRejectedReviewDirective(t, payload["agent_directive"])
}

func TestMCPGetExecutionResult_HeldPayloadKeys(t *testing.T) {
	for _, tc := range []struct {
		status        string
		wantDirective bool
	}{
		{"held", true},
		{"queued", true},
		{"running", true},
		{"completed", false},
		{"failed", false},
		{"cancelled", false},
	} {
		t.Run(tc.status, func(t *testing.T) {
			srv, _, _ := heldServers(t, tc.status)
			s := heldTestServer(t)
			res, err := s.handleGetExecutionResult(activeCtxWithBroker(srv.URL, srv.URL),
				callToolRequest("get_execution_result", `{"held_execution_id":"held_9","timeout":0}`))
			if err != nil || res.IsError {
				t.Fatalf("get_execution_result: err=%v result=%s", err, toolResultText(res))
			}
			payload := decodeToolJSON(t, res)
			delete(payload, "instance")
			for _, k := range []string{"schema_version", "held_execution_id", "state", "status", "review_url", "expires_at"} {
				if _, ok := payload[k]; !ok {
					t.Errorf("payload = %v, missing %q", payload, k)
				}
			}
			if payload["review_url"] != mcpReviewURLFor(srv) || payload["expires_at"] != "2026-10-07T00:00:00Z" {
				t.Errorf("payload = %v", payload)
			}
			directive, ok := payload["agent_directive"]
			if ok != tc.wantDirective || (ok && directive != stillHeldAgentDirective) {
				t.Errorf("status %s: agent_directive = %v (present %v), want present=%v", tc.status, directive, ok, tc.wantDirective)
			}
			allowed := map[string]bool{
				"schema_version": true, "held_execution_id": true, "state": true, "status": true, "review_url": true,
				"expires_at": true, "execution_id": true, "result": true, "result_error": true, "truncated": true,
				"total_bytes": true, "agent_directive": true,
			}
			for k := range payload {
				if !allowed[k] {
					t.Errorf("unexpected payload key %q", k)
				}
			}
		})
	}
}

func TestMCPGetExecutionResult_NoReviewURLFromAnOlderServerIsOmitted(t *testing.T) {
	srv, _, _ := heldServers(t, "")
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtxWithBroker(srv.URL, srv.URL),
		callToolRequest("get_execution_result", `{"held_execution_id":"held_9","timeout":0}`))
	if err != nil || res.IsError {
		t.Fatalf("get_execution_result: err=%v result=%s", err, toolResultText(res))
	}
	if payload := decodeToolJSON(t, res); payload["review_url"] != nil {
		t.Errorf("payload = %v, want no review_url when the server sends none", payload)
	}
}

func TestMCPGetExecutionResult_TimeoutReturnsEarlyOnceFinished(t *testing.T) {
	srv, _, polls := heldServers(t, "held", "queued", "failed")
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtxWithBroker(srv.URL, srv.URL),
		callToolRequest("get_execution_result", `{"held_execution":"held_9","timeout":60}`))
	if err != nil || res.IsError {
		t.Fatalf("get_execution_result: err=%v result=%s", err, toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	result, _ := payload["result"].(map[string]any)
	if payload["status"] != "failed" || payload["held_execution_id"] != "held_9" || result["type"] != "approval_denied" ||
		polls.Load() != 3 {
		t.Fatalf("payload=%v polls=%d, want the failed hold and its problem after three polls", payload, polls.Load())
	}
	if _, ok := payload["agent_directive"]; ok {
		t.Errorf("payload = %v, want no agent_directive once finished", payload)
	}
}

func TestMCPGetExecutionResult_NegativeTimeoutAnswersAtOnce(t *testing.T) {
	srv, _, polls := heldServers(t, "held")
	s := heldTestServer(t)
	ctx := activeCtxWithBroker(srv.URL, srv.URL)
	res, err := s.handleGetExecutionResult(ctx,
		callToolRequest("get_execution_result", `{"held_execution_id":"held_9","timeout":-5}`))
	if err != nil || res.IsError || polls.Load() != 1 {
		t.Fatalf("negative timeout: err=%v polls=%d, want one poll and an immediate answer", err, polls.Load())
	}
	if payload := decodeToolJSON(t, res); payload["agent_directive"] != stillHeldAgentDirective {
		t.Errorf("payload = %v, want the still-held directive", payload)
	}
}

func TestPollTimeout(t *testing.T) {
	for _, tc := range []struct {
		args map[string]any
		want time.Duration
	}{
		{map[string]any{}, 30 * time.Second},
		{map[string]any{"timeout": 0}, 0},
		{map[string]any{"timeout": -3}, 0},
		{map[string]any{"timeout": 15}, 15 * time.Second},
		{map[string]any{"timeout": 30}, 30 * time.Second},
		{map[string]any{"timeout": 60}, 30 * time.Second},
		{map[string]any{"timeout": 600}, 30 * time.Second},
	} {
		if got := pollTimeout(tc.args); got != tc.want {
			t.Errorf("pollTimeout(%v) = %v, want %v", tc.args, got, tc.want)
		}
	}
}

func TestMCPGetExecutionResult_RequiresExactlyOneID(t *testing.T) {
	s := heldTestServer(t)
	for _, args := range []string{`{}`, `{"held_execution_id":"held_9","job_id":"job_9"}`, `{"timeout":10}`} {
		if _, err := s.handleGetExecutionResult(activeCtx("http://127.0.0.1:1"),
			callToolRequest("get_execution_result", args)); err == nil ||
			!strings.Contains(err.Error(), "exactly one of") {
			t.Errorf("args %s: err = %v, want invalid params naming both ids", args, err)
		}
	}
}

func TestMCPGetExecutionResult_TransportErrorKeepsTheHeldExecution(t *testing.T) {
	var polls atomic.Int32
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if polls.Add(1) > 1 {
			conn, _, _ := w.(http.Hijacker).Hijack()
			_ = conn.Close()
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"held_9","state":"pending","execution_status":"held",`+
			`"agent_id":"agt_1","api_vendor":"v","api_name":"n","api_version":"1","credential_id":"cred_1",`+
			`"review_url":"`+mcpReviewURLFor(srv)+`","method":"POST","path":"/v1/charges","expires_at":"2026-10-07T00:00:00Z",`+
			`"created_at":"2026-10-06T12:00:00Z","_links":{"self":"/executions/holds/held_9"}}`)
	}))
	t.Cleanup(srv.Close)
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtx(srv.URL),
		callToolRequest("get_execution_result", `{"held_execution_id":"held_9","timeout":5}`))
	if err != nil || !res.IsError {
		t.Fatalf("want a soft error, got err=%v result=%s", err, toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	details, _ := payload["details"].(map[string]any)
	if payload["error_code"] != "TRANSPORT_ERROR" || payload["next_tool"] != "get_execution_result" ||
		payload["retryable"] != true || details["held_execution_id"] != "held_9" || details["review_url"] != mcpReviewURLFor(srv) {
		t.Errorf("payload = %v", payload)
	}
	if _, ok := details["review_url_trusted"]; ok {
		t.Errorf("details = %v, want no untrusted marker for the configured origin", details)
	}
}

func TestMCPGetExecutionResult_ForeignReviewURLTransportErrorStaysRejected(t *testing.T) {
	var polls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if polls.Add(1) > 1 {
			conn, _, _ := w.(http.Hijacker).Hijack()
			_ = conn.Close()
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"held_9","state":"pending","execution_status":"held",`+
			`"agent_id":"agt_1","api_vendor":"v","api_name":"n","api_version":"1","credential_id":"cred_1",`+
			`"review_url":"`+mcpReviewURL+`","method":"POST","path":"/v1/charges","expires_at":"2026-10-07T00:00:00Z",`+
			`"created_at":"2026-10-06T12:00:00Z","_links":{"self":"/executions/holds/held_9"}}`)
	}))
	t.Cleanup(srv.Close)
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtx(srv.URL),
		callToolRequest("get_execution_result", `{"held_execution_id":"held_9","timeout":5}`))
	if err != nil || !res.IsError {
		t.Fatalf("want a soft error, got err=%v result=%s", err, toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	details, _ := payload["details"].(map[string]any)
	if trusted, ok := details["review_url_trusted"].(bool); !ok || trusted {
		t.Errorf("details = %v, want review_url_trusted=false", details)
	}
	assertRejectedReviewDirective(t, payload["actionable_step"])
}

func TestMCPGetExecutionResult_TransportErrorOnTheFirstReadNamesTheID(t *testing.T) {
	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(activeCtx("http://127.0.0.1:1"),
		callToolRequest("get_execution_result", `{"held_execution_id":"held_9","timeout":0}`))
	if err != nil || !res.IsError {
		t.Fatalf("want a soft error, got err=%v", err)
	}
	payload := decodeToolJSON(t, res)
	details, _ := payload["details"].(map[string]any)
	if details["held_execution_id"] != "held_9" {
		t.Errorf("payload = %v, want the held execution named", payload)
	}
	if _, ok := details["review_url"]; ok {
		t.Errorf("payload = %v, want no review_url before one was seen", payload)
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
		callToolRequest("get_execution_result", `{"held_execution_id":"held_gone"}`))
	if err != nil || !res.IsError {
		t.Fatalf("want a soft error, got err=%v", err)
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != "RESOLVE_FAILED" || payload["next_tool"] != "get_execution_result" {
		t.Errorf("payload = %v", payload)
	}
}

func TestMCPGetExecutionResult_JobTimeoutReadsTheJobRoutes(t *testing.T) {
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
		callToolRequest("get_execution_result", `{"job":"job_9","timeout":30}`))
	if err != nil || res.IsError {
		t.Fatalf("get_execution_result: err=%v result=%s", err, toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	result, _ := payload["result"].(map[string]any)
	if payload["job_id"] != "job_9" || payload["status"] != "completed" || result["ok"] != true || polls.Load() != 2 {
		t.Fatalf("payload=%v polls=%d, want the completed job and its result after two polls", payload, polls.Load())
	}
}
