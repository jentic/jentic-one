package agentops

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"strings"
)

// Held is the broker's 202 answer to a call a require-approval rule held for
// a human: the held job, its approval (with the review page a person opens to
// approve or deny it) and the directive telling the agent what to do next.
type Held struct {
	JobID          string       `json:"job_id"`
	Status         string       `json:"status"`
	Approval       HeldApproval `json:"approval"`
	AgentDirective string       `json:"agent_directive"`
}

// HeldApproval is the approval sub-object of a held envelope.
type HeldApproval struct {
	ID        string `json:"id"`
	ReviewURL string `json:"review_url"`
	ExpiresAt string `json:"expires_at"`
}

// heldJobStatus is the held envelope's (and the held job's) status value.
const heldJobStatus = "held"

// ParseHeld reports whether a broker result is a held call: a 202 whose body
// is the held envelope (status "held", a job id and an approval carrying a
// review URL). Any other 202 — a plain respond-async job — is not held.
func ParseHeld(r *ExecuteResult) (*Held, bool) {
	if r == nil || r.Status != http.StatusAccepted {
		return nil, false
	}
	var h Held
	if json.Unmarshal(r.Body, &h) != nil {
		return nil, false
	}
	if h.Status != heldJobStatus || h.JobID == "" || h.Approval.ReviewURL == "" {
		return nil, false
	}
	return &h, true
}

// Job statuses a polled job settles in (the control plane's JobStatus).
const (
	JobCompleted  = "completed"
	JobFailed     = "failed"
	JobCancelled  = "cancelled"
	JobDeadLetter = "dead_letter"
)

// IsTerminalJobStatus reports whether a job status is final.
func IsTerminalJobStatus(status string) bool {
	switch status {
	case JobCompleted, JobFailed, JobCancelled, JobDeadLetter:
		return true
	}
	return false
}

// IsHeldJobStatus reports whether a job is still waiting for its approval.
func IsHeldJobStatus(status string) bool { return status == heldJobStatus }

// executionJobResult is the result document an execution job stores: the
// upstream status and body (base64) of a run, or the problem body of a call
// that never reached the upstream (a run-time denial).
type executionJobResult struct {
	ExecutionID string          `json:"execution_id"`
	HTTPStatus  *int            `json:"http_status"`
	BodyB64     string          `json:"body_b64"`
	Problem     json.RawMessage `json:"problem"`
	// A held job that was denied or whose approval expired stores the problem
	// body itself as its result: these are its members. Status is raw because
	// a run's document carries its execution status (a string) there.
	Type   string          `json:"type"`
	Status json.RawMessage `json:"status"`
}

// problemContentType is the media type of a broker problem body.
const problemContentType = "application/problem+json"

// ExecutionResultFromJob turns an execution job's result document
// (GET /jobs/{id}/result: its body and Content-Type) into the ExecuteResult a
// synchronous call would have produced, so a waited-for held call renders and
// classifies exactly like one that ran at once:
//
//   - a run: the upstream status and decoded body, under the upstream
//     Content-Type, stamped Jentic-Error-Origin "upstream" on an error status
//     like the broker stamps a mirrored upstream error;
//   - a call that never ran (a denied, expired or unresumable approval, or a
//     run-time denial): the problem body at its own status, stamped origin
//     "broker", so Classify reads it as the broker denial it is.
//
// It reports false when the document is neither (a run that failed before
// the upstream answered carries no status).
func ExecutionResultFromJob(contentType string, body []byte, executionID string) (*ExecuteResult, bool) {
	var doc executionJobResult
	if json.Unmarshal(body, &doc) != nil {
		return nil, false
	}
	if doc.ExecutionID != "" {
		executionID = doc.ExecutionID
	}
	if problem, status, ok := problemOf(doc, body); ok {
		h := http.Header{}
		h.Set("Content-Type", problemContentType)
		h.Set("Jentic-Error-Origin", errorOriginBroker)
		return &ExecuteResult{Status: status, Headers: h, Body: problem, ExecutionID: executionID}, true
	}
	if doc.HTTPStatus == nil {
		return nil, false
	}
	upstream, err := base64.StdEncoding.DecodeString(doc.BodyB64)
	if err != nil {
		return nil, false
	}
	h := http.Header{}
	if ct := strings.TrimSpace(contentType); ct != "" && len(upstream) > 0 {
		h.Set("Content-Type", ct)
	}
	if *doc.HTTPStatus >= http.StatusBadRequest {
		h.Set("Jentic-Error-Origin", errorOriginUpstream)
	}
	return &ExecuteResult{Status: *doc.HTTPStatus, Headers: h, Body: upstream, ExecutionID: executionID}, true
}

// problemOf returns the problem body a result document carries — nested under
// "problem" (a run-time denial) or the document itself (a held job's
// decision) — and its status.
func problemOf(doc executionJobResult, body []byte) (json.RawMessage, int, bool) {
	if len(doc.Problem) > 0 && string(doc.Problem) != "null" {
		var p struct {
			Status int `json:"status"`
		}
		if json.Unmarshal(doc.Problem, &p) != nil || p.Status == 0 {
			return nil, 0, false
		}
		return doc.Problem, p.Status, true
	}
	var status int
	if doc.Type != "" && doc.HTTPStatus == nil && json.Unmarshal(doc.Status, &status) == nil && status != 0 {
		return body, status, true
	}
	return nil, 0, false
}
