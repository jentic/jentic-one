package api

// mcp_held.go shapes a held (require-approval) execute for the MCP client,
// per request, from the capabilities the client declares on THIS call (the
// `_meta` clientCapabilities of a 2026-07-28 request, else the legacy
// initialize params):
//
//   - elicitation.url declared → a URL-mode elicitation of the review page as
//     a multi-round-trip input-required result, carrying an HMAC-sealed
//     requestState. The retry never re-sends the call: it reads the job
//     (terminal → the result; still held → the short wait, then the held
//     result). The elicitation accept is consent to open the link, never a
//     decision — only a signed-in reviewer's :decide releases the job.
//   - otherwise → the held result at once: the broker's envelope as a normal
//     tool result, so the model relays the review link straight away and
//     polls with get_execution_result. Nobody can approve a call before seeing
//     its review link, so waiting on the first call would only delay the link.
//
// The short wait (poll the job up to heldWaitBudget) runs only on the
// URL-elicitation retry, after the client has sent the user to the review
// page.
//
// Form-mode elicitation is never used for approvals. No task front door is
// served, so a client declaring the Tasks extension gets the URL elicitation
// (with elicitation.url) or the held result. Kept in parity
// with the mounted app's src/jentic_one/mcp/approvals.py.

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const (
	// defaultHeldWaitBudget is the short server-side wait before a held
	// result: inside a host's tool-call timeout, long enough for a reviewer
	// who is already looking.
	defaultHeldWaitBudget = 30 * time.Second
	heldWaitPoll          = 2 * time.Second
	// reviewInputKey is the inputRequests key of the review-page elicitation.
	reviewInputKey = "review_approval"
	// heldStateTTL bounds how long a sealed requestState is accepted.
	heldStateTTL = 10 * time.Minute

	frontDoorURLElicitation = "url_elicitation"
	frontDoorHeldResult     = "held_result"
)

// heldAgentDirective mirrors the broker's directive for a held call.
const heldAgentDirective = "This call needs human approval. Show the user the review_url, then call " +
	"get_execution_result once with job_id and wait_seconds: 30. If it is still held, " +
	"tell the user it is waiting for approval and end your turn; call " +
	"get_execution_result again when they return. Do not re-send the call."

// heldApproval is the approval sub-object of the broker's held envelope.
type heldApproval struct {
	ID        string `json:"id"`
	ReviewURL string `json:"review_url"`
	ExpiresAt string `json:"expires_at"`
}

// heldState is the sealed requestState of a URL-elicitation round trip.
type heldState struct {
	JobID    string         `json:"job_id"`
	Agent    string         `json:"agent"`
	Approval heldApproval   `json:"approval"`
	Links    map[string]any `json:"links,omitempty"`
	Exp      int64          `json:"exp"`
}

var (
	heldStateKeyOnce sync.Once
	heldStateKey     []byte
)

// heldKey is the per-process HMAC key for requestState: the stdio server is
// one process serving one client, so state never needs to outlive it.
func heldKey() []byte {
	heldStateKeyOnce.Do(func() {
		heldStateKey = make([]byte, 32)
		if _, err := rand.Read(heldStateKey); err != nil {
			panic(fmt.Errorf("failed to seed the requestState key: %w", err))
		}
	})
	return heldStateKey
}

// heldEnvelope returns the broker's held envelope from a relayed 202 body, or
// nil for any other response.
func heldEnvelope(status int, body any) map[string]any {
	if status != 202 {
		return nil
	}
	m, ok := body.(map[string]any)
	if !ok {
		return nil
	}
	if st, _ := m["status"].(string); st != "held" {
		return nil
	}
	if _, ok := m["job_id"].(string); !ok {
		return nil
	}
	approval, ok := m["approval"].(map[string]any)
	if !ok {
		return nil
	}
	if _, ok := approval["review_url"].(string); !ok {
		return nil
	}
	return m
}

// heldFrontDoor picks the door the request's declared capabilities allow.
func heldFrontDoor(caps *mcp.ClientCapabilities) string {
	if caps != nil && caps.Elicitation != nil && caps.Elicitation.URL != nil {
		return frontDoorURLElicitation
	}
	return frontDoorHeldResult
}

func sealHeldState(st heldState) (string, error) {
	raw, err := json.Marshal(st)
	if err != nil {
		return "", fmt.Errorf("failed to encode requestState: %w", err)
	}
	mac := hmac.New(sha256.New, heldKey())
	mac.Write(raw)
	return base64.RawURLEncoding.EncodeToString(raw) + "." +
		base64.RawURLEncoding.EncodeToString(mac.Sum(nil)), nil
}

