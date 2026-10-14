package api

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

const heldBody = `{
	"status": "held",
	"held_execution_id": "exap_held1",
	"review_url": "http://ui.local/app/agents/approvals/exap_held1",
	"expires_at": "2026-10-10T12:00:00Z",
	"agent_directive": "This call needs human approval.",
	"_links": {
		"self": "http://ui.local/executions/holds/exap_held1",
		"result": "http://ui.local/executions/holds/exap_held1/result",
		"withdraw": "http://ui.local/executions/holds/exap_held1:withdraw"
	}
}`

// heldStack is one httptest server playing the broker (every path outside
// /executions/holds answers the held 202) and the control plane's holds
// routes: GET /executions/holds/{id} reports state "pending" and execution
// status "held" for the first heldPolls polls, then state/status; GET
// /executions/holds/{id}/result answers result (with resultType), or
// resultStatus when non-zero, or 409 when empty.
type heldStack struct {
	heldPolls    int32
	state        string
	status       string
	result       string
	resultType   string
	resultStatus int
	holdStatus   int // non-zero: GET /executions/holds/{id} answers this status instead
	brokerHits   atomic.Int32
	polls        atomic.Int32
	jobsHits     atomic.Int32
}

func (s *heldStack) serve(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/executions/holds/exap_held1/result":
			if s.resultStatus != 0 || s.result == "" {
				status := s.resultStatus
				if status == 0 {
					status = http.StatusConflict
				}
				w.Header().Set("Content-Type", "application/problem+json")
				w.WriteHeader(status)
				_, _ = w.Write([]byte(`{"type":"held_execution_result_not_ready","status":` + strconv.Itoa(status) + `}`))
				return
			}
			w.Header().Set("Content-Type", s.resultType)
			_, _ = w.Write([]byte(s.result))
		case strings.HasPrefix(r.URL.Path, "/executions/holds/"):
			if s.holdStatus != 0 {
				w.Header().Set("Content-Type", "application/problem+json")
				w.WriteHeader(s.holdStatus)
				_, _ = w.Write([]byte(`{"type":"held_execution_not_found","status":404}`))
				return
			}
			state, status := s.state, s.status
			if s.polls.Add(1) <= s.heldPolls {
				state, status = "pending", "held"
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id": "exap_held1", "state": state, "execution_status": status,
				"agent_id": "agt_1", "api_vendor": "example.com", "api_name": "orders", "api_version": "1",
				"credential_id": "cred_1", "method": "POST", "path": "/v1/orders",
				"execution_id": "exec_9", "expires_at": "2026-10-10T12:00:00Z",
				"created_at": "2026-10-09T12:00:00Z",
				"_links":     map[string]any{"self": "/executions/holds/exap_held1"},
			})
		case strings.HasPrefix(r.URL.Path, "/jobs/"):
			s.jobsHits.Add(1)
			w.WriteHeader(http.StatusNotFound)
		default:
			s.brokerHits.Add(1)
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Preference-Applied", "respond-async")
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(heldBody))
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

func runHeld(t *testing.T, srv *httptest.Server, args ...string) (stdout, stderr string, err error) {
	t.Helper()
	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)
	out, errBuf := new(bytes.Buffer), new(bytes.Buffer)
	app.Out, app.Err = out, errBuf
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(errBuf)
	if args[0] == "execute" {
		args = append(args, "--broker-scheme", "http", "--broker-host", srv.Listener.Addr().String())
	}
	root.SetArgs(args)
	err = root.Execute()
	return out.String(), errBuf.String(), err
}

func exitOf(t *testing.T, err error) (int, *ux.CodedError) {
	t.Helper()
	if err == nil {
		return 0, nil
	}
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("want a coded error, got %v", err)
	}
	return coded.ExitCode(), coded
}

func envelopeOf(t *testing.T, stdout string) map[string]any {
	t.Helper()
	var env map[string]any
	if err := json.Unmarshal([]byte(stdout), &env); err != nil {
		t.Fatalf("stdout is not one JSON envelope: %v\n%s", err, stdout)
	}
	return env
}

const reviewURL = "http://ui.local/app/agents/approvals/exap_held1"

func TestExecuteHeldExits3WithTheReviewLink(t *testing.T) {
	s := &heldStack{}
	srv := s.serve(t)
	stdout, stderr, err := runHeld(t, srv, "execute", "POST:/v1/orders", "-d", `{"n":1}`, "--json")

	code, coded := exitOf(t, err)
	if code != ux.ExitTimeoutPending || coded.Code != ux.CodeTimeoutPending {
		t.Fatalf("exit %d code %v, want 3 TIMEOUT_PENDING", code, coded)
	}
	if coded.Details["held_execution_id"] != "exap_held1" || coded.Details["review_url"] != reviewURL {
		t.Errorf("details = %v", coded.Details)
	}
	if _, ok := coded.Details["job_id"]; ok {
		t.Errorf("details = %v, want no job_id", coded.Details)
	}
	if !strings.Contains(coded.Msg, "held execution exap_held1") ||
		!strings.Contains(coded.Actionable, "jentic holds wait exap_held1") {
		t.Errorf("msg %q / actionable %q do not name the held execution and holds wait", coded.Msg, coded.Actionable)
	}
	env := envelopeOf(t, stdout)
	body, _ := env["body"].(map[string]any)
	if env["status"] != float64(202) || body["status"] != "held" || body["held_execution_id"] != "exap_held1" {
		t.Errorf("stdout envelope = %v", env)
	}
	for _, want := range []string{"needs human approval", reviewURL, "jentic holds wait exap_held1"} {
		if !strings.Contains(stderr, want) {
			t.Errorf("stderr missing %q:\n%s", want, stderr)
		}
	}
	if s.polls.Load() != 0 {
		t.Errorf("without --wait the hold is never polled (polled %d times)", s.polls.Load())
	}
}

