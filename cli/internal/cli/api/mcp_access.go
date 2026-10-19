package api

// mcp_access.go holds the show_access MCP tool: the calling agent's own access
// through one credential binding (`jentic access show`'s read). It is what a
// connect-session edit is relative to, so an agent reads it before asking for
// a change, and again when such a request fails.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

var showAccessParams = []paramSpec{
	{name: "credential_id", kind: paramString},
}

var showAccessSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"credential_id": map[string]any{
			"type":        "string",
			"description": "The credential whose binding to read (required), e.g. \"cred_…\" from whoami's credential bindings.",
		},
	},
	"required": []string{"credential_id"},
}

func (s *mcpServer) accessToolSpecs() []mcpToolSpec {
	return []mcpToolSpec{
		{
			tool: &mcp.Tool{
				Name:  "show_access",
				Title: "Show your access through a credential",
				Description: "Show what your binding to one credential lets you do: its permission rules " +
					"with their ids (data, first match wins, default deny), the shared rule set it points " +
					"at (whose rules apply instead of data), the credential's granted OAuth scopes, and " +
					"whether the binding is suspended. " +
					`Example: {"credential_id": "cred_…"} — credential ids come from whoami's bindings. ` +
					"Call it before asking for a change to your access, and when such a request fails. " +
					"A credential you are not bound to (or whose binding is suspended) is not found: " +
					"call whoami to list your bindings.",
				InputSchema: showAccessSchema,
				Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true},
			},
			handler: s.handleShowAccess,
		},
	}
}

func (s *mcpServer) handleShowAccess(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, showAccessParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	credentialID, _ := args["credential_id"].(string)
	if credentialID == "" {
		return nil, invalidParams(errors.New(`show_access requires "credential_id", e.g. {"credential_id": "cred_…"}`))
	}
	access, err := s.app.getBindingAccess(cctx, credentialID)
	if err != nil {
		s.logger.Warn("show_access failed", "credential_id", credentialID, "error", redactedErr(err))
		next := ""
		if mcpCoded(err).Code == ux.CodeResolveFailed {
			next = "whoami"
		}
		return s.softErrorNext(cctx, err, next), nil
	}
	access["schema_version"] = mcpSchemaVersion
	return s.result(cctx, access), nil
}

// ruleListSchema is a JSON-schema rule object list (the rule shape the
// permissions read returns, minus ids).
var ruleListSchema = map[string]any{
	"type": "array",
	"items": map[string]any{
		"type": "object",
		"properties": map[string]any{
			"effect":     map[string]any{"type": "string", "enum": []string{"allow", "deny", "require-approval"}},
			"methods":    map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
			"path":       map[string]any{"type": "string"},
			"match_mode": map[string]any{"type": "string", "enum": []string{"regex", "prefix", "exact"}},
			"operations": map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
		},
		"required": []string{"effect"},
	},
}

var editRulesSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"add": map[string]any{
			"type": "array",
			"description": "Rules to add: each a rule object, or {\"rule\": …, \"before\": \"<rule id>\"} to place it " +
				"before a rule (first match wins; appended otherwise). A denial's parameters.edit.suggested_rules fit as-is.",
			"items": map[string]any{"type": "object"},
		},
		"remove": map[string]any{
			"type": "array", "items": map[string]any{"type": "string"},
			"description": "Ids of rules to remove (from show_access).",
		},
		"set": map[string]any{
			"description": "The full rule list to replace the current one with, in order. Not combined with add or remove.",
			"type":        "array", "items": ruleListSchema["items"],
		},
	},
}

var requestAccessChangeParams = []paramSpec{
	{name: "credential_id", kind: paramString},
	{name: "rules", kind: paramJSON},
	{name: "scopes", kind: paramStringList},
	{name: "resume", kind: paramJSON},
	{name: "reason", kind: paramString},
}

var requestAccessChangeSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"credential_id": map[string]any{
			"type":        "string",
			"description": "The binding's credential (required), as show_access and whoami report it.",
		},
		"rules": editRulesSchema,
		"scopes": map[string]any{
			"type": "array", "items": map[string]any{"type": "string"},
			"description": "OAuth scopes to add. The approver re-consents the credential; other agents bound to it gain them too.",
		},
		"resume": map[string]any{
			"type":        "boolean",
			"description": "Ask to switch a suspended binding back on.",
		},
		"reason": map[string]any{
			"type":        "string",
			"description": "Why you need the change (max 1024 chars). Shown to the reviewer.",
		},
	},
	"required": []string{"credential_id"},
}

