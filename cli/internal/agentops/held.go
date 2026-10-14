package agentops

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"strings"
)

// Held is the broker's 202 answer to a call a require-approval rule held for
// a human: the held execution, the review page a person opens to approve or
// deny it, and the directive telling the agent what to do next.
type Held struct {
	Status          string    `json:"status"`
	HeldExecutionID string    `json:"held_execution_id"`
	ReviewURL       string    `json:"review_url"`
	ExpiresAt       string    `json:"expires_at"`
	AgentDirective  string    `json:"agent_directive"`
	Links           HeldLinks `json:"_links"`
}

// HeldLinks are a held envelope's links: the held execution itself, its
// eventual result, and where to withdraw it.
type HeldLinks struct {
	Self     string `json:"self"`
	Result   string `json:"result"`
	Withdraw string `json:"withdraw"`
}

// Execution statuses of a held execution (the control plane's
// HeldExecution execution_status): where the held call stands.
const (
	HeldStatusHeld      = "held"
	HeldStatusQueued    = "queued"
	HeldStatusRunning   = "running"
	HeldStatusCompleted = "completed"
	HeldStatusFailed    = "failed"
	HeldStatusCancelled = "cancelled"
)

// ParseHeld reports whether a broker result is a held call: a 202 whose body
// is the held envelope (status "held", a held execution id and a review URL).
// Any other 202 — a plain respond-async job — is not held.
func ParseHeld(r *ExecuteResult) (*Held, bool) {
	if r == nil || r.Status != http.StatusAccepted {
		return nil, false
	}
	var h Held
	if json.Unmarshal(r.Body, &h) != nil {
		return nil, false
	}
	if h.Status != HeldStatusHeld || h.HeldExecutionID == "" || h.ReviewURL == "" {
		return nil, false
	}
	return &h, true
}

// IsFinishedHeldStatus reports whether a held call's execution status is
// final: it ran (completed or failed) or will never run (cancelled).
func IsFinishedHeldStatus(status string) bool {
	switch status {
	case HeldStatusCompleted, HeldStatusFailed, HeldStatusCancelled:
		return true
	}
	return false
}

// HasHeldResult reports whether a held call's execution status carries a
// result to collect (GET /executions/holds/{id}/result).
func HasHeldResult(status string) bool {
	return status == HeldStatusCompleted || status == HeldStatusFailed
}

// executionResultDoc is the result document of a held execution: the
// upstream status and body (base64) of a run, or the problem body of a call
// that never reached the upstream (a run-time denial).
type executionResultDoc struct {
	ExecutionID string          `json:"execution_id"`
	HTTPStatus  *int            `json:"http_status"`
	BodyB64     string          `json:"body_b64"`
	Problem     json.RawMessage `json:"problem"`
	// A held call that was denied or whose approval expired has the problem
	// body itself as its result: these are its members. Status is raw because
	// a run's document carries its execution status (a string) there.
	Type   string          `json:"type"`
	Status json.RawMessage `json:"status"`
}

// problemContentType is the media type of a broker problem body.
const problemContentType = "application/problem+json"

// ExecutionResultFromHeld turns a held execution's result
// (GET /executions/holds/{id}/result: its body and Content-Type) into the
// ExecuteResult a synchronous call would have produced, so a waited-for held
// call renders and classifies exactly like one that ran at once:
//
//   - a run: the upstream status and decoded body, under the upstream
//     Content-Type, stamped Jentic-Error-Origin "upstream" on an error status
//     like the broker stamps a mirrored upstream error;
//   - a call that never ran (a denied, expired or unresumable hold, or a
//     run-time denial): the problem body at its own status, stamped origin
//     "broker", so Classify reads it as the broker denial it is.
//
// It reports false when the document is neither (a run that failed before
// the upstream answered carries no status).
func ExecutionResultFromHeld(contentType string, body []byte, executionID string) (*ExecuteResult, bool) {
	var doc executionResultDoc
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
// "problem" (a run-time denial) or the document itself (a hold's
// decision) — and its status.
func problemOf(doc executionResultDoc, body []byte) (json.RawMessage, int, bool) {
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
