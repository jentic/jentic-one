// Package accessclient is a thin HTTP client for the Jentic control-plane
// access-request surface: an agent files a request for the access it is missing
// (a toolkit binding or a scope grant), watches it, amends or withdraws it, and
// reads its own identity/bindings via GET /me. It targets the same control-plane
// base URL as the other clients and attaches an agent bearer token to every
// request. Deciding (approve/deny) a request is a human/admin action and is
// deliberately not exposed here.
package accessclient

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"github.com/jentic/jentic-one/cli/internal/httpx"
)

// Access-request lifecycle status values.
const (
	StatusPending           = "pending"
	StatusApproved          = "approved"
	StatusPartiallyApproved = "partially_approved"
	StatusDenied            = "denied"
	StatusWithdrawn         = "withdrawn"
	StatusExpired           = "expired"
)

// Client talks to a single Jentic control-plane base URL.
type Client struct {
	http *httpx.Client
}

// New returns a client for the given base URL (trailing slash trimmed).
func New(baseURL string) *Client {
	return &Client{http: httpx.New(baseURL, 30*time.Second)}
}

// HTTPError is the shared problem-details transport error.
type HTTPError = httpx.HTTPError

// ErrDuplicatePending is returned when filing collides with an existing pending
// request for the same resource. The control plane answers 409 with the
// existing request's id and approve_url, which the caller can attach to instead
// of erroring out.
var ErrDuplicatePending = errors.New("a pending access request already exists for this resource")

// DuplicatePendingError carries the existing request reference from a 409.
type DuplicatePendingError struct {
	ExistingRequestID string
	ApproveURL        string
}

func (e *DuplicatePendingError) Error() string {
	return "a pending access request already exists: " + e.ExistingRequestID
}

func (e *DuplicatePendingError) Unwrap() error { return ErrDuplicatePending }

// Rule is a permission rule attached to a request item (credential binds).
type Rule struct {
	Effect     string   `json:"effect"`
	Methods    []string `json:"methods,omitempty"`
	Path       string   `json:"path,omitempty"`
	Operations []string `json:"operations,omitempty"`
}

// Item is a single line-item in a file request. Exactly one of ResourceID or
// ResourceReference identifies the target resource.
type Item struct {
	ResourceType      string         `json:"resource_type"`
	Action            string         `json:"action"`
	ResourceID        string         `json:"resource_id,omitempty"`
	ResourceReference map[string]any `json:"resource_reference,omitempty"`
	ToType            string         `json:"to_type,omitempty"`
	ToID              string         `json:"to_id,omitempty"`
	Rules             []Rule         `json:"rules,omitempty"`
}

// FileRequest is the POST body for /access-requests.
type FileRequest struct {
	Reason string `json:"reason,omitempty"`
	Items  []Item `json:"items"`
}

// AmendItem amends a single pending item (rules or resource_id).
type AmendItem struct {
	ItemID     string `json:"item_id"`
	Rules      []Rule `json:"rules,omitempty"`
	ResourceID string `json:"resource_id,omitempty"`
}

type amendRequest struct {
	Items []AmendItem `json:"items"`
}

// ItemResponse is a single line item as returned by the API.
type ItemResponse struct {
	ID                string           `json:"id"`
	ResourceType      string           `json:"resource_type"`
	Action            string           `json:"action"`
	ResourceID        string           `json:"resource_id,omitempty"`
	ResourceReference map[string]any   `json:"resource_reference,omitempty"`
	ToType            string           `json:"to_type,omitempty"`
	ToID              string           `json:"to_id,omitempty"`
	Rules             []map[string]any `json:"rules,omitempty"`
	Status            string           `json:"status"`
	DecidedBy         string           `json:"decided_by,omitempty"`
	DecisionReason    string           `json:"decision_reason,omitempty"`
}

// EvaluationCheck is a single fulfillment check.
type EvaluationCheck struct {
	Check   string `json:"check"`
	Passed  bool   `json:"passed"`
	Blocker string `json:"blocker,omitempty"`
}

// Evaluation is the computed verdict on whether the caller can fulfill a request.
type Evaluation struct {
	CanFulfill bool              `json:"can_fulfill"`
	Checks     []EvaluationCheck `json:"checks"`
}

// Request is an access-request envelope.
type Request struct {
	ID         string         `json:"id"`
	ActorID    string         `json:"actor_id"`
	Reason     string         `json:"reason,omitempty"`
	Status     string         `json:"status"`
	ApproveURL string         `json:"approve_url"`
	FiledAt    time.Time      `json:"filed_at"`
	ExpiresAt  time.Time      `json:"expires_at"`
	Items      []ItemResponse `json:"items"`
	Evaluation *Evaluation    `json:"evaluation,omitempty"`
}

// IsTerminal reports whether the request has left the pending state.
func (r *Request) IsTerminal() bool { return r.Status != StatusPending }

// ListResult is a page of access requests.
type ListResult struct {
	Data       []Request `json:"data"`
	HasMore    bool      `json:"has_more"`
	NextCursor string    `json:"next_cursor"`
}

// ToolkitBinding is a single agent→toolkit binding from GET /me.
type ToolkitBinding struct {
	ToolkitID string    `json:"toolkit_id"`
	Name      string    `json:"name"`
	BoundAt   time.Time `json:"bound_at"`
}

