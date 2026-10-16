package agentops

import (
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// brokerTransportResult is brokerResult with an optional Retry-After header.
func brokerTransportResult(status int, origin, retryAfter, body string) *ExecuteResult {
	r := brokerResult(status, origin, body)
	if retryAfter != "" {
		r.Headers.Set("Retry-After", retryAfter)
	}
	return r
}

// TestBrokerTransportError pins #1531: a 5xx or 429 the broker itself emitted
// is a TRANSPORT_ERROR (exit 1) whose retryable hint follows the problem type,
// while a mirrored upstream 5xx and a missing origin header keep passing through.
func TestBrokerTransportError(t *testing.T) {
	cases := []struct {
		name          string
		res           *ExecuteResult
		retrySafe     bool
		want          bool
		wantRetryable bool
		wantRec       BrokerTransportRecovery
		wantAfter     any // float64 when Retry-After is relayed, else nil
	}{
		// The broker's own 5xx problem types, in its wire shape.
		{
			name: "circuit open (CircuitOpenError) with Retry-After",
			res: brokerTransportResult(503, "broker", "12",
				`{"type":"circuit_open","title":"Upstream circuit open; the broker is fast-failing to let it recover.","status":503,"error_origin":"broker"}`),
			want: true, wantRetryable: true, wantRec: RecoverWait, wantAfter: float64(12),
		},
		{
			name: "runner unavailable (RunnerUnavailableError)",
			res: brokerTransportResult(503, "broker", "",
				`{"type":"runner_unavailable","title":"The runner for the 'grpc' scheme is currently unavailable.","status":503}`),
			want: true, wantRetryable: true, wantRec: RecoverWait,
		},
		{
			name: "admission shed (server_at_capacity) with Retry-After",
			res:  brokerTransportResult(503, "broker", "1", `{"type":"server_at_capacity","title":"Server at capacity; retry shortly.","status":503}`),
			want: true, wantRetryable: true, wantRec: RecoverWait, wantAfter: float64(1),
		},
		{
			name: "transient credential refresh (CredentialRefreshTransientError) stamped broker",
			res:  brokerTransportResult(502, "broker", "", `{"type":"refresh_transient_error","title":"cred_1: TimeoutError","status":502}`),
			want: true, wantRetryable: true, wantRec: RecoverWait,
		},
		{
			name: "upstream timeout (UpstreamTimeoutError, untyped 504) on an unsafe call",
			res:  brokerTransportResult(504, "broker", "", `{"type":"about:blank","title":"The upstream did not respond within the deadline.","status":504}`),
			want: true, wantRetryable: false, wantRec: RecoverTimeout,
		},
		{
			name: "upstream timeout on a retry-safe call", retrySafe: true,
			res:  brokerTransportResult(504, "broker", "", `{"type":"about:blank","title":"The upstream did not respond within the deadline.","status":504}`),
			want: true, wantRetryable: true, wantRec: RecoverTimeout,
		},
		{
			name: "deadline exceeded (DeadlineExceededError) on a retry-safe call", retrySafe: true,
			res:  brokerTransportResult(504, "broker", "", `{"type":"deadline_exceeded","title":"The request exceeded the broker's 30s overall deadline.","status":504}`),
			want: true, wantRetryable: true, wantRec: RecoverTimeout,
		},
		{
			name: "scheme unsupported (RunnerSchemeUnsupportedError) is never retryable", retrySafe: true,
			res: brokerTransportResult(501, "broker", "",
				`{"type":"runner_scheme_unsupported","title":"No upstream runner is registered for the 'ftp' scheme.","status":501}`),
			want: true, wantRetryable: false, wantRec: RecoverScheme,
		},
		{
			name: "response too large (UpstreamResponseTooLargeError, untyped 502) is never retryable", retrySafe: true,
			res: brokerTransportResult(502, "broker", "",
				`{"type":"about:blank","title":"The upstream response exceeded the 1048576-byte response-size cap.","status":502,"max_response_bytes":1048576}`),
			want: true, wantRetryable: false, wantRec: RecoverUnknown,
		},
		{
			name: "unknown broker 5xx type defaults to not retryable", retrySafe: true,
			res:  brokerTransportResult(500, "broker", "", `{"type":"streaming_unsupported","title":"The wrapped runner does not support streaming.","status":500}`),
			want: true, wantRetryable: false, wantRec: RecoverUnknown,
		},
		{
			name: "non-JSON broker 5xx defaults to not retryable",
			res:  brokerTransportResult(502, "broker", "", `Bad Gateway`),
			want: true, wantRetryable: false, wantRec: RecoverUnknown,
		},
		{
			name: "broker 429 with Retry-After is retryable",
			res: brokerTransportResult(429, "broker", "7",
				`{"type":"rate_limit_exceeded","title":"Rate limit exceeded; slow down and retry after the indicated delay.","status":429}`),
			want: true, wantRetryable: true, wantRec: RecoverWait, wantAfter: float64(7),
		},
		{
			name: "unparseable Retry-After is not relayed",
			res:  brokerTransportResult(503, "broker", "soon", `{"type":"circuit_open","title":"open","status":503}`),
			want: true, wantRetryable: true, wantRec: RecoverWait,
		},
		{
			name: "origin header is case and space tolerant",
			res:  brokerTransportResult(503, " Broker ", "", `{"type":"circuit_open"}`),
			want: true, wantRetryable: true, wantRec: RecoverWait,
		},

		// The header decides: everything not stamped "broker" passes through.
		{
			name: "upstream 503 is the caller's data",
			res:  brokerTransportResult(503, "upstream", "5", `{"type":"circuit_open","error":"vendor down"}`),
		},
		{
			name: "upstream 500 is the caller's data",
			res:  brokerTransportResult(500, "upstream", "", `{"error":"boom"}`),
		},
		{
			name: "upstream 429 is the caller's data",
			res:  brokerTransportResult(429, "upstream", "3", `{"error":"slow down"}`),
		},
		{
			name: "missing origin keeps pass-through",
			res:  brokerTransportResult(502, "", "", `{"type":"circuit_open"}`),
		},
		// The broker stamps "upstream" on its own timeout, deadline, refresh and
		// response-size failures (broker/adapters/runners, credentials/orchestrator),
		// so in their real wire shape they keep passing through too.
		{
			name: "upstream-stamped UpstreamTimeoutError passes through",
			res:  brokerTransportResult(504, "upstream", "", `{"type":"about:blank","title":"The upstream did not respond within the deadline.","status":504,"error_origin":"upstream"}`),
		},
		{
			name: "upstream-stamped DeadlineExceededError passes through",
			res:  brokerTransportResult(504, "upstream", "", `{"type":"deadline_exceeded","status":504,"error_origin":"upstream"}`),
		},
		{
			name: "upstream-stamped CredentialRefreshTransientError passes through",
			res:  brokerTransportResult(502, "upstream", "", `{"type":"refresh_transient_error","status":502,"error_origin":"upstream"}`),
		},
		{
			name: "upstream-stamped UpstreamResponseTooLargeError passes through",
			res:  brokerTransportResult(502, "upstream", "", `{"type":"about:blank","status":502,"error_origin":"upstream"}`),
		},
		{
			name: "broker 4xx stays on the resolve-failure path",
			res:  brokerTransportResult(404, "broker", "", `{"type":"operation_not_found"}`),
		},
		{
			name: "broker denial stays on the denial path",
			res:  brokerTransportResult(403, "broker", "", `{"type":"action_denied"}`),
		},
		{name: "success", res: brokerTransportResult(200, "", "", `{"ok":true}`)},
		{name: "nil result", res: nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := BrokerTransportError(tc.res, tc.retrySafe)
			if (got != nil) != tc.want {
				t.Fatalf("BrokerTransportError() = %v, want error: %v", got, tc.want)
			}
			if got == nil {
				return
			}
			if got.Code != ux.CodeTransportError || got.ExitCode() != ux.ExitError {
				t.Errorf("code=%q exit=%d, want TRANSPORT_ERROR / exit 1", got.Code, got.ExitCode())
			}
			if got.Details["http_status"] != tc.res.Status || got.Details["origin"] != "broker" {
				t.Errorf("details = %v, want the broker status and origin", got.Details)
			}
			if got.Details["retryable"] != tc.wantRetryable {
				t.Errorf("retryable = %v, want %v", got.Details["retryable"], tc.wantRetryable)
			}
			if got.Details["retry_after_s"] != tc.wantAfter {
				t.Errorf("retry_after_s = %v, want %v", got.Details["retry_after_s"], tc.wantAfter)
			}
			pt, _ := got.Details["problem_type"].(string)
			if rec := BrokerTransportRecoveryFor(tc.res.Status, pt); rec != tc.wantRec {
				t.Errorf("recovery = %q, want %q", rec, tc.wantRec)
			}
			if got.Actionable == "" {
				t.Error("a broker transport error must carry a recovery step")
			}
			// The advice must agree with the hint: never "re-run" on a call that
			// is not retryable.
			if !tc.wantRetryable && strings.Contains(got.Actionable, "re-run the same") {
				t.Errorf("non-retryable advice tells the agent to retry: %q", got.Actionable)
			}
		})
	}
}

