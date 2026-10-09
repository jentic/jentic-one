package api

// mcp_request_connection.go holds the request_connection MCP tool — the agent-initiable
// leg of credential provisioning (theme-7 Phase 1b). It rides the same
// generated-client seam the `jentic connect` verb uses
// (POST /integrations:connect via IntegrationsConnectWithResponse): the tool
// STARTS a connect session for a registry vendor and returns the
// {session_id, approval_url, resolved_flow} the agent relays to its human
// operator. It is deliberately create-only: approval always blocks on a human
// in the browser, so the agent's loop is relay approval_url → operator
// approves → confirm the new binding with whoami → retry the blocked call.
// The poll_token is not a field of the tool result — the tool surface serves
// no poll leg, and the approval_url carries only the session id: the agent's
// owner (or an org admin) opens the approve page without the token.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// requestConnectionParams: vendor is the registry key; scopes/reason/rules
// are the optional request shaping the approver reviews;
// oauth_app_registration_id pins the shared OAuth app the user picked.
var requestConnectionParams = []paramSpec{
	{name: "vendor", kind: paramString},
	{name: "requested_scopes", aliases: []string{"scopes"}, kind: paramStringList},
	{name: "reason", kind: paramString},
	{name: "oauth_app_registration_id", aliases: []string{"registration_id"}, kind: paramString},
	{name: "requested_permission_rules", aliases: []string{"permission_rules"}, kind: paramJSON},
}

var requestConnectionSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"vendor": map[string]any{
			"type":        "string",
			"description": "Vendor registry key, e.g. \"github\" (required). Only vendors in this deployment's connect registry work.",
		},
		"requested_scopes": map[string]any{
			"type":        "array",
			"items":       map[string]any{"type": "string"},
			"description": "Vendor scope names to request (optional; \"scopes\" is an accepted alias). The human approver confirms the final set — write scopes are flagged for their attention.",
		},
		"reason": map[string]any{
			"type":        "string",
			"description": "Why you need this connection (optional, max 1024 chars). Shown to the approver — a clear one-liner gets you approved faster.",
		},
		"oauth_app_registration_id": map[string]any{
			"type":        "string",
			"description": "registration_id of the shared OAuth app to connect through (optional; \"registration_id\" is an accepted alias). Only needed when several shared apps serve the vendor: the error then lists them, and your human user picks one — never choose it yourself.",
		},
		"requested_permission_rules": map[string]any{
			"type": "array",
			"items": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"effect":     map[string]any{"type": "string", "enum": []string{"allow", "deny"}},
					"methods":    map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
					"path":       map[string]any{"type": "string"},
					"match_mode": map[string]any{"type": "string", "enum": []string{"regex", "prefix", "exact"}},
					"operations": map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				},
				"required": []string{"effect"},
			},
			"description": "Permission rules you are asking for on the new binding (optional, max 100; \"permission_rules\" is an accepted alias), e.g. [{\"effect\": \"allow\", \"methods\": [\"GET\"], \"path\": \"/repos/.*\"}]. The approver reviews and edits them before anything is granted; an allow rule must constrain methods, path, or operations.",
		},
	},
	"required": []string{"vendor"},
}

// requestConnectionInstruction is the operator-relay guidance stamped on every
// successful result. Lane-invariant by construction: it names only whoami and
// the human approval step, never a stdio-only tool.
const requestConnectionInstruction = "Relay the approval_url to your human operator — they open it in " +
	"their browser and approve (or reject) the connection; you cannot open it or approve it yourself. " +
	"Once they confirm, call whoami to see the new credential binding, then retry the call that was blocked."

// connectResponse is the typed projection of the 201 body. Decoded from the
// raw bytes (the generated JSON201 is untyped); poll_token is decoded but
// never re-emitted — see the file comment.
type connectResponse struct {
	SessionID    string `json:"session_id"`
	ApprovalURL  string `json:"approval_url"`
	PollToken    string `json:"poll_token"`
	ResolvedFlow string `json:"resolved_flow"`
}

