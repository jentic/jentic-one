package api

// mcp_held.go shapes a held (require-approval) execute for the MCP client,
// per request, from the capabilities the client declares on THIS call (the
// `_meta` clientCapabilities of a 2026-07-28 request, else the legacy
// initialize params):
//
//   - elicitation.url declared → a URL-mode elicitation of the review page as
//     a multi-round-trip input-required result, carrying an HMAC-sealed
//     requestState. The retry never re-sends the call: it reads the held
//     execution (finished → the result; still held → the short wait, then
//     the held result). The elicitation accept is consent to open the link,
//     never a decision — only a signed-in reviewer's :decide releases the
//     call.
//   - otherwise → the held result at once: the broker's envelope as a normal
//     tool result, so the model relays the review link straight away and
//     polls with get_execution_result. Nobody can approve a call before seeing
//     its review link, so waiting on the first call would only delay the link.
//
// The short wait (poll the held execution up to heldWaitBudget) runs only on the
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

// heldAgentDirective is the held 202 envelope's agent_directive. Kept
// byte-identical with HELD_AGENT_DIRECTIVE in src/jentic_one/shared/jobs/hold.py.
const heldAgentDirective = "This call needs human approval. Most important: show the user the review_url, " +
	"since they cannot approve it without that link, and end your turn so they see it. " +
	"Do not call get_execution_result until the user replies; then call it once with " +
	"held_execution_id. It waits up to 30 seconds (timeout, at most 30). If it is " +
	"still held, show the user the review_url again and end your turn again. Do not " +
	"re-send the call."

// stillHeldAgentDirective is a held poll payload's agent_directive while the
// call has not finished. Kept byte-identical with STILL_HELD_AGENT_DIRECTIVE
// in src/jentic_one/shared/jobs/hold.py.
const stillHeldAgentDirective = "This call is still waiting on human approval. Show the user the review_url again " +
	"and end your turn. When they reply, call get_execution_result once with this " +
	"held_execution_id. Do not re-send the call."

// heldState is the sealed requestState of a URL-elicitation round trip.
type heldState struct {
	HeldExecutionID string         `json:"held_execution_id"`
	Agent           string         `json:"agent"`
	ReviewURL       string         `json:"review_url"`
	ExpiresAt       string         `json:"expires_at"`
	Links           map[string]any `json:"links,omitempty"`
	Exp             int64          `json:"exp"`
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
	if _, ok := m["held_execution_id"].(string); !ok {
		return nil
	}
	if _, ok := m["review_url"].(string); !ok {
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
	if st.Agent != agent || st.HeldExecutionID == "" || st.ReviewURL == "" || time.Now().Unix() > st.Exp {
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
	heldExecutionID, _ := envelope["held_execution_id"].(string)
	door := heldFrontDoor(req.ClientCapabilities())
	s.logHeldFrontDoor(req, door, heldExecutionID)
	if door == frontDoorURLElicitation {
		reviewURL, _ := envelope["review_url"].(string)
		expiresAt, _ := envelope["expires_at"].(string)
		links, _ := envelope["_links"].(map[string]any)
		token, err := sealHeldState(heldState{
			HeldExecutionID: heldExecutionID, Agent: agent, ReviewURL: reviewURL, ExpiresAt: expiresAt,
			Links: links, Exp: time.Now().Add(heldStateTTL).Unix(),
		})
		if err != nil {
			return nil, err
		}
		return &mcp.CallToolResult{
			InputRequests: mcp.InputRequestMap{
				reviewInputKey: &mcp.ElicitParams{
					Mode:    "url",
					Message: heldReviewMessage(method, path),
					URL:     reviewURL,
				},
			},
			RequestState: token,
		}, nil
	}
	return s.result(ctx, payload), nil
}

// resumeHeld answers a URL-elicitation retry: never re-sends the call, reads
// the held execution the sealed state names instead.
func (s *mcpServer) resumeHeld(ctx context.Context, req *mcp.CallToolRequest, agent string) (*mcp.CallToolResult, error) {
	st, err := openHeldState(req.Params.RequestState, agent)
	if err != nil {
		s.logger.Warn("held requestState refused", "error", err)
		return nil, invalidParams(errors.New("invalid or expired requestState"))
	}
	s.logHeldFrontDoor(req, "url_elicitation_retry", st.HeldExecutionID)
	if terminal := s.heldShortWait(ctx, st.HeldExecutionID); terminal != nil {
		return s.result(ctx, terminal), nil
	}
	return s.result(ctx, map[string]any{
		"schema_version": mcpSchemaVersion,
		"status":         202,
		"headers":        map[string]string{},
		"body": map[string]any{
			"status":            "held",
			"held_execution_id": st.HeldExecutionID,
			"review_url":        st.ReviewURL,
			"expires_at":        st.ExpiresAt,
			"agent_directive":   heldAgentDirective,
			"_links":            linksOrEmpty(st.Links),
		},
	}), nil
}

// linksOrEmpty is a held envelope's _links, never null.
func linksOrEmpty(links map[string]any) map[string]any {
	if links == nil {
		return map[string]any{}
	}
	return links
}

// heldShortWait follows the held execution for up to heldWaitBudget; the
// finished poll payload, or nil when the window lapses (or a poll fails —
// the held result still tells the model how to poll).
func (s *mcpServer) heldShortWait(ctx context.Context, heldExecutionID string) map[string]any {
	budget := s.heldWaitBudget
	if budget <= 0 {
		budget = defaultHeldWaitBudget
	}
	payload, finished, soft := s.followHeldExecution(ctx, heldExecutionID, budget)
	if soft != nil || !finished {
		return nil
	}
	return payload
}

func (s *mcpServer) logHeldFrontDoor(req *mcp.CallToolRequest, door, heldExecutionID string) {
	name, version := "", ""
	if ci := req.ClientInfo(); ci != nil {
		name, version = ci.Name, ci.Version
	}
	s.logger.Info("held execution front door", "front_door", door, "held_execution_id", heldExecutionID,
		"client_name", name, "client_version", version)
}