func TestExecuteWaitPrintsTheApprovedRunLikeASyncCall(t *testing.T) {
	s := &heldStack{
		heldPolls:  2,
		state:      "approved",
		status:     "completed",
		resultType: "application/json",
		result: `{"execution_id":"exec_9","status":"completed","http_status":201,"duration_ms":5,"body_b64":"` +
			base64.StdEncoding.EncodeToString([]byte(`{"order":7}`)) + `"}`,
	}
	srv := s.serve(t)
	stdout, stderr, err := runHeld(t, srv, "execute", "POST:/v1/orders", "-d", `{"n":1}`, "--wait", "--json")
	if code, coded := exitOf(t, err); code != 0 {
		t.Fatalf("exit %d (%v), want 0\nstderr: %s", code, coded, stderr)
	}
	env := envelopeOf(t, stdout)
	body, _ := env["body"].(map[string]any)
	if env["status"] != float64(201) || body["order"] != float64(7) || env["execution_id"] != "exec_9" {
		t.Errorf("envelope = %v", env)
	}
	headers, _ := env["headers"].(map[string]any)
	if headers["Content-Type"] != "application/json" {
		t.Errorf("headers = %v", headers)
	}
	if s.brokerHits.Load() != 1 {
		t.Errorf("the call was sent %d times; --wait never re-sends it", s.brokerHits.Load())
	}
	if s.polls.Load() != 3 || s.jobsHits.Load() != 0 {
		t.Errorf("polls=%d jobs=%d, want three hold polls and no /jobs read", s.polls.Load(), s.jobsHits.Load())
	}
	if !strings.Contains(stderr, "waiting for the decision") {
		t.Errorf("stderr = %s", stderr)
	}
}

func TestExecuteWaitDeniedExits2WithTheReason(t *testing.T) {
	s := &heldStack{
		state:      "denied",
		status:     "failed",
		resultType: "application/problem+json",
		result: `{"type":"approval_denied","title":"Execution approval denied","status":403,` +
			`"detail":"A reviewer denied this held execution: not today","error_origin":"broker",` +
			`"held_execution":{"id":"exap_held1","state":"denied"}}`,
	}
	srv := s.serve(t)
	stdout, stderr, err := runHeld(t, srv, "execute", "POST:/v1/orders", "--wait", "--json")
	code, coded := exitOf(t, err)
	if code != ux.ExitDenied || coded.Code != ux.CodeBrokerDenied || coded.Details["problem_type"] != "approval_denied" {
		t.Fatalf("exit %d code %v, want 2 BROKER_DENIED approval_denied", code, coded)
	}
	env := envelopeOf(t, stdout)
	body, _ := env["body"].(map[string]any)
	if env["status"] != float64(403) || body["type"] != "approval_denied" {
		t.Errorf("envelope = %v", env)
	}
	if !strings.Contains(stderr, "Denied by a reviewer") || !strings.Contains(stderr, "not today") {
		t.Errorf("stderr = %s", stderr)
	}
	if strings.Contains(stderr, "credential binding") {
		t.Errorf("an approval denial must not get the binding hint:\n%s", stderr)
	}
}

func TestExecuteWaitTimeoutExits3WithTheHeldEnvelope(t *testing.T) {
	s := &heldStack{heldPolls: 1 << 20}
	srv := s.serve(t)
	stdout, _, err := runHeld(t, srv, "execute", "GET:/v1/items", "--wait", "--timeout", "30ms", "--json")
	code, coded := exitOf(t, err)
	if code != ux.ExitTimeoutPending || !strings.Contains(coded.Msg, "after waiting 30ms") {
		t.Fatalf("exit %d code %v", code, coded)
	}
	if env := envelopeOf(t, stdout); env["status"] != float64(202) {
		t.Errorf("envelope = %v", env)
	}
	if s.polls.Load() == 0 {
		t.Error("--wait never polled the hold")
	}
}

func TestExecuteWaitRejectsANonPositiveTimeout(t *testing.T) {
	s := &heldStack{}
	srv := s.serve(t)
	_, _, err := runHeld(t, srv, "execute", "GET:/v1/items", "--wait", "--timeout", "0s")
	if _, coded := exitOf(t, err); coded == nil || coded.Code != ux.CodeMissingArgument {
		t.Fatalf("want MISSING_ARGUMENT, got %v", err)
	}
	if s.brokerHits.Load() != 0 {
		t.Error("a bad --timeout must fail before the call is sent")
	}
}

