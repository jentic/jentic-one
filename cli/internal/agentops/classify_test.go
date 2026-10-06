package agentops

import (
	"net/http"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// TestBrokerError pins #1429: a broker-origin 4xx that is not a denial is a
// RESOLVE_FAILED (the call never reached the upstream), while upstream answers,
// a missing origin header, denials and broker 5xx keep their existing readings.
func TestBrokerError(t *testing.T) {
	result := func(status int, origin, body string) *ExecuteResult {
		h := http.Header{}
		if origin != "" {
			h.Set("Jentic-Error-Origin", origin)
		}
		return &ExecuteResult{Status: status, Headers: h, Body: []byte(body)}
	}
	cases := []struct {
		name string
		res  *ExecuteResult
		want bool
	}{
		// The four repros from #1429.
		{"unknown credential id", result(400, "broker", `{"type":"credential_id_not_found","detail":"credential cred_nope not found"}`), true},
		{"unbound credential id", result(400, "broker", `{"type":"credential_not_bound","detail":"credential not bound to this operation"}`), true},
		{"unknown credential name", result(400, "broker", `{"type":"credential_name_not_found","detail":"no credential named zzz"}`), true},
		{"unregistered upstream", result(404, "broker", `{"type":"operation_not_found","detail":"no operation registered for this upstream"}`), true},
		{"origin header is case and space tolerant", result(400, " Broker ", `{}`), true},

		{"upstream 400 is the caller's data", result(400, "upstream", `{"error":"bad request"}`), false},
		{"missing origin keeps pass-through", result(404, "", `{"error":"not found"}`), false},
		{"broker denial stays on the denial path", result(403, "broker", `{"type":"action_denied"}`), false},
		{"broker 5xx is out of scope", result(502, "broker", `{"type":"upstream_unreachable"}`), false},
		{"success", result(200, "", `{"ok":true}`), false},
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

func TestBrokerErrorCarriesTheProblemDetail(t *testing.T) {
	h := http.Header{}
	h.Set("Jentic-Error-Origin", "broker")
	got := BrokerError(&ExecuteResult{
		Status:  400,
		Headers: h,
		Body:    []byte(`{"type":"credential_id_not_found","title":"Bad Request","detail":"credential cred_nope not found"}`),
	})
	if got == nil || !strings.Contains(got.Msg, "credential cred_nope not found") {
		t.Fatalf("Msg must carry the broker's detail, got %v", got)
	}
	if got.Details["problem_type"] != "credential_id_not_found" {
		t.Errorf("problem_type = %v", got.Details["problem_type"])
	}

	// A broker problem with NO detail (its own bodies carry title, not detail —
	// problem_body stamps title=detail) must still surface the title.
	got = BrokerError(&ExecuteResult{
		Status:  404,
		Headers: h,
		Body:    []byte(`{"type":"operation_not_found","title":"no operation registered for this upstream"}`),
	})
	if got == nil || !strings.Contains(got.Msg, "no operation registered for this upstream") {
		t.Fatalf("a title-only problem must surface the title, got %v", got)
	}

	// FastAPI's array-form 422 detail must be flattened to its validation
	// messages, not swallowed in favor of the status text.
	got = BrokerError(&ExecuteResult{
		Status:  422,
		Headers: h,
		Body:    []byte(`{"detail":[{"loc":["header","jentic-credential-id"],"msg":"field required"}]}`),
	})
	if got == nil {
		t.Fatal("array-form detail must still classify as a broker error")
	}
	if !strings.Contains(got.Msg, "field required") {
		t.Errorf("array-form detail should surface the validation message, got %q", got.Msg)
	}
	if !strings.Contains(got.Msg, "header.jentic-credential-id") {
		t.Errorf("array-form detail should surface the error location, got %q", got.Msg)
	}
}

// TestBrokerErrorRelaysCandidates pins #1429 candidate relay: the broker embeds
// the caller's own covering credentials on an unknown/unbound credential id or
// name, so the classified error must carry them in details["candidates"] rather
// than forcing the agent into a follow-up whoami to learn which ids exist.
func TestBrokerErrorRelaysCandidates(t *testing.T) {
	h := http.Header{}
	h.Set("Jentic-Error-Origin", "broker")
	got := BrokerError(&ExecuteResult{
		Status:  400,
		Headers: h,
		Body: []byte(`{"type":"credential_id_not_found","title":"Bad Request",` +
			`"candidates":[{"id":"cred_a","name":"prod","last4":"1234"},{"id":"cred_b","name":"staging","last4":"5678"}]}`),
	})
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
	// A problem body with no candidates must not stamp an empty key.
	bare := BrokerError(&ExecuteResult{
		Status:  400,
		Headers: h,
		Body:    []byte(`{"type":"credential_id_not_found","title":"Bad Request"}`),
	})
	if _, present := bare.Details["candidates"]; present {
		t.Errorf("a body with no candidates must not carry a candidates key: %v", bare.Details)
	}
}

// TestBrokerErrorActionableBranchesOnStatus pins the branched recovery advice:
// a 404/operation error points at discovery, a credential error at the bindings,
// and any other 4xx at the operation contract — never a one-size-fits-all hint
// that is noise for the other cases.
func TestBrokerErrorActionableBranchesOnStatus(t *testing.T) {
	h := http.Header{}
	h.Set("Jentic-Error-Origin", "broker")
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
			[]string{"registered", "apis list"},
			[]string{"creds list"},
		},
		{
			"credential error", 400,
			`{"type":"credential_id_not_found","title":"bad id"}`,
			[]string{"creds list", "Jentic-Credential"},
			[]string{"apis list"},
		},
		{
			"contract error", 413,
			`{"type":"payload_too_large","title":"too big"}`,
			[]string{"inspect"},
			[]string{"creds list", "apis list"},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := BrokerError(&ExecuteResult{Status: tc.status, Headers: h, Body: []byte(tc.body)})
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
