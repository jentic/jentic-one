package agentops

import (
	"net/http"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// brokerResult builds an ExecuteResult the way the broker renders its own
// problems (broker/core/problem.problem_body): the message rides `title`, there
// is no `detail` member, and the origin header says "broker".
func brokerResult(status int, origin, body string) *ExecuteResult {
	h := http.Header{}
	if origin != "" {
		h.Set("Jentic-Error-Origin", origin)
	}
	return &ExecuteResult{Status: status, Headers: h, Body: []byte(body)}
}

// TestBrokerError pins #1429: a broker-origin 4xx that is not a denial is a
// RESOLVE_FAILED (the call never reached the upstream), while upstream answers,
// a missing origin header, denials, the retryable broker 429 and broker 5xx keep
// their existing readings.
func TestBrokerError(t *testing.T) {
	cases := []struct {
		name string
		res  *ExecuteResult
		want bool
	}{
		// The repros from #1429, in the broker's real wire shape.
		{"unknown credential id", brokerResult(400, "broker",
			`{"type":"credential_id_not_found","title":"Credential id cred_nope is not among your credentials","status":400,"error_origin":"broker","candidates":[]}`), true},
		{"unknown credential name", brokerResult(400, "broker",
			`{"type":"credential_name_not_found","title":"No credential named zzz","status":400,"error_origin":"broker","candidates":[]}`), true},
		{"unregistered upstream", brokerResult(404, "broker",
			`{"type":"operation_not_found","title":"Operation not found — unregistered upstream URL.","status":404,"error_origin":"broker"}`), true},
		{"request validation", brokerResult(422, "broker",
			`{"type":"about:blank#validation","title":"Request validation failed","status":422,"error_origin":"broker","errors":[]}`), true},
		{"origin header is case and space tolerant", brokerResult(400, " Broker ", `{}`), true},

		{"upstream 400 is the caller's data", brokerResult(400, "upstream", `{"error":"bad request"}`), false},
		{"missing origin keeps pass-through", brokerResult(404, "", `{"error":"not found"}`), false},
		{"broker denial stays on the denial path", brokerResult(403, "broker", `{"type":"action_denied"}`), false},
		{"broker 429 is retryable, not a resolve failure", brokerResult(429, "broker",
			`{"type":"rate_limit_exceeded","title":"Rate limit exceeded; slow down and retry after the indicated delay.","status":429,"error_origin":"broker"}`), false},
		{"broker 5xx is out of scope", brokerResult(502, "broker", `{"type":"upstream_unreachable"}`), false},
		{"success", brokerResult(200, "", `{"ok":true}`), false},
		{"nil result", nil, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := BrokerError(tc.res)
			if (got != nil) != tc.want {
				t.Fatalf("BrokerError() = %v, want error: %v", got, tc.want)
			}
			if got == nil {
				return
			}
			if got.Code != ux.CodeResolveFailed || got.ExitCode() != ux.ExitDenied {
				t.Errorf("code=%q exit=%d, want RESOLVE_FAILED / exit 2", got.Code, got.ExitCode())
			}
			if got.Details["http_status"] != tc.res.Status || got.Details["origin"] != "broker" {
				t.Errorf("details = %v, want the broker status and origin", got.Details)
			}
		})
	}
}

func TestBrokerErrorCarriesTheProblemReason(t *testing.T) {
	// The broker's own bodies carry the message in `title` (no `detail`).
	got := BrokerError(brokerResult(400, "broker",
		`{"type":"credential_id_not_found","title":"Credential id cred_nope is not among your credentials","status":400}`))
	if got == nil || !strings.Contains(got.Msg, "cred_nope") {
		t.Fatalf("Msg must carry the broker's title, got %v", got)
	}
	if got.Details["problem_type"] != "credential_id_not_found" {
		t.Errorf("problem_type = %v", got.Details["problem_type"])
	}

	// A conformant RFC 9457 body that does set `detail` is preferred over title.
	got = BrokerError(brokerResult(400, "broker",
		`{"type":"credential_id_not_found","title":"Bad Request","detail":"credential cred_nope not found"}`))
	if got == nil || !strings.Contains(got.Msg, "credential cred_nope not found") {
		t.Fatalf("a string detail must win over the title, got %v", got)
	}

	// No title and no detail falls back to the HTTP status text.
	got = BrokerError(brokerResult(405, "broker", `{}`))
	if got == nil || !strings.Contains(got.Msg, "Method Not Allowed") {
		t.Fatalf("an empty problem must surface the status text, got %v", got)
	}
}

// TestBrokerErrorSurfacesValidationErrors pins the broker's 422 shape
// (broker/web/errors.handle_validation): the field errors ride `errors`, the
// title is only "Request validation failed", so the reason must name the field.
func TestBrokerErrorSurfacesValidationErrors(t *testing.T) {
	got := BrokerError(brokerResult(422, "broker",
		`{"type":"about:blank#validation","title":"Request validation failed","status":422,"error_origin":"broker",`+
			`"errors":[{"type":"missing","loc":["header","jentic-credential-id"],"msg":"field required"}]}`))
	if got == nil {
		t.Fatal("a broker validation 422 must classify as a broker error")
	}
	for _, want := range []string{"Request validation failed", "header.jentic-credential-id: field required"} {
		if !strings.Contains(got.Msg, want) {
			t.Errorf("Msg %q should contain %q", got.Msg, want)
		}
	}

	// A detail-array body (FastAPI's default shape, from a non-broker-rendered
	// route) is flattened the same way rather than dropped.
	got = BrokerError(brokerResult(422, "broker",
		`{"detail":[{"loc":["body","name"],"msg":"field required"}]}`))
	if got == nil || !strings.Contains(got.Msg, "body.name: field required") {
		t.Fatalf("array-form detail should surface its location and message, got %v", got)
	}
}