// TestBrokerTransportErrorCarriesReason pins that the message names the
// broker's own reason and status, so the agent sees why the call failed.
func TestBrokerTransportErrorCarriesReason(t *testing.T) {
	got := BrokerTransportError(brokerResult(503, "broker",
		`{"type":"circuit_open","title":"Upstream circuit open","detail":"Upstream circuit open; fast-failing.","status":503}`), false)
	if got == nil {
		t.Fatal("want a coded error")
	}
	if !strings.Contains(got.Msg, "HTTP 503") || !strings.Contains(got.Msg, "fast-failing") {
		t.Errorf("msg %q should carry the status and the broker's detail", got.Msg)
	}
	if got.Details["problem_type"] != "circuit_open" {
		t.Errorf("problem_type = %v, want circuit_open", got.Details["problem_type"])
	}
	if !strings.Contains(got.Actionable, "Wait briefly") {
		t.Errorf("without Retry-After the advice should say wait briefly, got %q", got.Actionable)
	}

	withAfter := BrokerTransportError(brokerTransportResult(503, "broker", "30", `{"type":"circuit_open"}`), false)
	if !strings.Contains(withAfter.Actionable, "Wait 30s") {
		t.Errorf("the advice should relay the broker's Retry-After, got %q", withAfter.Actionable)
	}
}

