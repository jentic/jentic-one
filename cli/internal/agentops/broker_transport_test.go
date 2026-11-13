package agentops

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
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
			name: "upstream timeout (UpstreamTimeoutError) on an unsafe call",
			res: brokerTransportResult(504, "broker", "",
				`{"type":"upstream_timeout","title":"The upstream did not respond within the deadline.","status":504,"error_origin":"broker"}`),
			want: true, wantRetryable: false, wantRec: RecoverTimeout,
		},
		{
			name: "upstream timeout on a retry-safe call", retrySafe: true,
			res:  brokerTransportResult(504, "broker", "", `{"type":"upstream_timeout","title":"The upstream did not respond within the deadline.","status":504}`),
			want: true, wantRetryable: true, wantRec: RecoverTimeout,
		},
		{
			name: "connect failure (UpstreamTimeoutError, pre-send) is retryable on an unsafe call",
			res: brokerTransportResult(504, "broker", "",
				`{"type":"upstream_connect_failed","title":"The upstream connection could not be established in time.","status":504}`),
			want: true, wantRetryable: true, wantRec: RecoverWait,
		},
		{
			name: "refused connect (UpstreamTransportError, pre-send) is retryable on an unsafe call",
			res: brokerTransportResult(502, "broker", "",
				`{"type":"upstream_connect_failed","title":"The upstream connection could not be established.","status":502}`),
			want: true, wantRetryable: true, wantRec: RecoverWait,
		},
		{
			name: "too many candidates (TooManyCandidatesError) fails before dispatch",
			res:  brokerTransportResult(503, "broker", "", `{"type":"too_many_candidates","title":"Discovery matched too many candidates.","status":503}`),
			want: true, wantRetryable: true, wantRec: RecoverWait,
		},
		{
			name: "an untyped broker 504 is unknown, never a guessed timeout", retrySafe: true,
			res:  brokerTransportResult(504, "broker", "", `{"type":"about:blank","title":"Gateway Timeout","status":504}`),
			want: true, wantRetryable: false, wantRec: RecoverUnknown,
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
			name: "response too large (UpstreamResponseTooLargeError) is never retryable", retrySafe: true,
			res: brokerTransportResult(502, "broker", "",
				`{"type":"upstream_response_too_large","title":"The upstream response exceeded the 1048576-byte response-size cap.","status":502,"max_response_bytes":1048576}`),
			want: true, wantRetryable: false, wantRec: RecoverUnknown,
		},
		{
			name: "unknown broker 5xx type defaults to not retryable", retrySafe: true,
			res:  brokerTransportResult(500, "broker", "", `{"type":"streaming_unsupported","title":"The wrapped runner does not support streaming.","status":500}`),
			want: true, wantRetryable: false, wantRec: RecoverUnknown,
		},
		{
			name: "mid-request transport failure (upstream_transport_error) is never retryable", retrySafe: true,
			res: brokerTransportResult(502, "broker", "",
				`{"type":"upstream_transport_error","title":"Upstream transport error (RemoteProtocolError)","status":502}`),
			want: true, wantRetryable: false, wantRec: RecoverUnknown,
		},
		{
			name: "invalid outbound request (upstream_request_invalid) is never retryable", retrySafe: true,
			res: brokerTransportResult(500, "broker", "",
				`{"type":"upstream_request_invalid","title":"Upstream transport error (LocalProtocolError)","status":500}`),
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
			name: "fractional Retry-After rounds up to whole seconds",
			res:  brokerTransportResult(503, "broker", "1.5", `{"type":"circuit_open","title":"open","status":503}`),
			want: true, wantRetryable: true, wantRec: RecoverWait, wantAfter: float64(2),
		},
		{
			name: "oversized Retry-After caps at one day",
			res:  brokerTransportResult(503, "broker", "999999", `{"type":"circuit_open","title":"open","status":503}`),
			want: true, wantRetryable: true, wantRec: RecoverWait, wantAfter: float64(86400),
		},
		{
			name: "signed Retry-After is not relayed",
			res:  brokerTransportResult(503, "broker", "+5", `{"type":"circuit_open","title":"open","status":503}`),
			want: true, wantRetryable: true, wantRec: RecoverWait,
		},
		{
			name: "exponent Retry-After is not relayed",
			res:  brokerTransportResult(503, "broker", "1e3", `{"type":"circuit_open","title":"open","status":503}`),
			want: true, wantRetryable: true, wantRec: RecoverWait,
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
		// A vendor 504/502 the broker mirrored stays the caller's data even when
		// its body happens to carry one of the broker's own type identifiers.
		{
			name: "mirrored vendor 504 with a timeout-shaped body passes through",
			res:  brokerTransportResult(504, "upstream", "", `{"type":"upstream_timeout","status":504}`),
		},
		{
			name: "mirrored vendor 502 with a refresh-shaped body passes through",
			res:  brokerTransportResult(502, "upstream", "", `{"type":"refresh_transient_error","status":502}`),
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

// TestRetryAfterSecondsParity replays the shared delay-seconds vector file
// (tests/fixtures/retry-after-parity.json) so Go, MCP and the broker enforce
// identical rounding, grammar and one-day cap rules (#1991).
func TestRetryAfterSecondsParity(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "fixtures", "retry-after-parity.json"))
	if err != nil {
		t.Fatalf("read shared Retry-After parity fixture: %v", err)
	}
	var fixture struct {
		Cases []struct {
			Name    string  `json:"name"`
			Header  *string `json:"header"`
			Seconds *int    `json:"seconds"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatalf("decode shared Retry-After parity fixture: %v", err)
	}
	if len(fixture.Cases) == 0 {
		t.Fatal("shared Retry-After parity fixture contains no cases")
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			h := http.Header{}
			label := "<absent>"
			if tc.Header != nil {
				h.Set("Retry-After", *tc.Header)
				label = *tc.Header
			}
			got, ok := RetryAfterSeconds(h)
			wantOK := tc.Seconds != nil
			if ok != wantOK {
				t.Fatalf("RetryAfterSeconds(%q) ok = %v, want %v (got %v)", label, ok, wantOK, got)
			}
			if !wantOK {
				if got != 0 {
					t.Errorf("RetryAfterSeconds(%q) = %v on unparseable input, want 0", label, got)
				}
				return
			}
			want := float64(*tc.Seconds)
			if got != want {
				t.Errorf("RetryAfterSeconds(%q) = %v, want %v", label, got, want)
			}
		})
	}
}

func TestRetryAfterSecondsHTTPDate(t *testing.T) {
	future := time.Now().Add(90 * time.Second).UTC().Format(http.TimeFormat)
	past := time.Now().Add(-time.Hour).UTC().Format(http.TimeFormat)
	farFuture := time.Now().Add(30 * 24 * time.Hour).UTC().Format(http.TimeFormat)
	liveCases := []struct {
		name  string
		value string
		check func(float64) bool
	}{
		{"near future", future, func(s float64) bool { return s > 60 && s <= 91 }},
		{"past date floors at zero", past, func(s float64) bool { return s == 0 }},
		{"far future caps at one day", farFuture, func(s float64) bool { return s == 86400 }},
	}
	for _, tc := range liveCases {
		t.Run(tc.name, func(t *testing.T) {
			h := http.Header{}
			h.Set("Retry-After", tc.value)
			got, ok := RetryAfterSeconds(h)
			if !ok {
				t.Fatalf("RetryAfterSeconds(%q) ok = false, want true", tc.value)
			}
			if !tc.check(got) {
				t.Errorf("RetryAfterSeconds(%q) = %v", tc.value, got)
			}
		})
	}

	fixedNow := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	fixedCases := []struct {
		name string
		now  time.Time
		when time.Time
		want float64
	}{
		{"exact future whole seconds", fixedNow, fixedNow.Add(45 * time.Second), 45},
		{"sub-second remaining rounds up", fixedNow.Add(500 * time.Millisecond), fixedNow.Add(45 * time.Second), 45},
		{"sub-second past next whole second rounds up", fixedNow.Add(-500 * time.Millisecond), fixedNow.Add(45 * time.Second), 46},
		{"sub-second past date floors at zero", fixedNow.Add(500 * time.Millisecond), fixedNow, 0},
		{"past date floors at zero", fixedNow, fixedNow.Add(-2 * time.Minute), 0},
		{"exact one-day cap", fixedNow, fixedNow.Add(86400 * time.Second), 86400},
		{"far-future date caps at one day", fixedNow, fixedNow.Add(30 * 24 * time.Hour), 86400},
	}
	for _, tc := range fixedCases {
		t.Run("deterministic/"+tc.name, func(t *testing.T) {
			h := http.Header{}
			h.Set("Retry-After", tc.when.UTC().Format(http.TimeFormat))
			got, ok := retryAfterSecondsAt(h, tc.now)
			if !ok || got != tc.want {
				t.Errorf("retryAfterSecondsAt(%q, %v) = (%v, %v), want (%v, true)",
					h.Get("Retry-After"), tc.now, got, ok, tc.want)
			}
		})
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
		// Parity with jentic_one.mcp.execute.retry_safe (test_retry_safe).
		{"get", "", true},
		{"head", "", true},
		{http.MethodPost, "   ", false},
		{http.MethodPost, "\tidem-1\n", true},
		{http.MethodPut, "\t", false},
	}
	for _, tc := range cases {
		if got := RetrySafe(tc.method, tc.key); got != tc.want {
			t.Errorf("RetrySafe(%s, %q) = %v, want %v", tc.method, tc.key, got, tc.want)
		}
	}
}

// TestBrokerTransportRecoveryForParity is the shared routing table: the MCP
// mount's twin (test_broker_transport_recovery_parity in
// tests/unit/mcp/test_broker_transport_error.py) asserts the same rows, so the
// two lanes classify every broker problem type identically.
func TestBrokerTransportRecoveryForParity(t *testing.T) {
	cases := []struct {
		status      int
		problemType string
		want        BrokerTransportRecovery
	}{
		{503, "circuit_open", RecoverWait},
		{503, "runner_unavailable", RecoverWait},
		{503, "server_at_capacity", RecoverWait},
		{503, "too_many_candidates", RecoverWait},
		{502, "refresh_transient_error", RecoverWait},
		{504, "upstream_connect_failed", RecoverWait},
		{502, "upstream_connect_failed", RecoverWait},
		{429, "rate_limit_exceeded", RecoverWait},
		{429, "", RecoverWait},
		{504, "upstream_timeout", RecoverTimeout},
		{504, "deadline_exceeded", RecoverTimeout},
		{501, "runner_scheme_unsupported", RecoverScheme},
		{502, "upstream_response_too_large", RecoverUnknown},
		{502, "upstream_transport_error", RecoverUnknown},
		{500, "upstream_request_invalid", RecoverUnknown},
		{504, "about:blank", RecoverUnknown},
		{504, "", RecoverUnknown},
		{503, "about:blank", RecoverUnknown},
		{500, "streaming_unsupported", RecoverUnknown},
	}
	for _, tc := range cases {
		if got := BrokerTransportRecoveryFor(tc.status, tc.problemType); got != tc.want {
			t.Errorf("BrokerTransportRecoveryFor(%d, %q) = %q, want %q", tc.status, tc.problemType, got, tc.want)
		}
	}
}