// connectScopesMax mirrors the route's requested_scopes bound
// (IntegrationsConnectRequest.requested_scopes — max_length=100), enforced
// client-side for the same reason as connectReasonMax.
const connectScopesMax = 100

// connectReasonMax mirrors the route's reason bound
// (IntegrationsConnectRequest.reason — max_length=1024): enforced client-side
// so an overlong reason is a clear invalid-params error, not a route 422
// rendered as a retryable transport failure (review L2; the Python mount
// bounds it the same way).
const connectReasonMax = 1024

// connectRegistrationMax mirrors the route's oauth_app_registration_id bound
// (IntegrationsConnectRequest.oauth_app_registration_id — max_length=30).
const connectRegistrationMax = 30

// connectRulesMax mirrors the route's requested_permission_rules bound
// (IntegrationsConnectRequest.requested_permission_rules — max_length=100).
const connectRulesMax = 100

// decodeConnectRules decodes the requested_permission_rules argument into the
// generated rule type, refusing unknown keys and a missing/unknown effect so
// a malformed ask is an invalid-params error here rather than a route 422.
// Path-pattern validity and the condition-less-allow guard stay server-side.
func decodeConnectRules(raw json.RawMessage) ([]control.PermissionRuleSchema, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var rules []control.PermissionRuleSchema
	if err := dec.Decode(&rules); err != nil {
		return nil, fmt.Errorf("requested_permission_rules must be a list of rule objects "+
			"({effect, methods, path, match_mode, operations}): %w", err)
	}
	if len(rules) > connectRulesMax {
		return nil, fmt.Errorf("requested_permission_rules must list at most %d rules, got %d", connectRulesMax, len(rules))
	}
	for i, r := range rules {
		if r.Effect != "allow" && r.Effect != "deny" {
			return nil, fmt.Errorf("requested_permission_rules[%d].effect must be \"allow\" or \"deny\", got %q", i, r.Effect)
		}
	}
	return rules, nil
}

func (s *mcpServer) handleRequestConnection(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, requestConnectionParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	vendor, _ := args["vendor"].(string)
	if vendor == "" {
		return nil, invalidParams(errors.New(`request_connection requires "vendor": the vendor registry key, e.g. {"vendor": "github"}`))
	}
	reason, _ := args["reason"].(string)
	if len(reason) > connectReasonMax {
		return nil, invalidParams(fmt.Errorf("reason must be at most %d characters, got %d", connectReasonMax, len(reason)))
	}
	scopes, _ := args["requested_scopes"].([]string)
	if len(scopes) > connectScopesMax {
		return nil, invalidParams(fmt.Errorf("requested_scopes must list at most %d scopes, got %d", connectScopesMax, len(scopes)))
	}
	registration, _ := args["oauth_app_registration_id"].(string)
	if len(registration) > connectRegistrationMax {
		return nil, invalidParams(fmt.Errorf("oauth_app_registration_id must be at most %d characters, got %d", connectRegistrationMax, len(registration)))
	}
	var rules []control.PermissionRuleSchema
	if raw, ok := args["requested_permission_rules"].(json.RawMessage); ok {
		if rules, err = decodeConnectRules(raw); err != nil {
			return nil, invalidParams(err)
		}
	}
	client, err := s.app.controlClient(cctx)
	if err != nil {
		s.logger.Warn("request_connection failed", "vendor", vendor, "error", redactedErr(err))
		return s.softError(cctx, err), nil
	}
	body := control.IntegrationsConnectRequest{Vendor: vendor}
	if len(scopes) > 0 {
		body.RequestedScopes = &scopes
	}
	if reason != "" {
		body.Reason = &reason
	}
	if registration != "" {
		body.OauthAppRegistrationId = &registration
	}
	if len(rules) > 0 {
		body.RequestedPermissionRules = &rules
	}
	// Never set body.AgentId: the caller IS the agent — the control plane
	// injects the caller's identity, and a supplied agent_id is refused (403).
	resp, callErr := client.IntegrationsConnectWithResponse(cctx, body)
	if err := apiErrorFor(resp, callErr); err != nil {
		return s.requestConnectionError(cctx, client, vendor, err, connectRetryAfter(resp)), nil
	}
	var created connectResponse
	if err := json.Unmarshal(resp.Body, &created); err != nil {
		return s.softError(cctx, &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  "decode /integrations:connect response: " + err.Error(),
		}), nil
	}
	s.logger.Info("request_connection", "vendor", vendor, "session_id", created.SessionID, "flow", created.ResolvedFlow)
	return s.result(cctx, map[string]any{
		"schema_version": mcpSchemaVersion,
		"session_id":     created.SessionID,
		"approval_url":   created.ApprovalURL,
		"resolved_flow":  created.ResolvedFlow,
		"instruction":    requestConnectionInstruction,
	}), nil
}