// openHeldState verifies a requestState; it fails for a forged, expired, or
// another identity's state.
func openHeldState(token, agent string) (*heldState, error) {
	body, sig, ok := strings.Cut(token, ".")
	if !ok {
		return nil, errors.New("malformed requestState")
	}
	raw, err := base64.RawURLEncoding.DecodeString(body)
	if err != nil {
		return nil, errors.New("malformed requestState")
	}
	got, err := base64.RawURLEncoding.DecodeString(sig)
	if err != nil {
		return nil, errors.New("malformed requestState")
	}
	mac := hmac.New(sha256.New, heldKey())
	mac.Write(raw)
	if !hmac.Equal(got, mac.Sum(nil)) {
		return nil, errors.New("requestState signature mismatch")
	}
	var st heldState
	if err := json.Unmarshal(raw, &st); err != nil {
		return nil, errors.New("malformed requestState")
	}
	if st.Agent != agent || st.JobID == "" || time.Now().Unix() > st.Exp {
		return nil, errors.New("requestState expired or not this identity's")
	}
	return &st, nil
}

func heldReviewMessage(method, path string) string {
	return fmt.Sprintf("This agent wants to %s %s, which needs human approval. "+
		"Open the review page to approve or deny it (you sign in there).", method, path)
}

// answerHeld shapes a held execute: URL elicitation for clients declaring
// it, otherwise the held result (payload) at once.
func (s *mcpServer) answerHeld(
	ctx context.Context, req *mcp.CallToolRequest, agent string,
	envelope, payload map[string]any, method, path string,
) (*mcp.CallToolResult, error) {
	jobID, _ := envelope["job_id"].(string)
	door := heldFrontDoor(req.ClientCapabilities())
	s.logHeldFrontDoor(req, door, jobID)
	if door == frontDoorURLElicitation {
		raw, _ := json.Marshal(envelope["approval"])
		var approval heldApproval
		if err := json.Unmarshal(raw, &approval); err != nil {
			return nil, fmt.Errorf("failed to read the held approval: %w", err)
		}
		links, _ := envelope["_links"].(map[string]any)
		token, err := sealHeldState(heldState{
			JobID: jobID, Agent: agent, Approval: approval, Links: links,
			Exp: time.Now().Add(heldStateTTL).Unix(),
		})
		if err != nil {
			return nil, err
		}
		return &mcp.CallToolResult{
			InputRequests: mcp.InputRequestMap{
				reviewInputKey: &mcp.ElicitParams{
					Mode:    "url",
					Message: heldReviewMessage(method, path),
					URL:     approval.ReviewURL,
				},
			},
			RequestState: token,
		}, nil
	}
	return s.result(ctx, payload), nil
}

// resumeHeld answers a URL-elicitation retry: never re-sends the call, reads
// the job the sealed state names instead.
func (s *mcpServer) resumeHeld(ctx context.Context, req *mcp.CallToolRequest, agent string) (*mcp.CallToolResult, error) {
	st, err := openHeldState(req.Params.RequestState, agent)
	if err != nil {
		s.logger.Warn("held requestState refused", "error", err)
		return nil, invalidParams(errors.New("invalid or expired requestState"))
	}
	s.logHeldFrontDoor(req, "url_elicitation_retry", st.JobID)
	if terminal := s.heldShortWait(ctx, st.JobID); terminal != nil {
		return s.result(ctx, terminal), nil
	}
	return s.result(ctx, map[string]any{
		"schema_version": mcpSchemaVersion,
		"status":         202,
		"headers":        map[string]string{},
		"body": map[string]any{
			"job_id":          st.JobID,
			"status":          "held",
			"approval":        st.Approval,
			"agent_directive": heldAgentDirective,
			"_links":          st.Links,
		},
	}), nil
}

// heldShortWait polls the job for up to heldWaitBudget; the terminal poll
// payload, or nil when the window lapses (or a poll fails — the held result
// still tells the model how to poll).
func (s *mcpServer) heldShortWait(ctx context.Context, jobID string) map[string]any {
	budget := s.heldWaitBudget
	if budget <= 0 {
		budget = defaultHeldWaitBudget
	}
	poll := s.heldWaitPoll
	if poll <= 0 {
		poll = heldWaitPoll
	}
	deadline := time.Now().Add(budget)
	for {
		payload, soft := s.jobPollPayload(ctx, jobID)
		if soft != nil {
			return nil
		}
		if isTerminalJobStatus(payload["status"]) {
			return payload
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return nil
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(min(poll, remaining)):
		}
	}
}

func (s *mcpServer) logHeldFrontDoor(req *mcp.CallToolRequest, door, jobID string) {
	name, version := "", ""
	if ci := req.ClientInfo(); ci != nil {
		name, version = ci.Name, ci.Version
	}
	s.logger.Info("held execution front door", "front_door", door, "job_id", jobID,
		"client_name", name, "client_version", version)
}
