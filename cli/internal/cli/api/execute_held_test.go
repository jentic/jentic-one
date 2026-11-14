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
	"time"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/config"
)

var heldBody = `{
	"status": "held",
	"held_execution_id": "held_held1",
	"review_url": "http://ui.local/app/agents/approvals/held_held1",
	"expires_at": "2026-10-10T12:00:00Z",
	"agent_directive": ` + strconv.Quote(heldAgentDirective) + `,
	"_links": {
		"self": "http://ui.local/executions/holds/held_held1",
		"result": "http://ui.local/executions/holds/held_held1/result",
		"withdraw": "http://ui.local/executions/holds/held_held1:withdraw"
	},
	"unknown_future_field": {"preserve": "this"}
}`

// heldStack is one httptest server playing the broker (every path outside
// /executions/holds answers the held 202) and the control plane's holds
// routes: GET /executions/holds/{id} reports state "pending" and execution
// status "held" for the first heldPolls polls, then state/status; GET
// /executions/holds/{id}/result answers result (with resultType), or
// resultStatus when non-zero, or 409 when empty.
type heldStack struct {
	heldPolls     int32
	state         string
	status        string
	result        string
	resultType    string
	resultStatus  int
	holdStatus    int  // non-zero: GET /executions/holds/{id} answers this status instead
	holdDrop      bool // GET /executions/holds/{id} drops the connection after the first poll
	noReviewURL   bool // GET /executions/holds/{id} omits review_url
	upstreamHeld  bool
	foreignReview bool
	reviewOrigin  string
	brokerHits    atomic.Int32
	polls         atomic.Int32
	jobsHits      atomic.Int32
}

