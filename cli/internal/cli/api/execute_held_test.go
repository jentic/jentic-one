package api

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

const heldBody = `{
	"job_id": "job_held1",
	"status": "held",
	"approval": {"id": "apr_1", "review_url": "http://ui.local/app/agents/approvals/apr_1", "expires_at": "2026-10-10T12:00:00Z"},
	"agent_directive": "This call needs human approval.",
	"_links": {"self": "http://ui.local/jobs/job_held1"}
}`

// heldStack is one httptest server playing the broker (every non-/jobs path
// answers the held 202) and the control plane's job routes: GET /jobs/{id}
// reports "held" for the first heldPolls polls, then status; GET
// /jobs/{id}/result answers result (with resultType) or 409 when empty.
type heldStack struct {
	heldPolls  int32
	status     string
	result     string
	resultType string
	jobStatus  int // non-zero: GET /jobs/{id} answers this status instead
	brokerHits atomic.Int32
	polls      atomic.Int32
}

func (s *heldStack) serve(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/jobs/job_held1/result":
			if s.result == "" {
				w.Header().Set("Content-Type", "application/problem+json")
				w.WriteHeader(http.StatusConflict)
				_, _ = w.Write([]byte(`{"type":"job_not_completed","status":409}`))
				return
			}
			w.Header().Set("Content-Type", s.resultType)
			_, _ = w.Write([]byte(s.result))
		case strings.HasPrefix(r.URL.Path, "/jobs/"):
			if s.jobStatus != 0 {
				w.Header().Set("Content-Type", "application/problem+json")
				w.WriteHeader(s.jobStatus)
				_, _ = w.Write([]byte(`{"type":"not_found","status":404}`))
				return
			}
			status := s.status
			if s.polls.Add(1) <= s.heldPolls {
				status = "held"
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"job_id": "job_held1", "kind": "execution", "status": status,
				"approval_id": "apr_1", "execution_id": "exec_9",
				"created_at": "2026-10-09T12:00:00Z",
				"_links":     map[string]any{"self": "/jobs/job_held1"},
			})
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

func TestExecuteHeldExits3WithTheReviewLink(t *testing.T) {
	s := &heldStack{}
	srv := s.serve(t)
	stdout, stderr, err := runHeld(t, srv, "execute", "POST:/v1/orders", "-d", `{"n":1}`, "--json")

	code, coded := exitOf(t, err)
	if code != ux.ExitTimeoutPending || coded.Code != ux.CodeTimeoutPending {
		t.Fatalf("exit %d code %v, want 3 TIMEOUT_PENDING", code, coded)
	}
	if coded.Details["job_id"] != "job_held1" || coded.Details["review_url"] != "http://ui.local/app/agents/approvals/apr_1" {
		t.Errorf("details = %v", coded.Details)
	}
	if !strings.Contains(coded.Actionable, "jentic jobs wait job_held1") {
		t.Errorf("actionable %q does not name jobs wait", coded.Actionable)
	}
	env := envelopeOf(t, stdout)
	body, _ := env["body"].(map[string]any)
	if env["status"] != float64(202) || body["status"] != "held" || body["job_id"] != "job_held1" {
		t.Errorf("stdout envelope = %v", env)
	}
	for _, want := range []string{"needs human approval", "http://ui.local/app/agents/approvals/apr_1", "jentic jobs wait job_held1"} {
		if !strings.Contains(stderr, want) {
			t.Errorf("stderr missing %q:\n%s", want, stderr)
		}
	}
	if s.polls.Load() != 0 {
		t.Errorf("without --wait the job is never polled (polled %d times)", s.polls.Load())
	}
}

func TestExecuteWaitPrintsTheApprovedRunLikeASyncCall(t *testing.T) {
	s := &heldStack{
		heldPolls:  2,
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
	if !strings.Contains(stderr, "waiting for the decision") {
		t.Errorf("stderr = %s", stderr)
	}
}

func TestExecuteWaitDeniedExits2WithTheReason(t *testing.T) {
	s := &heldStack{
		status:     "failed",
		resultType: "application/problem+json",
		result: `{"type":"approval_denied","title":"Execution approval denied","status":403,` +
			`"detail":"A reviewer denied this held execution: not today","error_origin":"broker",` +
			`"approval":{"id":"apr_1","state":"denied"}}`,
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
	s := &heldStack{heldPolls: 1 << 20, status: "held"}
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
		t.Error("--wait never polled the job")
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

func TestJobsWait(t *testing.T) {
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
			stack:    &heldStack{status: "completed", resultType: "application/json", result: upstream404},
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
			stack: &heldStack{status: "failed", resultType: "application/json", result: `{"execution_id":"exec_9",` +
				`"status":"failed","http_status":403,"duration_ms":0,"problem":{"type":"action_denied",` +
				`"title":"Denied","status":403,"error_origin":"broker"}}`},
			wantExit: ux.ExitDenied,
			wantCode: ux.CodeBrokerDenied,
		},
		{
			name: "an expired approval exits 2",
			stack: &heldStack{status: "failed", resultType: "application/problem+json", result: `{"type":"approval_expired",` +
				`"title":"Execution approval expired","status":403,"error_origin":"broker"}`},
			wantExit: ux.ExitDenied,
			wantCode: ux.CodeBrokerDenied,
			check: func(t *testing.T, _, stderr string) {
				if !strings.Contains(stderr, "Expired") {
					t.Errorf("stderr = %s", stderr)
				}
			},
		},
		{
			name:     "a withdrawn hold (cancelled) exits 2",
			stack:    &heldStack{status: "cancelled"},
			wantExit: ux.ExitDenied,
			wantCode: ux.CodeBrokerDenied,
		},
		{
			name:     "a run with no upstream answer exits 1",
			stack:    &heldStack{status: "failed"},
			wantExit: ux.ExitError,
			wantCode: ux.CodeTransportError,
		},
		{
			name:     "an unknown job id exits 2",
			stack:    &heldStack{jobStatus: http.StatusNotFound},
			wantExit: ux.ExitDenied,
			wantCode: ux.CodeResolveFailed,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := tc.stack
			srv := s.serve(t)
			stdout, stderr, err := runHeld(t, srv, "jobs", "wait", "job_held1", "--json")
			code, coded := exitOf(t, err)
			if code != tc.wantExit || (tc.wantCode != "" && coded.Code != tc.wantCode) {
				t.Fatalf("exit %d code %v, want %d %s\nstderr: %s", code, coded, tc.wantExit, tc.wantCode, stderr)
			}
			if s.brokerHits.Load() != 0 {
				t.Error("jobs wait never sends a call")
			}
			if tc.check != nil {
				tc.check(t, stdout, stderr)
			}
		})
	}
}

func TestJobsWaitTimeoutOnAHeldJobExits3(t *testing.T) {
	s := &heldStack{heldPolls: 1 << 20}
	srv := s.serve(t)
	_, _, err := runHeld(t, srv, "jobs", "wait", "job_held1", "--timeout", "20ms")
	code, coded := exitOf(t, err)
	if code != ux.ExitTimeoutPending || coded.Details["approval_id"] != "apr_1" || coded.Details["job_status"] != "held" {
		t.Fatalf("exit %d code %v", code, coded)
	}
}