var amendRequestParams = []paramSpec{
	{name: "session_id", kind: paramString},
	{name: "rules", kind: paramJSON},
	{name: "scopes", kind: paramStringList},
	{name: "resume", kind: paramJSON},
	{name: "reason", kind: paramString},
	{name: "requested_permission_rules", aliases: []string{"permission_rules"}, kind: paramJSON},
	{name: "requested_scopes", kind: paramStringList},
}

var amendRequestSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"session_id": map[string]any{
			"type":        "string",
			"description": "The open request to change (required), from request_access_change or request_connection.",
		},
		"rules":  editRulesSchema,
		"scopes": requestAccessChangeSchema["properties"].(map[string]any)["scopes"],
		"resume": requestAccessChangeSchema["properties"].(map[string]any)["resume"],
		"reason": requestAccessChangeSchema["properties"].(map[string]any)["reason"],
		"requested_permission_rules": map[string]any{
			"description": "For a request_connection request: the rules to ask for, replacing the ask.",
			"type":        "array", "items": ruleListSchema["items"],
		},
		"requested_scopes": map[string]any{
			"type": "array", "items": map[string]any{"type": "string"},
			"description": "For a request_connection request: the scopes to ask for, replacing the ask.",
		},
	},
	"required": []string{"session_id"},
}

const requestAccessChangeInstruction = "Relay the approval_url to your human operator — they review the " +
	"change in the browser; you cannot approve it yourself. Once they confirm, check it with " +
	"show_access and retry the call that was blocked. To change the request before then, use " +
	"amend_request rather than asking again."

// editInputFromArgs builds an editInput from normalized tool arguments.
func editInputFromArgs(args map[string]any) (editInput, error) {
	var in editInput
	if raw, ok := args["rules"].(json.RawMessage); ok {
		var rules struct {
			Add    json.RawMessage `json:"add"`
			Remove []string        `json:"remove"`
			Set    json.RawMessage `json:"set"`
		}
		if err := json.Unmarshal(raw, &rules); err != nil {
			return in, fmt.Errorf(`rules must be an object {"add": […], "remove": […]} or {"set": […]}: %w`, err)
		}
		in.addJSON, in.remove, in.setJSON = rules.Add, rules.Remove, rules.Set
	}
	in.scopes, _ = args["scopes"].([]string)
	if raw, ok := args["resume"].(json.RawMessage); ok {
		var resume bool
		if err := json.Unmarshal(raw, &resume); err != nil {
			return in, errors.New("resume must be true or false")
		}
		in.resume = &resume
	}
	if reason, ok := args["reason"].(string); ok {
		if len(reason) > connectReasonMax {
			return in, fmt.Errorf("reason must be at most %d characters, got %d", connectReasonMax, len(reason))
		}
		in.reason = &reason
	}
	if raw, ok := args["requested_permission_rules"].(json.RawMessage); ok {
		in.requestedRulesJSON = raw
	}
	in.requestedScopes, _ = args["requested_scopes"].([]string)
	return in, nil
}

// editErrorResult renders a refused edit or amend; a check_access directive
// points the model at show_access.
func (s *mcpServer) editErrorResult(ctx context.Context, err error, what string) *mcp.CallToolResult {
	coded := editRequestCoded(err, what)
	next := ""
	extra := map[string]any{}
	if p, ok := parseControlProblem(err); ok && len(p.AgentDirective) > 0 {
		extra["agent_directive"] = p.AgentDirective
		if p.Directive.Strategy == "check_access" {
			next = "show_access"
		}
	}
	return s.softErrorExtra(ctx, coded, next, extra)
}

