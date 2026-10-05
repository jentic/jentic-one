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
	got := BrokerError(&ExecuteResult{Status: 400, Headers: h,
		Body: []byte(`{"type":"credential_id_not_found","title":"Bad Request","detail":"credential cred_nope not found"}`)})
	if got == nil || !strings.Contains(got.Msg, "credential cred_nope not found") {
		t.Fatalf("Msg must carry the broker's detail, got %v", got)
	}
	if got.Details["problem_type"] != "credential_id_not_found" {
		t.Errorf("problem_type = %v", got.Details["problem_type"])
	}

	// FastAPI's array-form 422 detail must not break classification.
	got = BrokerError(&ExecuteResult{Status: 422, Headers: h,
		Body: []byte(`{"title":"Unprocessable Entity","detail":[{"loc":["header"],"msg":"invalid"}]}`)})
	if got == nil || !strings.Contains(got.Msg, "Unprocessable Entity") {
		t.Fatalf("array-form detail should fall back to the title, got %v", got)
	}
}