func (s *heldStack) serve(t *testing.T) *httptest.Server {
	t.Helper()
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reviewOrigin := srv.URL
		if s.foreignReview {
			reviewOrigin = "http://ui.local"
		}
		if s.reviewOrigin != "" {
			reviewOrigin = s.reviewOrigin
		}
		switch {
		case r.URL.Path == "/executions/holds/held_held1/result":
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
			n := s.polls.Add(1)
			if s.holdDrop && n > 1 {
				conn, _, _ := w.(http.Hijacker).Hijack()
				_ = conn.Close()
				return
			}
			if n <= s.heldPolls {
				state, status = "pending", "held"
			}
			doc := map[string]any{
				"id": "held_held1", "state": state, "execution_status": status,
				"agent_id": "agt_1", "api_vendor": "example.com", "api_name": "orders", "api_version": "1",
				"credential_id": "cred_1", "method": "POST", "path": "/v1/orders",
				"execution_id": "exec_9", "expires_at": "2026-10-10T12:00:00Z",
				"created_at": "2026-10-09T12:00:00Z", "review_url": reviewOrigin + "/app/agents/approvals/held_held1",
				"_links": map[string]any{"self": "/executions/holds/held_held1"},
			}
			if s.noReviewURL {
				delete(doc, "review_url")
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(doc)
		case strings.HasPrefix(r.URL.Path, "/jobs/"):
			s.jobsHits.Add(1)
			w.WriteHeader(http.StatusNotFound)
		default:
			s.brokerHits.Add(1)
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Preference-Applied", "respond-async")
			if s.upstreamHeld {
				w.Header().Set("Jentic-Upstream-Status", "202")
			}
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(strings.ReplaceAll(heldBody, "http://ui.local", reviewOrigin)))
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

const reviewURL = "http://ui.local/app/agents/approvals/held_held1"

func reviewURLFor(srv *httptest.Server) string {
	return srv.URL + "/app/agents/approvals/held_held1"
}

func TestExecuteHeldExits3WithTheReviewLink(t *testing.T) {
	s := &heldStack{}
	srv := s.serve(t)
	reviewURL := reviewURLFor(srv)
	stdout, stderr, err := runHeld(t, srv, "execute", "POST:/v1/orders", "-d", `{"n":1}`, "--json")

	code, coded := exitOf(t, err)
	if code != ux.ExitTimeoutPending || coded.Code != ux.CodeTimeoutPending {
		t.Fatalf("exit %d code %v, want 3 TIMEOUT_PENDING", code, coded)
	}
	if coded.Details["held_execution_id"] != "held_held1" || coded.Details["review_url"] != reviewURL ||
		coded.Details["next_command"] != "jentic executions poll held_held1" {
		t.Errorf("details = %v", coded.Details)
	}
	if _, ok := coded.Details["job_id"]; ok {
		t.Errorf("details = %v, want no job_id", coded.Details)
	}
	if !strings.Contains(coded.Msg, "held execution held_held1") ||
		!strings.Contains(coded.Actionable, "jentic executions poll held_held1") {
		t.Errorf("msg %q / actionable %q do not name the held execution and executions poll", coded.Msg, coded.Actionable)
	}
	env := envelopeOf(t, stdout)
	body, _ := env["body"].(map[string]any)
	if env["status"] != float64(202) || body["status"] != "held" || body["held_execution_id"] != "held_held1" {
		t.Errorf("stdout envelope = %v", env)
	}
	if body["agent_directive"] != heldAgentDirective {
		t.Errorf("trusted held directive = %q, want the broker directive %q", body["agent_directive"], heldAgentDirective)
	}
	for _, want := range []string{"needs human approval", "held execution: held_held1", reviewURL, "jentic executions poll held_held1"} {
		if !strings.Contains(stderr, want) {
			t.Errorf("stderr missing %q:\n%s", want, stderr)
		}
	}
	if s.polls.Load() != 0 {
		t.Errorf("without --wait the hold is never polled (polled %d times)", s.polls.Load())
	}
}

func TestExecuteCLI_UpstreamHeldShapeRendersAsResponse(t *testing.T) {
	s := &heldStack{upstreamHeld: true}
	srv := s.serve(t)
	stdout, stderr, err := runHeld(t, srv, "execute", "POST:/v1/orders", "--json")
	if err != nil {
		t.Fatalf("an upstream 202 is response data, got %v", err)
	}
	env := envelopeOf(t, stdout)
	body, _ := env["body"].(map[string]any)
	if env["status"] != float64(http.StatusAccepted) || body["status"] != "held" || body["held_execution_id"] != "held_held1" {
		t.Errorf("stdout envelope = %v, want the upstream body relayed", env)
	}
	if strings.Contains(stderr, "open:") || strings.Contains(stderr, "Held —") {
		t.Errorf("upstream response must not render a review prompt:\n%s", stderr)
	}
	if s.polls.Load() != 0 {
		t.Errorf("upstream response triggered %d hold polls", s.polls.Load())
	}
}

func TestExecuteCLI_ForeignReviewURLWarnsInsteadOfOpening(t *testing.T) {
	for _, tc := range []struct {
		name string
		args []string
	}{
		{name: "immediate", args: []string{"execute", "POST:/v1/orders", "--json"}},
		{name: "wait timeout", args: []string{"execute", "POST:/v1/orders", "--wait", "--timeout", "0", "--json"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := &heldStack{heldPolls: 1 << 20, foreignReview: true}
			srv := s.serve(t)
			stdout, stderr, err := runHeld(t, srv, tc.args...)
			code, coded := exitOf(t, err)
			if code != ux.ExitTimeoutPending || coded.Details["review_url"] != reviewURL {
				t.Fatalf("exit %d details %v, want the held execution with its original response data", code, coded.Details)
			}
			if strings.Contains(stderr, "open:") || !strings.Contains(stderr, "review_url did not come from the configured Jentic instance") {
				t.Errorf("stderr must warn without an open instruction:\n%s", stderr)
			}
			if strings.Contains(coded.Actionable, "Show the user the review_url") {
				t.Errorf("actionable step must not promote the untrusted URL: %q", coded.Actionable)
			}

			env := envelopeOf(t, stdout)
			body, ok := env["body"].(map[string]any)
			if !ok {
				t.Fatalf("stdout body = %T, want the held response object", env["body"])
			}
			wantDirective := untrustedReviewInstruction +
				" When they reply, run `jentic executions poll held_held1` once for the result. Do not re-send the call."
			directive, _ := body["agent_directive"].(string)
			if directive != wantDirective {
				t.Errorf("stdout agent_directive = %q, want non-promoting warning and poll recovery %q", directive, wantDirective)
			}
			if strings.Contains(strings.ToLower(directive), "show the user") || strings.Contains(directive, reviewURL) {
				t.Errorf("stdout agent_directive promotes the rejected URL: %q", directive)
			}
			if body["review_url"] != reviewURL || body["held_execution_id"] != "held_held1" ||
				body["expires_at"] != "2026-10-10T12:00:00Z" {
				t.Errorf("stdout held response data was not preserved: %v", body)
			}
			if extension, _ := body["unknown_future_field"].(map[string]any); extension["preserve"] != "this" {
				t.Errorf("unknown held response field was not preserved: %v", body["unknown_future_field"])
			}
		})
	}
}

func TestHeldRefConfigureTrustResolvesEffectiveBroker(t *testing.T) {
	defaultBrokerURL := config.DefaultBrokerScheme + "://" + config.DefaultBrokerHost
	for _, tc := range []struct {
		name             string
		configuredBroker string
		brokerScheme     string
		brokerHost       string
		reviewURL        string
		wantBroker       string
		wantTrusted      bool
	}{
		{
			name:        "zero values use the canonical default",
			reviewURL:   defaultBrokerURL + "/review",
			wantBroker:  defaultBrokerURL,
			wantTrusted: true,
		},
		{
			name:             "configured broker takes precedence over the default",
			configuredBroker: "https://configured.corp",
			reviewURL:        "https://configured.corp/review",
			wantBroker:       "https://configured.corp",
			wantTrusted:      true,
		},
		{
			name:             "configured broker does not trust the default",
			configuredBroker: "https://configured.corp",
			reviewURL:        defaultBrokerURL + "/review",
			wantBroker:       "https://configured.corp",
		},
		{
			name:             "complete explicit pair overrides configured broker",
			configuredBroker: "https://configured.corp",
			brokerScheme:     "https",
			brokerHost:       "flags.corp",
			reviewURL:        "https://flags.corp/review",
			wantBroker:       "https://flags.corp",
			wantTrusted:      true,
		},
		{
			name:             "explicit pair does not trust overridden configured broker",
			configuredBroker: "https://configured.corp",
			brokerScheme:     "https",
			brokerHost:       "flags.corp",
			reviewURL:        "https://configured.corp/review",
			wantBroker:       "https://flags.corp",
		},
		{
			name:         "scheme without host fails closed",
			brokerScheme: "https",
			reviewURL:    defaultBrokerURL + "/review",
		},
		{
			name:       "host without scheme fails closed",
			brokerHost: "127.0.0.1:8100",
			reviewURL:  defaultBrokerURL + "/review",
		},
		{
			name:             "malformed explicit pair fails closed without configured fallback",
			configuredBroker: "https://configured.corp",
			brokerScheme:     "https",
			brokerHost:       "bad host",
			reviewURL:        "https://configured.corp/review",
			wantBroker:       "https://bad host",
		},
		{
			name:             "malformed configured broker fails closed without default fallback",
			configuredBroker: "://bad",
			reviewURL:        defaultBrokerURL + "/review",
			wantBroker:       "://bad",
		},
		{
			name:             "control origin remains trusted with malformed broker target",
			configuredBroker: "://bad",
			reviewURL:        "https://control.corp/review",
			wantBroker:       "://bad",
			wantTrusted:      true,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx := activeCtx("https://control.corp")
			clictx.ActiveContext(ctx).BrokerURL = tc.configuredBroker
			ref := &heldRef{ReviewURL: tc.reviewURL}
			ref.configureTrust(ctx, tc.brokerScheme, tc.brokerHost)
			if ref.brokerURL != tc.wantBroker {
				t.Errorf("broker URL = %q, want %q", ref.brokerURL, tc.wantBroker)
			}
			if got := ref.trustedReviewURL(); got != tc.wantTrusted {
				t.Errorf("trustedReviewURL() = %v, want %v", got, tc.wantTrusted)
			}
		})
	}
}

func TestExecuteHeld_TrustsEffectiveBrokerFlags(t *testing.T) {
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("unexpected control-plane request %s", r.URL.Path)
		http.NotFound(w, r)
	}))
	t.Cleanup(control.Close)
	s := &heldStack{}
	broker := s.serve(t)
	app := testApp(t)
	seedRegistered(t, app, "default", control.URL)
	out, errBuf := new(bytes.Buffer), new(bytes.Buffer)
	app.Out, app.Err = out, errBuf
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(errBuf)
	root.SetArgs([]string{
		"execute", "POST:/v1/orders", "--json",
		"--broker-scheme", "http", "--broker-host", broker.Listener.Addr().String(),
	})

	err := root.Execute()
	if code, _ := exitOf(t, err); code != ux.ExitTimeoutPending {
		t.Fatalf("exit %d, want the held execution", code)
	}
	if want := "open: " + reviewURLFor(broker); !strings.Contains(errBuf.String(), want) {
		t.Errorf("stderr missing %q for the effective broker origin:\n%s", want, errBuf.String())
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
	if env["status"] != float64(201) || body["order"] != float64(7) || env["execution_id"] != "exec_9" ||
		env["held_execution_id"] != "held_held1" {
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
	for _, want := range []string{"waiting for the decision", "held execution held_held1: the call ran (HTTP 201)"} {
		if !strings.Contains(stderr, want) {
			t.Errorf("stderr missing %q:\n%s", want, stderr)
		}
	}
}

func TestExecuteWaitDeniedExits2WithTheReason(t *testing.T) {
	s := &heldStack{
		state:      "denied",
		status:     "failed",
		resultType: "application/problem+json",
		result: `{"type":"approval_denied","title":"Execution approval denied","status":403,` +
			`"detail":"A reviewer denied this held execution: not today","error_origin":"broker",` +
			`"held_execution":{"id":"held_held1","state":"denied"}}`,
	}
	srv := s.serve(t)
	stdout, stderr, err := runHeld(t, srv, "execute", "POST:/v1/orders", "--wait", "--json")
	code, coded := exitOf(t, err)
	if code != ux.ExitDenied || coded.Code != ux.CodeBrokerDenied || coded.Details["problem_type"] != "approval_denied" {
		t.Fatalf("exit %d code %v, want 2 BROKER_DENIED approval_denied", code, coded)
	}
	if coded.Details["held_execution_id"] != "held_held1" || !strings.Contains(coded.Msg, "held_held1") {
		t.Errorf("msg %q details %v, want the held execution named", coded.Msg, coded.Details)
	}
	env := envelopeOf(t, stdout)
	body, _ := env["body"].(map[string]any)
	if env["status"] != float64(403) || body["type"] != "approval_denied" || env["held_execution_id"] != "held_held1" {
		t.Errorf("envelope = %v", env)
	}
	for _, want := range []string{"Denied by a reviewer", "not today", "held execution: held_held1"} {
		if !strings.Contains(stderr, want) {
			t.Errorf("stderr missing %q:\n%s", want, stderr)
		}
	}
	if strings.Contains(stderr, "credential binding") {
		t.Errorf("an approval denial must not get the binding hint:\n%s", stderr)
	}
}

func TestExecuteWaitTimeoutExits3WithTheHeldEnvelope(t *testing.T) {
	s := &heldStack{heldPolls: 1 << 20}
	srv := s.serve(t)
	reviewURL := reviewURLFor(srv)
	stdout, stderr, err := runHeld(t, srv, "execute", "GET:/v1/items", "--wait", "--timeout", "1", "--json")
	code, coded := exitOf(t, err)
	if code != ux.ExitTimeoutPending || !strings.Contains(coded.Msg, "after waiting 1s") {
		t.Fatalf("exit %d code %v", code, coded)
	}
	if coded.Details["held_execution_id"] != "held_held1" || coded.Details["review_url"] != reviewURL ||
		coded.Details["next_command"] != "jentic executions poll held_held1" {
		t.Errorf("details = %v", coded.Details)
	}
	if env := envelopeOf(t, stdout); env["status"] != float64(202) {
		t.Errorf("envelope = %v", env)
	}
	if s.polls.Load() == 0 {
		t.Error("--wait never polled the hold")
	}
	if !strings.Contains(stderr, "Still held after 1s") || strings.Count(stderr, "jentic executions poll held_held1") < 1 {
		t.Errorf("stderr = %s", stderr)
	}
}

func TestExecuteWaitZeroTimeoutChecksOnce(t *testing.T) {
	s := &heldStack{heldPolls: 1 << 20}
	srv := s.serve(t)
	_, _, err := runHeld(t, srv, "execute", "GET:/v1/items", "--wait", "--timeout", "0", "--json")
	if code, _ := exitOf(t, err); code != ux.ExitTimeoutPending || s.polls.Load() != 1 {
		t.Fatalf("exit %d polls %d, want 3 after exactly one poll", code, s.polls.Load())
	}
}

func TestExecuteWaitRejectsATimeoutAboveTheMaximum(t *testing.T) {
	for _, v := range []string{"6m", "301", "-1s", "soon"} {
		t.Run(v, func(t *testing.T) {
			s := &heldStack{}
			srv := s.serve(t)
			_, _, err := runHeld(t, srv, "execute", "GET:/v1/items", "--wait", "--timeout", v)
			code, coded := exitOf(t, err)
			if code != ux.ExitError || coded.Code != ux.CodeMissingArgument || !strings.Contains(coded.Msg, "--timeout") {
				t.Fatalf("exit %d code %v, want 1 MISSING_ARGUMENT naming --timeout", code, coded)
			}
			if s.brokerHits.Load() != 0 {
				t.Error("a bad --timeout must fail before the call is sent")
			}
		})
	}
}

func TestParseHeldTimeout(t *testing.T) {
	for in, want := range map[string]time.Duration{
		"30s": 30 * time.Second, "1m": time.Minute, "30": 30 * time.Second, "0": 0, "0s": 0,
		"5m": 5 * time.Minute, "300": 5 * time.Minute, "1500ms": 1500 * time.Millisecond,
	} {
		if got, err := parseHeldTimeout(in); err != nil || got != want {
			t.Errorf("parseHeldTimeout(%q) = %v, %v; want %v", in, got, err, want)
		}
	}
	if got, err := parseHeldTimeout(" 45 "); err != nil || got != 45*time.Second {
		t.Errorf("parseHeldTimeout(\" 45 \") = %v, %v; want 45s (surrounding spaces trimmed)", got, err)
	}
	for _, in := range []string{"5m1s", "301", "1h", "-1", "-5s", "", "ten"} {
		if _, err := parseHeldTimeout(in); err == nil {
			t.Errorf("parseHeldTimeout(%q) accepted, want an error", in)
		}
	}
	if _, err := parseHeldTimeout("6m"); err == nil || !strings.Contains(err.Error(), "maximum of 5m0s") {
		t.Errorf("6m: err = %v, want the maximum named", err)
	}
}

func TestHeldTimeoutDefaultsTo30s(t *testing.T) {
	app := testApp(t)
	root := newAPIRootCmd(app.App)
	for _, path := range [][]string{{"execute"}, {"executions", "poll"}} {
		cmd, _, err := root.Find(path)
		if err != nil {
			t.Fatalf("find %v: %v", path, err)
		}
		f := cmd.Flags().Lookup("timeout")
		if f == nil || f.DefValue != "30s" || f.Value.Type() != "duration" {
			t.Errorf("%v --timeout = %+v, want a 30s duration default", path, f)
		}
	}
}

func TestExecutionsPoll(t *testing.T) {
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
			check: func(t *testing.T, stdout, stderr string) {
				env := envelopeOf(t, stdout)
				headers, _ := env["headers"].(map[string]any)
				if env["status"] != float64(404) || headers["Jentic-Error-Origin"] != "upstream" ||
					env["held_execution_id"] != "held_held1" {
					t.Errorf("envelope = %v", env)
				}
				if !strings.Contains(stderr, "held execution held_held1: the call ran (HTTP 404)") {
					t.Errorf("stderr = %s", stderr)
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
				`"held_execution":{"id":"held_held1","state":"expired"}}`},
			wantExit: ux.ExitDenied,
			wantCode: ux.CodeBrokerDenied,
			check: func(t *testing.T, _, stderr string) {
				if !strings.Contains(stderr, "Expired") || !strings.Contains(stderr, "held execution: held_held1") {
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
			stdout, stderr, err := runHeld(t, srv, "executions", "poll", "held_held1", "--json")
			code, coded := exitOf(t, err)
			if code != tc.wantExit || (tc.wantCode != "" && coded.Code != tc.wantCode) {
				t.Fatalf("exit %d code %v, want %d %s\nstderr: %s", code, coded, tc.wantExit, tc.wantCode, stderr)
			}
			if coded != nil && (coded.Details["held_execution_id"] != "held_held1" || !strings.Contains(coded.Msg, "held_held1")) {
				t.Errorf("msg %q details %v, want the held execution named", coded.Msg, coded.Details)
			}
			if s.brokerHits.Load() != 0 || s.jobsHits.Load() != 0 {
				t.Error("executions poll never sends a call and never reads /jobs")
			}
			if tc.check != nil {
				tc.check(t, stdout, stderr)
			}
		})
	}
}

func TestExecutionsPollTrustsCanonicalDefaultBroker(t *testing.T) {
	t.Setenv("JENTIC_BROKER_URL", "")
	s := &heldStack{heldPolls: 1 << 20, reviewOrigin: "https://127.0.0.1:8100"}
	srv := s.serve(t)
	reviewURL := "https://127.0.0.1:8100/app/agents/approvals/held_held1"
	_, stderr, err := runHeld(t, srv, "executions", "poll", "held_held1", "--timeout", "0", "--json")

	code, coded := exitOf(t, err)
	if code != ux.ExitTimeoutPending || coded.Details["review_url"] != reviewURL {
		t.Fatalf("exit %d details %v, want the held execution with its review URL", code, coded.Details)
	}
	if !strings.Contains(coded.Actionable, "Show the user the review_url ("+reviewURL+")") {
		t.Errorf("actionable = %q, want trusted review-link recovery for the canonical default broker", coded.Actionable)
	}
	untrustedWarning := "review_url did not come from the configured Jentic instance"
	if strings.Contains(coded.Actionable, untrustedWarning) {
		t.Errorf("actionable rejects the canonical default broker review URL: %q", coded.Actionable)
	}
	if !strings.Contains(stderr, "open: "+reviewURL) || strings.Contains(stderr, untrustedWarning) {
		t.Errorf("stderr must open the trusted default-broker review URL without a warning:\n%s", stderr)
	}
	if s.brokerHits.Load() != 0 || s.polls.Load() != 1 {
		t.Errorf("broker hits = %d, polls = %d; want one control-plane poll and no broker call", s.brokerHits.Load(), s.polls.Load())
	}
}

func TestExecutionsPollTimeoutOnAPendingHoldExits3(t *testing.T) {
	s := &heldStack{heldPolls: 1 << 20}
	srv := s.serve(t)
	reviewURL := reviewURLFor(srv)
	_, stderr, err := runHeld(t, srv, "executions", "poll", "held_held1", "--timeout", "20ms")
	code, coded := exitOf(t, err)
	if code != ux.ExitTimeoutPending || coded.Details["held_execution_id"] != "held_held1" ||
		coded.Details["state"] != "pending" || coded.Details["execution_status"] != "held" ||
		coded.Details["review_url"] != reviewURL || coded.Details["expires_at"] != "2026-10-10T12:00:00Z" ||
		coded.Details["next_command"] != "jentic executions poll held_held1" {
		t.Fatalf("exit %d code %v", code, coded)
	}
	if !strings.Contains(coded.Actionable, reviewURL) || !strings.Contains(coded.Actionable, "jentic executions poll held_held1") {
		t.Errorf("actionable = %q", coded.Actionable)
	}
	for _, want := range []string{
		"Still held after 20ms", "held execution: held_held1", "open: " + reviewURL,
		"then: jentic executions poll held_held1",
	} {
		if !strings.Contains(stderr, want) {
			t.Errorf("stderr missing %q:\n%s", want, stderr)
		}
	}
}

func TestExecutionsPollZeroTimeoutChecksOnce(t *testing.T) {
	s := &heldStack{heldPolls: 1 << 20}
	srv := s.serve(t)
	_, _, err := runHeld(t, srv, "executions", "poll", "held_held1", "--timeout", "0")
	if code, _ := exitOf(t, err); code != ux.ExitTimeoutPending || s.polls.Load() != 1 {
		t.Fatalf("exit %d polls %d, want 3 after exactly one poll", code, s.polls.Load())
	}
}

func TestExecutionsPollAcceptsBareSeconds(t *testing.T) {
	s := &heldStack{
		state: "approved", status: "completed", resultType: "application/json",
		result: `{"execution_id":"exec_9","status":"completed","http_status":200,"duration_ms":1,"body_b64":""}`,
	}
	srv := s.serve(t)
	if _, stderr, err := runHeld(t, srv, "executions", "poll", "held_held1", "--timeout", "45", "--json"); err != nil {
		t.Fatalf("--timeout 45: %v\n%s", err, stderr)
	}
}

func TestExecutionsPollRejectsATimeoutAboveTheMaximum(t *testing.T) {
	s := &heldStack{}
	srv := s.serve(t)
	_, _, err := runHeld(t, srv, "executions", "poll", "held_held1", "--timeout", "10m")
	code, coded := exitOf(t, err)
	if code != ux.ExitError || coded.Code != ux.CodeMissingArgument || !strings.Contains(coded.Msg, "maximum of 5m0s") {
		t.Fatalf("exit %d code %v, want 1 MISSING_ARGUMENT naming the maximum", code, coded)
	}
	if s.polls.Load() != 0 {
		t.Error("a bad --timeout must fail before the hold is read")
	}
}

func TestExecutionsPollTransportErrorKeepsTheHeldExecution(t *testing.T) {
	s := &heldStack{heldPolls: 1 << 20, holdDrop: true}
	srv := s.serve(t)
	reviewURL := reviewURLFor(srv)
	_, stderr, err := runHeld(t, srv, "executions", "poll", "held_held1", "--timeout", "5s")
	code, coded := exitOf(t, err)
	if code != ux.ExitError || coded.Code != ux.CodeTransportError {
		t.Fatalf("exit %d code %v, want 1 TRANSPORT_ERROR", code, coded)
	}
	if coded.Details["held_execution_id"] != "held_held1" || coded.Details["review_url"] != reviewURL ||
		coded.Details["next_command"] != "jentic executions poll held_held1" {
		t.Errorf("details = %v", coded.Details)
	}
	if !strings.Contains(coded.Actionable, reviewURL) || !strings.Contains(coded.Actionable, "jentic executions poll held_held1") {
		t.Errorf("actionable = %q", coded.Actionable)
	}
	for _, want := range []string{
		"Could not read held execution held_held1", "open: " + reviewURL,
		"then: jentic executions poll held_held1",
	} {
		if !strings.Contains(stderr, want) {
			t.Errorf("stderr missing %q:\n%s", want, stderr)
		}
	}
}

func TestExecuteWaitTransportErrorKeepsTheReviewURLFromTheHeldEnvelope(t *testing.T) {
	s := &heldStack{heldPolls: 1 << 20, holdStatus: http.StatusBadGateway}
	srv := s.serve(t)
	reviewURL := reviewURLFor(srv)
	_, stderr, err := runHeld(t, srv, "execute", "GET:/v1/items", "--wait", "--json")
	_, coded := exitOf(t, err)
	if coded == nil || coded.Details["held_execution_id"] != "held_held1" || coded.Details["review_url"] != reviewURL ||
		coded.Details["next_command"] != "jentic executions poll held_held1" {
		t.Fatalf("err = %v details = %v", err, coded)
	}
	if !strings.Contains(stderr, "Could not read held execution held_held1") || !strings.Contains(stderr, "open: "+reviewURL) {
		t.Errorf("stderr = %s", stderr)
	}
}

func TestHoldsAndJobsWaitAreNotCommands(t *testing.T) {
	s := &heldStack{}
	srv := s.serve(t)
	for _, args := range [][]string{{"holds", "wait", "held_held1"}, {"jobs", "wait", "job_1"}} {
		if _, _, err := runHeld(t, srv, args...); err == nil || !strings.Contains(err.Error(), "unknown command") {
			t.Errorf("`jentic %s` = %v, want unknown command; held calls are followed with `jentic executions poll`",
				strings.Join(args, " "), err)
		}
	}
}