func TestHoldsWait(t *testing.T) {
	upstream404 := `{"execution_id":"exec_9","status":"failed","http_status":404,"duration_ms":3,"body_b64":"` +
		base64.StdEncoding.EncodeToString([]byte(`{"error":"no such order"}`)) + `"}`
	cases := []struct {
		name     string
		stack    *heldStack
		wantExit int
		wantCode string
		check    func(t *testing.T, stdout, stderr string)
	}{
		{
			name:     "an upstream 4xx is data (exit 0)",
			stack:    &heldStack{state: "approved", status: "completed", resultType: "application/json", result: upstream404},
			wantExit: 0,
			check: func(t *testing.T, stdout, _ string) {
				env := envelopeOf(t, stdout)
				headers, _ := env["headers"].(map[string]any)
				if env["status"] != float64(404) || headers["Jentic-Error-Origin"] != "upstream" {
					t.Errorf("envelope = %v", env)
				}
			},
		},
		{
			name: "a run-time denial exits 2",
			stack: &heldStack{state: "approved", status: "failed", resultType: "application/json", result: `{"execution_id":"exec_9",` +
				`"status":"failed","http_status":403,"duration_ms":0,"problem":{"type":"action_denied",` +
				`"title":"Denied","status":403,"error_origin":"broker"}}`},
			wantExit: ux.ExitDenied,
			wantCode: ux.CodeBrokerDenied,
		},
		{
			name: "an expired hold exits 2",
			stack: &heldStack{state: "expired", status: "failed", resultType: "application/problem+json", result: `{"type":"approval_expired",` +
				`"title":"Execution approval expired","status":403,"error_origin":"broker",` +
				`"held_execution":{"id":"exap_held1","state":"expired"}}`},
			wantExit: ux.ExitDenied,
			wantCode: ux.CodeBrokerDenied,
			check: func(t *testing.T, _, stderr string) {
				if !strings.Contains(stderr, "Expired") {
					t.Errorf("stderr = %s", stderr)
				}
			},
		},
		{
			name:     "a withdrawn hold exits 2",
			stack:    &heldStack{state: "withdrawn", status: "cancelled"},
			wantExit: ux.ExitDenied,
			wantCode: ux.CodeBrokerDenied,
		},
		{
			name:     "a run with no upstream answer exits 1",
			stack:    &heldStack{state: "approved", status: "failed"},
			wantExit: ux.ExitError,
			wantCode: ux.CodeTransportError,
		},
		{
			name:     "a result past retention exits 2",
			stack:    &heldStack{state: "approved", status: "completed", resultStatus: http.StatusGone},
			wantExit: ux.ExitDenied,
			wantCode: ux.CodeResolveFailed,
		},
		{
			name:     "an unknown held execution id exits 2",
			stack:    &heldStack{holdStatus: http.StatusNotFound},
			wantExit: ux.ExitDenied,
			wantCode: ux.CodeResolveFailed,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := tc.stack
			srv := s.serve(t)
			stdout, stderr, err := runHeld(t, srv, "holds", "wait", "exap_held1", "--json")
			code, coded := exitOf(t, err)
			if code != tc.wantExit || (tc.wantCode != "" && coded.Code != tc.wantCode) {
				t.Fatalf("exit %d code %v, want %d %s\nstderr: %s", code, coded, tc.wantExit, tc.wantCode, stderr)
			}
			if s.brokerHits.Load() != 0 || s.jobsHits.Load() != 0 {
				t.Error("holds wait never sends a call and never reads /jobs")
			}
			if tc.check != nil {
				tc.check(t, stdout, stderr)
			}
		})
	}
}

func TestHoldsWaitTimeoutOnAPendingHoldExits3(t *testing.T) {
	s := &heldStack{heldPolls: 1 << 20}
	srv := s.serve(t)
	_, _, err := runHeld(t, srv, "holds", "wait", "exap_held1", "--timeout", "20ms")
	code, coded := exitOf(t, err)
	if code != ux.ExitTimeoutPending || coded.Details["held_execution_id"] != "exap_held1" ||
		coded.Details["state"] != "pending" || coded.Details["execution_status"] != "held" {
		t.Fatalf("exit %d code %v", code, coded)
	}
	if !strings.Contains(coded.Actionable, "review_url") || !strings.Contains(coded.Actionable, "jentic holds wait exap_held1") {
		t.Errorf("actionable = %q", coded.Actionable)
	}
}

func TestJobsWaitIsNotACommand(t *testing.T) {
	s := &heldStack{}
	srv := s.serve(t)
	if _, _, err := runHeld(t, srv, "jobs", "wait", "job_1"); err == nil {
		t.Fatal("`jentic jobs wait` must not exist; held calls are waited on with `jentic holds wait`")
	}
}