// connectRetryAfter extracts the Retry-After seconds the route stamps on its
// 429 (integrations.py: per-actor connect rate limit), so the tool's
// retryable envelope carries the same retry_after_s extra as the Python
// mount's. Zero when absent/unparseable.
func connectRetryAfter(resp *control.IntegrationsConnectHTTPResp) float64 {
	if resp == nil || resp.HTTPResponse == nil {
		return 0
	}
	secs, err := strconv.ParseFloat(strings.TrimSpace(resp.HTTPResponse.Header.Get("Retry-After")), 64)
	if err != nil || secs < 0 {
		return 0
	}
	return secs
}

// requestConnectionError maps the route's failure surface onto the coded
// taxonomy (§3.7 posture):
//   - 404 (unknown vendor) / 400 (unsupported flow) — a correctable ask:
//     RESOLVE_FAILED with the rediscovery/operator step.
//   - 400 ambiguous_vendor — several shared OAuth apps serve the vendor and
//     the call carried no pin: RESOLVE_FAILED listing the apps
//     (details.candidates) with "ask your user, then retry with the id";
//     next_tool is request_connection.
//   - 400 invalid_oauth_app_registration — the pin is unusable for the
//     vendor: RESOLVE_FAILED, retry without it to see the candidates.
//   - 422 — the route rejected the request shape (in practice a permission
//     rule the client-side decode can't judge, e.g. an invalid path regex):
//     RESOLVE_FAILED with the route's reason.
//   - 403 — the missing credentials:connect permission (agents hold it by
//     default): a permission gap for the operator, not a revoked identity —
//     mirrors the search_catalog/import_api special case.
//   - 429 — the per-actor connect rate limit: retryable TRANSPORT_ERROR
//     carrying the route's Retry-After as retry_after_s (Python-mount twin).
//   - 503 (vendor not configured) — the vendor is registered but its OAuth
//     client is not configured on this deployment: operator action.
func (s *mcpServer) requestConnectionError(
	ctx context.Context, client *control.ClientWithResponses, vendor string, err error, retryAfterS float64,
) *mcp.CallToolResult {
	s.logger.Warn("request_connection failed", "vendor", vendor, "error", redactedErr(err))
	var he *HTTPError
	if errors.As(err, &he) {
		if isAmbiguousVendor(he) {
			candidates := listVendorAppCandidates(ctx, client, vendor)
			return s.softErrorNext(ctx, ambiguousVendorCoded(vendor, err, candidates,
				requestConnectionRegistrationRetry), "request_connection")
		}
		if isInvalidRegistration(he) {
			return s.softErrorNext(ctx, invalidRegistrationCoded(vendor, err,
				"call request_connection again without oauth_app_registration_id to see the shared apps that serve it"),
				"request_connection")
		}
		switch he.StatusCode {
		case http.StatusUnprocessableEntity:
			return s.softError(ctx, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("the connect request for vendor %q was rejected: %v", vendor, err),
				Actionable: "Fix the argument the error names (usually a requested_permission_rules entry: " +
					"a valid path pattern, and an allow rule must constrain methods, path, or operations), " +
					"then retry request_connection.",
			})
		case http.StatusBadRequest, http.StatusNotFound:
			return s.softErrorNext(ctx, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("cannot start a connect session for vendor %q: %v", vendor, err),
				Actionable: "Pass a vendor registry key this deployment supports (e.g. \"github\"). " +
					"If the vendor is not in the registry, this tool cannot connect it: find the API " +
					"with search_catalog and ask your human operator to connect a credential for it " +
					"in the dashboard instead.",
			}, "search_catalog")
		case http.StatusForbidden:
			return s.softError(ctx, &ux.CodedError{
				Code: ux.CodeBrokerDenied,
				Msg:  fmt.Sprintf("starting a connect session requires the credentials:connect permission: %v", err),
				Actionable: "Ask your human operator to grant this agent the credentials:connect permission " +
					"in the dashboard. Once they confirm, run `jentic logout` (clears only the cached token) " +
					"so the next call mints a token carrying the permission, then retry request_connection.",
			})
		case http.StatusTooManyRequests:
			extra := map[string]any{"retryable": true}
			if retryAfterS > 0 {
				extra["retry_after_s"] = retryAfterS
			}
			return s.softErrorExtra(ctx, &ux.CodedError{
				Code:       ux.CodeTransportError,
				Msg:        fmt.Sprintf("connect sessions are rate limited: %v", err),
				Actionable: "Wait briefly and retry request_connection; do not loop on it.",
			}, "", extra)
		case http.StatusServiceUnavailable:
			return s.softError(ctx, &ux.CodedError{
				Code: ux.CodeBrokerDenied,
				Msg:  fmt.Sprintf("vendor %q is registered but not configured on this deployment: %v", vendor, err),
				Actionable: fmt.Sprintf("Ask your human operator to configure the %q vendor's OAuth client "+
					"on this deployment (or connect the credential in the dashboard), then retry.", vendor),
			})
		}
	}
	return s.transportSoftError(ctx, err, nil)
}