// Me is the agent's view of itself from GET /me.
//
// GET /me returns a discriminated union (MeUser | MeAgent | MeServiceAccount)
// keyed on Type; this struct mirrors the MeAgent variant. The Type field is
// decoded so Me() can reject a non-agent token instead of silently presenting a
// user/service-account as an agent with no bindings (see auth/web/schemas
// identity.MeResponse).
type Me struct {
	Type   string   `json:"type"`
	ID     string   `json:"id"`
	Name   string   `json:"name"`
	Status string   `json:"status"`
	Scopes []string `json:"scopes"`
	// nil when the server omits token_scopes (pre-#673); see StaleScopes.
	TokenScopes     []string         `json:"token_scopes"`
	ToolkitBindings []ToolkitBinding `json:"toolkit_bindings"`
}

// StaleScopes returns the scopes the agent has been granted that the presented
// token does not yet carry — i.e. grants that landed after the token was minted
// and won't take effect until the token is refreshed (`jentic access refresh`).
// See issue #673.
//
// A nil TokenScopes means the server did not report token scopes at all (e.g. an
// older server predating #673): staleness is then unknowable, so we report none
// rather than falsely flagging every grant and nagging the agent to refresh. An
// explicitly empty (non-nil) list is honored — a token that genuinely carries no
// scopes makes every grant stale. JSON decoding gives nil for a missing/null
// field and a non-nil empty slice for `[]`, so the two are distinguishable.
func (m *Me) StaleScopes() []string {
	if m.TokenScopes == nil {
		return nil
	}
	inToken := make(map[string]struct{}, len(m.TokenScopes))
	for _, s := range m.TokenScopes {
		inToken[s] = struct{}{}
	}
	var stale []string
	for _, s := range m.Scopes {
		if _, ok := inToken[s]; !ok {
			stale = append(stale, s)
		}
	}
	return stale
}

// File files a new access request (POST /access-requests). A 409 collision with
// an existing pending request is surfaced as *DuplicatePendingError.
func (c *Client) File(ctx context.Context, token string, req FileRequest) (*Request, error) {
	var out Request
	if err := c.http.Do(ctx, http.MethodPost, "/access-requests", token, req, &out); err != nil {
		if dup := asDuplicatePending(err); dup != nil {
			return nil, dup
		}
		return nil, err
	}
	return &out, nil
}

// List returns a page of the caller's access requests. Empty status/cursor mean
// "no filter"; limit <= 0 lets the server choose its default.
func (c *Client) List(ctx context.Context, token, status, cursor string, limit int) (*ListResult, error) {
	q := url.Values{}
	if status != "" {
		q.Set("status", status)
	}
	if cursor != "" {
		q.Set("cursor", cursor)
	}
	if limit > 0 {
		q.Set("limit", strconv.Itoa(limit))
	}
	var out ListResult
	if err := c.http.Do(ctx, http.MethodGet, "/access-requests"+httpx.Query(q), token, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Get fetches a single access request by id.
func (c *Client) Get(ctx context.Context, token, id string) (*Request, error) {
	var out Request
	if err := c.http.Do(ctx, http.MethodGet, "/access-requests/"+url.PathEscape(id), token, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Withdraw withdraws a pending request (POST /access-requests/{id}:withdraw).
func (c *Client) Withdraw(ctx context.Context, token, id string) (*Request, error) {
	var out Request
	path := "/access-requests/" + url.PathEscape(id) + ":withdraw"
	if err := c.http.Do(ctx, http.MethodPost, path, token, struct{}{}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Amend updates pending items on a request (POST /access-requests/{id}:amend).
func (c *Client) Amend(ctx context.Context, token, id string, items []AmendItem) (*Request, error) {
	var out Request
	path := "/access-requests/" + url.PathEscape(id) + ":amend"
	if err := c.http.Do(ctx, http.MethodPost, path, token, amendRequest{Items: items}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Me returns the caller's identity, scopes, and toolkit bindings (GET /me).
//
// access whoami/execute are agent-only flows, so a non-agent token (user or
// service account) is rejected here rather than decoded into an agent-shaped
// value with empty bindings — which would misleadingly read as an approved
// agent with no toolkits.
func (c *Client) Me(ctx context.Context, token string) (*Me, error) {
	var out Me
	if err := c.http.Do(ctx, http.MethodGet, "/me", token, nil, &out); err != nil {
		return nil, err
	}
	if out.Type != "" && out.Type != "agent" {
		return nil, fmt.Errorf("this token belongs to a %q, not an agent; agent commands require an agent token", out.Type)
	}
	return &out, nil
}

// asDuplicatePending maps a 409 carrying existing_request_id to a typed error.
func asDuplicatePending(err error) *DuplicatePendingError {
	var he *httpx.HTTPError
	if !errors.As(err, &he) || he.StatusCode != http.StatusConflict {
		return nil
	}
	fields := he.Fields()
	id, _ := fields["existing_request_id"].(string)
	if id == "" {
		return nil
	}
	approve, _ := fields["approve_url"].(string)
	return &DuplicatePendingError{ExistingRequestID: id, ApproveURL: approve}
}