func TestRetryAfterSeconds(t *testing.T) {
	future := time.Now().Add(90 * time.Second).UTC().Format(http.TimeFormat)
	past := time.Now().Add(-time.Hour).UTC().Format(http.TimeFormat)
	cases := []struct {
		value  string
		wantOK bool
		check  func(float64) bool
	}{
		{"", false, nil},
		{"12", true, func(s float64) bool { return s == 12 }},
		{" 3 ", true, func(s float64) bool { return s == 3 }},
		{"0", true, func(s float64) bool { return s == 0 }},
		{"-1", false, nil},
		{"soon", false, nil},
		{future, true, func(s float64) bool { return s > 60 && s <= 91 }},
		{past, true, func(s float64) bool { return s == 0 }},
	}
	for _, tc := range cases {
		h := http.Header{}
		if tc.value != "" {
			h.Set("Retry-After", tc.value)
		}
		got, ok := RetryAfterSeconds(h)
		if ok != tc.wantOK {
			t.Errorf("RetryAfterSeconds(%q) ok = %v, want %v", tc.value, ok, tc.wantOK)
			continue
		}
		if ok && !tc.check(got) {
			t.Errorf("RetryAfterSeconds(%q) = %v", tc.value, got)
		}
	}
}

func TestRetrySafe(t *testing.T) {
	cases := []struct {
		method, key string
		want        bool
	}{
		{http.MethodGet, "", true},
		{http.MethodHead, "", true},
		{http.MethodPost, "", false},
		{http.MethodPost, "idem-1", true},
		{http.MethodDelete, "", false},
	}
	for _, tc := range cases {
		if got := RetrySafe(tc.method, tc.key); got != tc.want {
			t.Errorf("RetrySafe(%s, %q) = %v, want %v", tc.method, tc.key, got, tc.want)
		}
	}
}