// requestConnectionRegistrationRetry is the MCP lanes' retry form in the
// ambiguous_vendor advice (the Python mount renders the same text).
const requestConnectionRegistrationRetry = "call request_connection again with " +
	"oauth_app_registration_id set to the registration_id they pick"

// connectToolSpecs declares the connect tool surface. No readOnlyHint (the
// tool creates a session + pending credential row — --read-only withholds
// it), no idempotentHint (every call files a fresh session), and no
// destructiveHint (execute alone carries that, §3.2).
func (s *mcpServer) connectToolSpecs() []mcpToolSpec {
	return []mcpToolSpec{
		{
			tool: &mcp.Tool{
				Name:  "request_connection",
				Title: "Start connecting a vendor credential",
				Description: "Start a connect session for a verified vendor when whoami shows no credential " +
					"binding serving the API you need — the self-service leg of credential provisioning. " +
					`Example: {"vendor": "github", "reason": "read open PRs to summarise them"}. Returns ` +
					"{session_id, approval_url, resolved_flow}: relay the approval_url to your human " +
					"operator — they approve the connection in their browser and confirm the scopes; you " +
					"cannot open the URL or approve it yourself. Once they confirm, call whoami to see the " +
					"new credential binding, then retry the blocked call. This tool only STARTS the flow " +
					"and never polls it. Optionally pass requested_scopes (vendor scope names; write scopes " +
					"are flagged for the approver), requested_permission_rules (the binding rules you need; " +
					"the approver reviews them) and a reason the approver will see. If several shared OAuth " +
					"apps serve the vendor, the error lists them: ask your human user which one to use " +
					"(choosing it is their call, not yours) and retry with its oauth_app_registration_id. Only vendors in the " +
					"deployment's connect registry work; for any other API, ask your operator to connect a " +
					"credential in the dashboard. Binding an EXISTING credential and scope grants stay " +
					"operator actions.",
				InputSchema: requestConnectionSchema,
				Annotations: &mcp.ToolAnnotations{},
			},
			handler: s.handleRequestConnection,
		},
	}
}