func (s *mcpServer) editToolSpecs() []mcpToolSpec {
	return []mcpToolSpec{
		{
			tool: &mcp.Tool{
				Name:  "request_access_change",
				Title: "Ask for a change to your access",
				Description: "Ask your human user to change what your binding to one credential lets you do — " +
					"the recovery for a rules denial (action_denied), whose parameters.edit names the " +
					"credential and the rule to ask for. Read your access with show_access first: rule ids " +
					`come from there. Example: {"credential_id": "cred_…", "rules": {"add": [{"effect": "allow", ` +
					`"methods": ["POST"], "path": "/v1/send", "match_mode": "exact"}]}, "reason": "send replies"}. ` +
					"Also: remove rules by id, replace the list with rules.set, add OAuth scopes, or resume " +
					"a suspended binding. Put every change you need in one request. Returns {session_id, " +
					"approval_url}: relay the approval_url — nothing changes until a human approves. If a " +
					"request for the binding is already open, you get that session back: change it with " +
					"amend_request instead.",
				InputSchema: requestAccessChangeSchema,
				Annotations: &mcp.ToolAnnotations{},
			},
			handler: s.handleRequestAccessChange,
		},
		{
			tool: &mcp.Tool{
				Name:  "amend_request",
				Title: "Change an open request",
				Description: "Change what an open request you made asks for, before it is approved — instead " +
					"of asking again. The fields you pass replace those parts of the ask; the rest stay. " +
					"For a request_access_change request pass rules, scopes, resume or reason; for a " +
					"request_connection request pass requested_permission_rules, requested_scopes or reason. " +
					`Example: {"session_id": "cs_…", "rules": {"add": [ … ]}}. The approval_url stays the same; ` +
					"tell your human user the request changed. A request already approved or ended cannot " +
					"be amended: ask again.",
				InputSchema: amendRequestSchema,
				Annotations: &mcp.ToolAnnotations{},
			},
			handler: s.handleAmendRequest,
		},
	}
}

func (s *mcpServer) handleRequestAccessChange(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, requestAccessChangeParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	credentialID, _ := args["credential_id"].(string)
	if credentialID == "" {
		return nil, invalidParams(errors.New(`request_access_change requires "credential_id"`))
	}
	in, err := editInputFromArgs(args)
	if err != nil {
		return nil, invalidParams(err)
	}
	created, openID, err := s.app.requestAccessChange(cctx, credentialID, in)
	if err != nil {
		var coded *ux.CodedError
		if errors.As(err, &coded) && coded.Code == ux.CodeMissingArgument {
			return nil, invalidParams(err)
		}
		s.logger.Warn("request_access_change failed", "credential_id", credentialID, "error", redactedErr(err))
		return s.editErrorResult(cctx, err, "the access change"), nil
	}
	if openID != "" {
		return s.result(cctx, map[string]any{
			"schema_version": mcpSchemaVersion,
			"session_id":     openID,
			"already_open":   true,
			"instruction": "You already asked for a change to this binding and it is still open: " +
				"change it with amend_request instead of asking again.",
			"next_tool": "amend_request",
		}), nil
	}
	return s.result(cctx, map[string]any{
		"schema_version": mcpSchemaVersion,
		"session_id":     created.SessionID,
		"approval_url":   created.ApprovalURL,
		"revision_id":    created.RevisionID,
		"resolved_flow":  created.ResolvedFlow,
		"instruction":    requestAccessChangeInstruction,
	}), nil
}

func (s *mcpServer) handleAmendRequest(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, amendRequestParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	sessionID, _ := args["session_id"].(string)
	if sessionID == "" {
		return nil, invalidParams(errors.New(`amend_request requires "session_id"`))
	}
	in, err := editInputFromArgs(args)
	if err != nil {
		return nil, invalidParams(err)
	}
	amended, err := s.app.amendRequest(cctx, sessionID, in)
	if err != nil {
		var coded *ux.CodedError
		if errors.As(err, &coded) && coded.Code == ux.CodeMissingArgument {
			return nil, invalidParams(err)
		}
		s.logger.Warn("amend_request failed", "session_id", sessionID, "error", redactedErr(err))
		return s.editErrorResult(cctx, err, "the amend"), nil
	}
	return s.result(cctx, map[string]any{
		"schema_version":  mcpSchemaVersion,
		"session_id":      amended.SessionID,
		"revision_id":     amended.RevisionID,
		"revision_number": amended.RevisionNumber,
		"instruction": "Amended. The approval_url is unchanged; tell your human user the request " +
			"changed so they review the new version.",
	}), nil
}