// TestBrokerErrorRelaysCandidates pins #1429 candidate relay: the broker embeds
// the caller's own covering credentials on an unknown credential id or name, so
// the classified error carries them in details["candidates"] and names them in
// the recovery step rather than forcing a follow-up whoami.
func TestBrokerErrorRelaysCandidates(t *testing.T) {
	got := BrokerError(brokerResult(400, "broker",
		`{"type":"credential_id_not_found","title":"Credential id cred_nope is not among your credentials",`+
			`"candidates":[{"id":"cred_a","name":"prod","last4":"ed_a","created_at":null},{"id":"cred_b","name":"staging","last4":"ed_b"}]}`))
	if got == nil {
		t.Fatal("want a classified broker error")
	}
	candidates, ok := got.Details["candidates"].([]any)
	if !ok || len(candidates) != 2 {
		t.Fatalf("details[candidates] = %v, want the two relayed candidates", got.Details["candidates"])
	}
	first, _ := candidates[0].(map[string]any)
	if first["id"] != "cred_a" || first["name"] != "prod" {
		t.Errorf("candidate[0] = %v, want the broker's record relayed verbatim", first)
	}
	if !strings.Contains(got.Actionable, "cred_a (prod), cred_b (staging)") {
		t.Errorf("actionable should name the candidates, got %q", got.Actionable)
	}

	// No candidates: no empty key, and the step falls back to listing them.
	bare := BrokerError(brokerResult(400, "broker", `{"type":"credential_id_not_found","title":"Bad Request","candidates":[]}`))
	if _, present := bare.Details["candidates"]; present {
		t.Errorf("a body with no candidates must not carry a candidates key: %v", bare.Details)
	}
	if !strings.Contains(bare.Actionable, "jentic creds list") {
		t.Errorf("without candidates the step should point at creds list, got %q", bare.Actionable)
	}
}

// TestBrokerErrorRecoveryForMatchesExactTypes pins the routing: exact problem
// types, never substrings, so a future type that merely mentions "credential"
// or "operation" lands on the contract path instead of being mis-routed.
func TestBrokerErrorRecoveryForMatchesExactTypes(t *testing.T) {
	cases := []struct {
		status int
		pt     string
		want   BrokerErrorRecovery
	}{
		{404, "operation_not_found", RecoverOperation},
		{404, "about:blank", RecoverOperation},
		{400, "operation_not_found", RecoverOperation},
		{400, "credential_id_not_found", RecoverCredential},
		{400, "credential_name_not_found", RecoverCredential},
		{400, "invalid_upstream_url", RecoverContract},
		{422, "unknown_revision_pin", RecoverContract},
		{422, "about:blank#validation", RecoverContract},
		{428, "mutation_requires_idempotency_key", RecoverContract},
		{400, "credential_header_malformed", RecoverContract},
		{405, "operation_method_mismatch", RecoverContract},
	}
	for _, tc := range cases {
		if got := BrokerErrorRecoveryFor(tc.status, tc.pt); got != tc.want {
			t.Errorf("BrokerErrorRecoveryFor(%d, %q) = %q, want %q", tc.status, tc.pt, got, tc.want)
		}
	}
}

// TestBrokerErrorActionableBranches pins the branched recovery advice: a
// 404/operation error points at discovery, a credential error at the bindings,
// and any other 4xx at the operation contract — never a one-size-fits-all hint
// that is noise for the other cases.
func TestBrokerErrorActionableBranches(t *testing.T) {
	cases := []struct {
		name           string
		status         int
		body           string
		wantSubstrings []string
		notSubstrings  []string
	}{
		{
			"unregistered operation", 404,
			`{"type":"operation_not_found","title":"no operation"}`,
			[]string{"registered", "jentic search"},
			[]string{"creds list", "apis inspect"},
		},
		{
			"credential error", 400,
			`{"type":"credential_id_not_found","title":"bad id"}`,
			[]string{"creds list", "Jentic-Credential"},
			[]string{"jentic search", "apis inspect"},
		},
		{
			"contract error", 413,
			`{"type":"payload_too_large","title":"too big"}`,
			[]string{"apis inspect"},
			[]string{"creds list", "jentic search"},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := BrokerError(brokerResult(tc.status, "broker", tc.body))
			if got == nil {
				t.Fatalf("want a classified broker error for %s", tc.name)
			}
			for _, want := range tc.wantSubstrings {
				if !strings.Contains(got.Actionable, want) {
					t.Errorf("actionable %q should contain %q", got.Actionable, want)
				}
			}
			for _, notWant := range tc.notSubstrings {
				if strings.Contains(got.Actionable, notWant) {
					t.Errorf("actionable %q should not contain the noise %q", got.Actionable, notWant)
				}
			}
		})
	}
}
