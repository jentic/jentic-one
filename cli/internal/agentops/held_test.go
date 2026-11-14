package agentops

import (
	"encoding/base64"
	"net/http"
	"testing"
)

const testHeldBody = `{"status":"held","held_execution_id":"held_1","review_url":"http://ui/r","expires_at":"x",` +
	`"agent_directive":"d","_links":{"self":"http://ui/executions/holds/held_1",` +
	`"result":"http://ui/executions/holds/held_1/result","withdraw":"http://ui/executions/holds/held_1:withdraw"}}`

func TestParseHeld(t *testing.T) {
	held := testHeldBody
	cases := []struct {
		name   string
		status int
		body   string
		want   bool
	}{
		{"the held envelope", http.StatusAccepted, held, true},
		{"a plain respond-async job", http.StatusAccepted, `{"job_id":"job_1","status":"queued"}`, false},
		{"held without a review link", http.StatusAccepted, `{"status":"held","held_execution_id":"held_1"}`, false},
		{"held without an id", http.StatusAccepted, `{"status":"held","review_url":"http://ui/r"}`, false},
		{"held but not a 202", http.StatusOK, held, false},
		{"not JSON", http.StatusAccepted, `held`, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h, ok := ParseHeld(&ExecuteResult{Status: tc.status, Body: []byte(tc.body)})
			if ok != tc.want {
				t.Fatalf("ParseHeld = %v, want %v", ok, tc.want)
			}
			if ok && (h.HeldExecutionID != "held_1" || h.ReviewURL != "http://ui/r" ||
				h.Links.Result != "http://ui/executions/holds/held_1/result") {
				t.Errorf("held = %+v", h)
			}
		})
	}
	if _, ok := ParseHeld(nil); ok {
		t.Error("nil is not held")
	}
}

func TestParseHeld_RejectsMirroredUpstream202(t *testing.T) {
	headers := http.Header{"Jentic-Upstream-Status": []string{"202"}}
	if _, ok := ParseHeld(&ExecuteResult{Status: http.StatusAccepted, Headers: headers, Body: []byte(testHeldBody)}); ok {
		t.Fatal("a mirrored upstream 202 must remain an upstream response, not a broker hold")
	}
}

func TestParseHeld_RejectsUpstreamOrigin(t *testing.T) {
	headers := http.Header{"Jentic-Error-Origin": []string{"upstream"}}
	if _, ok := ParseHeld(&ExecuteResult{Status: http.StatusAccepted, Headers: headers, Body: []byte(testHeldBody)}); ok {
		t.Fatal("an upstream-origin 202 must not become a broker hold")
	}
}

func TestParseHeld_AcceptsBrokerHeld(t *testing.T) {
	headers := http.Header{"Preference-Applied": []string{"respond-async"}}
	held, ok := ParseHeld(&ExecuteResult{Status: http.StatusAccepted, Headers: headers, Body: []byte(testHeldBody)})
	if !ok || held.HeldExecutionID != "held_1" {
		t.Fatalf("ParseHeld = %+v, %v, want the broker hold", held, ok)
	}
}

