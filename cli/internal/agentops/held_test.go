package agentops

import (
	"encoding/base64"
	"net/http"
	"testing"
)

func TestParseHeld(t *testing.T) {
	held := `{"job_id":"job_1","status":"held","approval":{"id":"apr_1","review_url":"http://ui/r","expires_at":"x"}}`
	cases := []struct {
		name   string
		status int
		body   string
		want   bool
	}{
		{"the held envelope", http.StatusAccepted, held, true},
		{"a plain respond-async job", http.StatusAccepted, `{"job_id":"job_1","status":"queued"}`, false},
		{"held without a review link", http.StatusAccepted, `{"job_id":"job_1","status":"held","approval":{}}`, false},
		{"held but not a 202", http.StatusOK, held, false},
		{"not JSON", http.StatusAccepted, `held`, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h, ok := ParseHeld(&ExecuteResult{Status: tc.status, Body: []byte(tc.body)})
			if ok != tc.want {
				t.Fatalf("ParseHeld = %v, want %v", ok, tc.want)
			}
			if ok && (h.JobID != "job_1" || h.Approval.ReviewURL != "http://ui/r" || h.Approval.ID != "apr_1") {
				t.Errorf("held = %+v", h)
			}
		})
	}
	if _, ok := ParseHeld(nil); ok {
		t.Error("nil is not held")
	}
}

func TestExecutionResultFromJob(t *testing.T) {
	b64 := base64.StdEncoding.EncodeToString
	t.Run("a run mirrors the upstream status and body", func(t *testing.T) {
		doc := `{"execution_id":"exec_1","status":"completed","http_status":200,"body_b64":"` + b64([]byte(`{"a":1}`)) + `"}`
		r, ok := ExecutionResultFromJob("application/json", []byte(doc), "")
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
		r, ok := ExecutionResultFromJob("text/plain", []byte(doc), "")
		if !ok || r.Status != 403 || r.Headers.Get("Jentic-Error-Origin") != "upstream" || Classify(r) != nil {
			t.Fatalf("result = %+v, %v", r, ok)
		}
	})
	t.Run("an empty upstream body carries no content type", func(t *testing.T) {
		r, ok := ExecutionResultFromJob("application/json", []byte(`{"status":"completed","http_status":204}`), "exec_2")
		if !ok || r.Status != 204 || len(r.Body) != 0 || r.Headers.Get("Content-Type") != "" || r.ExecutionID != "exec_2" {
			t.Fatalf("result = %+v, %v", r, ok)
		}
	})
	t.Run("a held decision's problem is a broker denial", func(t *testing.T) {
		doc := `{"type":"approval_expired","title":"t","status":403,"error_origin":"broker"}`
		r, ok := ExecutionResultFromJob("application/problem+json", []byte(doc), "exec_3")
		d := Classify(r)
		if !ok || r.Status != 403 || d == nil || d.ProblemType != "approval_expired" {
			t.Fatalf("result = %+v, %v, denial %+v", r, ok, d)
		}
	})
	t.Run("a run-time denial's nested problem is a broker denial", func(t *testing.T) {
		doc := `{"execution_id":"exec_4","status":"failed","http_status":409,"problem":{"type":"approval_resume_failed","status":409}}`
		r, ok := ExecutionResultFromJob("application/json", []byte(doc), "")
		d := Classify(r)
		if !ok || r.Status != 409 || d == nil || d.ProblemType != "approval_resume_failed" || r.ExecutionID != "exec_4" {
			t.Fatalf("result = %+v, %v, denial %+v", r, ok, d)
		}
	})
	t.Run("a run with no upstream answer has no result", func(t *testing.T) {
		if _, ok := ExecutionResultFromJob("", []byte(`{"status":"failed","http_status":null}`), ""); ok {
			t.Error("want false")
		}
		if _, ok := ExecutionResultFromJob("", []byte(`not json`), ""); ok {
			t.Error("want false")
		}
	})
}

func TestJobStatuses(t *testing.T) {
	for _, s := range []string{JobCompleted, JobFailed, JobCancelled, JobDeadLetter} {
		if !IsTerminalJobStatus(s) {
			t.Errorf("%s is terminal", s)
		}
	}
	for _, s := range []string{"queued", "running", "held"} {
		if IsTerminalJobStatus(s) {
			t.Errorf("%s is not terminal", s)
		}
	}
	if !IsHeldJobStatus("held") || IsHeldJobStatus("queued") {
		t.Error("IsHeldJobStatus")
	}
}