func TestIsBrokerHeld(t *testing.T) {
	for _, tc := range []struct {
		name   string
		result *ExecuteResult
		want   bool
	}{
		{name: "nil", result: nil, want: false},
		{name: "broker held", result: &ExecuteResult{Status: http.StatusAccepted}, want: true},
		{name: "other status", result: &ExecuteResult{Status: http.StatusOK}, want: false},
		{name: "mirrored upstream", result: &ExecuteResult{Status: http.StatusAccepted, Headers: http.Header{"Jentic-Upstream-Status": []string{"202"}}}, want: false},
		{name: "upstream origin", result: &ExecuteResult{Status: http.StatusAccepted, Headers: http.Header{"Jentic-Error-Origin": []string{"upstream"}}}, want: false},
		{name: "broker origin", result: &ExecuteResult{Status: http.StatusAccepted, Headers: http.Header{"Jentic-Error-Origin": []string{"broker"}}}, want: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := IsBrokerHeld(tc.result); got != tc.want {
				t.Errorf("IsBrokerHeld = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestTrustedReviewURL(t *testing.T) {
	for _, tc := range []struct {
		name       string
		reviewURL  string
		controlURL string
		brokerURL  string
		want       bool
	}{
		{name: "matching control base", reviewURL: "https://jentic.corp/app/review", controlURL: "https://jentic.corp/api", want: true},
		{name: "matching broker", reviewURL: "https://broker.corp/review", controlURL: "https://control.corp", brokerURL: "https://broker.corp", want: true},
		{name: "default port equivalence", reviewURL: "https://JENTIC.corp:443/review", controlURL: "https://jentic.corp", want: true},
		{name: "loopback http", reviewURL: "http://127.0.0.1:8080/review", brokerURL: "http://127.0.0.1:8080", want: true},
		{name: "other origin", reviewURL: "https://other.corp/review", controlURL: "https://jentic.corp", want: false},
		{name: "userinfo", reviewURL: "https://jentic.corp@evil.corp/review", controlURL: "https://evil.corp", want: false},
		{name: "http non-loopback", reviewURL: "http://jentic.corp/review", controlURL: "http://jentic.corp", want: false},
		{name: "relative", reviewURL: "/review", controlURL: "https://jentic.corp", want: false},
		{name: "malformed", reviewURL: "://bad", controlURL: "https://jentic.corp", want: false},
		{name: "no configured origin", reviewURL: "https://jentic.corp/review", want: false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := TrustedReviewURL(tc.reviewURL, tc.controlURL, tc.brokerURL); got != tc.want {
				t.Errorf("TrustedReviewURL(%q, %q, %q) = %v, want %v", tc.reviewURL, tc.controlURL, tc.brokerURL, got, tc.want)
			}
		})
	}
}

func TestExecutionResultFromHeld(t *testing.T) {
	b64 := base64.StdEncoding.EncodeToString
	t.Run("a run mirrors the upstream status and body", func(t *testing.T) {
		doc := `{"execution_id":"exec_1","status":"completed","http_status":200,"body_b64":"` + b64([]byte(`{"a":1}`)) + `"}`
		r, ok := ExecutionResultFromHeld("application/json", []byte(doc), "")
		if !ok || r.Status != 200 || string(r.Body) != `{"a":1}` || r.ExecutionID != "exec_1" {
			t.Fatalf("result = %+v, %v", r, ok)
		}
		if r.Headers.Get("Content-Type") != "application/json" || r.Headers.Get("Jentic-Error-Origin") != "" {
			t.Errorf("headers = %v", r.Headers)
		}
		if Classify(r) != nil {
			t.Error("a 2xx run is not a denial")
		}
	})
	t.Run("an upstream error is stamped upstream, not a denial", func(t *testing.T) {
		doc := `{"execution_id":"exec_1","status":"failed","http_status":403,"body_b64":"` + b64([]byte(`no`)) + `"}`
		r, ok := ExecutionResultFromHeld("text/plain", []byte(doc), "")
		if !ok || r.Status != 403 || r.Headers.Get("Jentic-Error-Origin") != "upstream" || Classify(r) != nil {
			t.Fatalf("result = %+v, %v", r, ok)
		}
	})
	t.Run("an empty upstream body carries no content type", func(t *testing.T) {
		r, ok := ExecutionResultFromHeld("application/json", []byte(`{"status":"completed","http_status":204}`), "exec_2")
		if !ok || r.Status != 204 || len(r.Body) != 0 || r.Headers.Get("Content-Type") != "" || r.ExecutionID != "exec_2" {
			t.Fatalf("result = %+v, %v", r, ok)
		}
	})
	t.Run("a held decision's problem is a broker denial", func(t *testing.T) {
		doc := `{"type":"approval_expired","title":"t","status":403,"error_origin":"broker"}`
		r, ok := ExecutionResultFromHeld("application/problem+json", []byte(doc), "exec_3")
		d := Classify(r)
		if !ok || r.Status != 403 || d == nil || d.ProblemType != "approval_expired" {
			t.Fatalf("result = %+v, %v, denial %+v", r, ok, d)
		}
	})
	t.Run("a run-time denial's nested problem is a broker denial", func(t *testing.T) {
		doc := `{"execution_id":"exec_4","status":"failed","http_status":409,"problem":{"type":"approval_resume_failed","status":409}}`
		r, ok := ExecutionResultFromHeld("application/json", []byte(doc), "")
		d := Classify(r)
		if !ok || r.Status != 409 || d == nil || d.ProblemType != "approval_resume_failed" || r.ExecutionID != "exec_4" {
			t.Fatalf("result = %+v, %v, denial %+v", r, ok, d)
		}
	})
	t.Run("a run with no upstream answer has no result", func(t *testing.T) {
		if _, ok := ExecutionResultFromHeld("", []byte(`{"status":"failed","http_status":null}`), ""); ok {
			t.Error("want false")
		}
		if _, ok := ExecutionResultFromHeld("", []byte(`not json`), ""); ok {
			t.Error("want false")
		}
	})
}

func TestHeldStatuses(t *testing.T) {
	for _, s := range []string{HeldStatusCompleted, HeldStatusFailed, HeldStatusCancelled} {
		if !IsFinishedHeldStatus(s) {
			t.Errorf("%s is finished", s)
		}
	}
	for _, s := range []string{HeldStatusHeld, HeldStatusQueued, HeldStatusRunning, ""} {
		if IsFinishedHeldStatus(s) {
			t.Errorf("%q is not finished", s)
		}
	}
	if !HasHeldResult(HeldStatusCompleted) || !HasHeldResult(HeldStatusFailed) || HasHeldResult(HeldStatusCancelled) {
		t.Error("HasHeldResult")
	}
}
