package api

// access_edit.go holds the agent's side of changing its own access: asking for
// an edit of one credential binding (POST /integrations:edit-connection) and
// amending an open request (POST /connect-sessions/{id}:amend). `jentic access
// edit` / `jentic access amend` and the request_access_change / amend_request
// MCP tools share it. A human approves every change in the browser; nothing
// here grants access by itself.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// editRulesMax mirrors the route's bound on each rule list (max_length=100).
const editRulesMax = 100

// editInput is one edit or amend ask, before it becomes a request body.
type editInput struct {
	// addJSON is a JSON list of rules to add, each a rule object or
	// {"rule": …, "before": "<rule id>"}; a denial's suggested_rules fit as-is.
	addJSON json.RawMessage
	// before places every added rule that names no position of its own.
	before string
	remove []string
	// setJSON is the full replacement rule list.
	setJSON json.RawMessage
	// The scope change, shaped like the rule change: add/remove or set.
	addScopes    []string
	removeScopes []string
	setScopes    []string
	resume       *bool
	reason       *string
	// Create sessions only (amend).
	requestedRulesJSON json.RawMessage
	requestedScopes    []string
}

// decodeEditRules decodes a rule list, refusing unknown keys and effects so a
// malformed ask is an argument error here rather than a route 422.
func decodeEditRules(raw json.RawMessage, field string) ([]control.PermissionRuleSchema, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var rules []control.PermissionRuleSchema
	if err := dec.Decode(&rules); err != nil {
		return nil, fmt.Errorf("%s must be a JSON list of rule objects "+
			"({effect, methods, path, match_mode, operations}): %w", field, err)
	}
	if len(rules) > editRulesMax {
		return nil, fmt.Errorf("%s must list at most %d rules, got %d", field, editRulesMax, len(rules))
	}
	for i, r := range rules {
		switch r.Effect {
		case "allow", "deny", "require-approval":
		default:
			return nil, fmt.Errorf("%s[%d].effect must be \"allow\", \"deny\" or \"require-approval\", got %q", field, i, r.Effect)
		}
	}
	return rules, nil
}

// decodeEditAdds decodes the rules to add: bare rule objects or {rule, before}.
func decodeEditAdds(raw json.RawMessage, before string) ([]control.EditRuleAdd, error) {
	var items []json.RawMessage
	if err := json.Unmarshal(raw, &items); err != nil {
		return nil, fmt.Errorf("add must be a JSON list: %w", err)
	}
	if len(items) > editRulesMax {
		return nil, fmt.Errorf("add must list at most %d rules, got %d", editRulesMax, len(items))
	}
	adds := make([]control.EditRuleAdd, 0, len(items))
	for i, item := range items {
		var wrapped struct {
			Rule   json.RawMessage `json:"rule"`
			Before string          `json:"before"`
		}
		ruleRaw, position := item, before
		if err := json.Unmarshal(item, &wrapped); err == nil && len(wrapped.Rule) > 0 {
			ruleRaw = wrapped.Rule
			if wrapped.Before != "" {
				position = wrapped.Before
			}
		}
		rules, err := decodeEditRules(append(append([]byte{'['}, ruleRaw...), ']'), fmt.Sprintf("add[%d]", i))
		if err != nil {
			return nil, err
		}
		add := control.EditRuleAdd{Rule: rules[0]}
		if position != "" {
			add.Before = ptr(position)
		}
		adds = append(adds, add)
	}
	return adds, nil
}

// editRules builds the rule change, or nil when the input names none.
func (in editInput) editRules() (*control.EditRulesRequest, error) {
	hasSet := len(in.setJSON) > 0
	if hasSet && (len(in.addJSON) > 0 || len(in.remove) > 0) {
		return nil, errors.New("pass either add/remove or set, not both")
	}
	if hasSet {
		rules, err := decodeEditRules(in.setJSON, "set")
		if err != nil {
			return nil, err
		}
		return &control.EditRulesRequest{Set: &rules}, nil
	}
	if len(in.addJSON) == 0 && len(in.remove) == 0 {
		return nil, nil
	}
	out := &control.EditRulesRequest{}
	if len(in.addJSON) > 0 {
		adds, err := decodeEditAdds(in.addJSON, in.before)
		if err != nil {
			return nil, err
		}
		out.Add = &adds
	}
	if len(in.remove) > 0 {
		remove := append([]string(nil), in.remove...)
		out.Remove = &remove
	}
	return out, nil
}

func (in editInput) editScopes() (*control.EditScopesRequest, error) {
	if in.setScopes != nil && (len(in.addScopes) > 0 || len(in.removeScopes) > 0) {
		return nil, errors.New("pass either add/remove or set for scopes, not both")
	}
	if in.setScopes != nil {
		set := append([]string{}, in.setScopes...)
		return &control.EditScopesRequest{Set: &set}, nil
	}
	if len(in.addScopes) == 0 && len(in.removeScopes) == 0 {
		return nil, nil
	}
	out := &control.EditScopesRequest{}
	if len(in.addScopes) > 0 {
		add := append([]string(nil), in.addScopes...)
		out.Add = &add
	}
	if len(in.removeScopes) > 0 {
		remove := append([]string(nil), in.removeScopes...)
		out.Remove = &remove
	}
	return out, nil
}

// editCreated is the 201 body of :edit-connection (poll_token decoded, never re-emitted).
type editCreated struct {
	SessionID    string `json:"session_id"`
	ApprovalURL  string `json:"approval_url"`
	PollToken    string `json:"poll_token"`
	RevisionID   string `json:"revision_id"`
	ResolvedFlow string `json:"resolved_flow"`
}

// controlProblem is the slice of a control-plane problem+json body the edit
// surfaces act on.
type controlProblem struct {
	Type           string          `json:"type"`
	Detail         string          `json:"detail"`
	SessionID      string          `json:"session_id"`
	AgentDirective json.RawMessage `json:"agent_directive"`
	Directive      struct {
		Strategy    string `json:"strategy"`
		Instruction string `json:"human_readable_instruction"`
		Parameters  struct {
			CredentialID     string `json:"credential_id"`
			SuggestedCommand string `json:"suggested_command"`
		} `json:"parameters"`
	} `json:"-"`
}

func parseControlProblem(err error) (*controlProblem, bool) {
	var he *HTTPError
	if !errors.As(err, &he) {
		return nil, false
	}
	var p controlProblem
	if json.Unmarshal([]byte(he.Body), &p) != nil {
		return nil, false
	}
	if len(p.AgentDirective) > 0 {
		_ = json.Unmarshal(p.AgentDirective, &p.Directive)
	}
	return &p, true
}

// editRequestCoded maps a refused edit or amend to the coded taxonomy. A refusal
// carrying the check_access directive means the caller's picture of its access
// is wrong: the actionable step is to read it first.
func editRequestCoded(err error, what string) *ux.CodedError {
	p, ok := parseControlProblem(err)
	if !ok {
		return asCoded(err)
	}
	coded := &ux.CodedError{Code: ux.CodeResolveFailed, Msg: fmt.Sprintf("%s was refused (%s): %s", what, p.Type, p.Detail)}
	if p.Directive.Strategy == "check_access" {
		coded.Actionable = p.Directive.Instruction
		if cmd := p.Directive.Parameters.SuggestedCommand; cmd != "" {
			coded.Actionable += " Then: " + cmd
		}
		return coded
	}
	var he *HTTPError
	if errors.As(err, &he) && (he.StatusCode == http.StatusUnauthorized || he.StatusCode == http.StatusForbidden) {
		return mcpCoded(err)
	}
	coded.Actionable = "Fix the request the error names, then retry."
	return coded
}

// requestAccessChange starts an edit of the caller's own binding. An edit
// already open for it comes back as (nil, open session id, nil).
func (a *app) requestAccessChange(ctx context.Context, credentialID string, in editInput) (*editCreated, string, error) {
	rules, err := in.editRules()
	if err != nil {
		return nil, "", &ux.CodedError{Code: ux.CodeMissingArgument, Msg: err.Error()}
	}
	scopes, err := in.editScopes()
	if err != nil {
		return nil, "", &ux.CodedError{Code: ux.CodeMissingArgument, Msg: err.Error()}
	}
	client, err := a.controlClient(ctx)
	if err != nil {
		return nil, "", err
	}
	// Never set AgentId: the caller IS the agent (an override is refused).
	body := control.EditConnectionRequest{
		CredentialId: credentialID,
		Rules:        rules,
		Scopes:       scopes,
		Resume:       in.resume,
		Reason:       in.reason,
	}
	resp, callErr := client.IntegrationsEditConnectionWithResponse(ctx, body)
	if err := apiErrorFor(resp, callErr); err != nil {
		if p, ok := parseControlProblem(err); ok && p.Type == "edit_open" && p.SessionID != "" {
			return nil, p.SessionID, nil
		}
		return nil, "", err
	}
	var created editCreated
	if err := json.Unmarshal(resp.Body, &created); err != nil {
		return nil, "", fmt.Errorf("decode /integrations:edit-connection response: %w", err)
	}
	return &created, "", nil
}

// amendedSession is the 200 body of :amend.
type amendedSession struct {
	SessionID      string `json:"session_id"`
	RevisionID     string `json:"revision_id"`
	RevisionNumber int    `json:"revision_number"`
}

// amendRequest changes an open session's ask (the caller must have opened it).
func (a *app) amendRequest(ctx context.Context, sessionID string, in editInput) (*amendedSession, error) {
	rules, err := in.editRules()
	if err != nil {
		return nil, &ux.CodedError{Code: ux.CodeMissingArgument, Msg: err.Error()}
	}
	scopes, err := in.editScopes()
	if err != nil {
		return nil, &ux.CodedError{Code: ux.CodeMissingArgument, Msg: err.Error()}
	}
	body := control.AmendSessionRequest{Rules: rules, Scopes: scopes, Resume: in.resume, Reason: in.reason}
	if len(in.requestedRulesJSON) > 0 {
		requested, err := decodeEditRules(in.requestedRulesJSON, "requested_permission_rules")
		if err != nil {
			return nil, &ux.CodedError{Code: ux.CodeMissingArgument, Msg: err.Error()}
		}
		body.RequestedPermissionRules = &requested
	}
	if len(in.requestedScopes) > 0 {
		scopes := append([]string(nil), in.requestedScopes...)
		body.RequestedScopes = &scopes
	}
	if body == (control.AmendSessionRequest{}) {
		return nil, &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        "an amend must change something",
			Actionable: "Pass the rules, scopes, resume or reason to change.",
		}
	}
	client, err := a.controlClient(ctx)
	if err != nil {
		return nil, err
	}
	resp, callErr := client.AmendConnectSessionWithResponse(ctx, sessionID, body)
	if err := apiErrorFor(resp, callErr); err != nil {
		return nil, err
	}
	var amended amendedSession
	if err := json.Unmarshal(resp.Body, &amended); err != nil {
		return nil, fmt.Errorf("decode :amend response: %w", err)
	}
	return &amended, nil
}

// accessEditConflicted is an open request the binding changed under in a way
// it can't follow: the agent amends it against the access as it is now.
func accessEditConflicted(sessionID, credentialID string) *ux.CodedError {
	return &ux.CodedError{
		Code: ux.CodeResolveFailed,
		Msg:  fmt.Sprintf("access change %s conflicts with a change to your access since you asked", sessionID),
		Actionable: "Read your access again with `jentic access show " + credentialID + "`, then " +
			"amend the request (`jentic access amend " + sessionID + "`) so it fits the access as it is now.",
	}
}

const accessEditNextStep = "Relay the approval_url to your human operator — they review the change " +
	"in the browser; you cannot approve it yourself. Once they confirm, check it with " +
	"`jentic access show <credential_id>` and retry the call that was blocked."

func accessEditOpenNextStep(sessionID string) string {
	return "You already asked for a change to this binding and it is still open: amend it " +
		"(`jentic access amend " + sessionID + "`) instead of asking again, and relay its approval_url."
}

// editFlags registers the rule/scope/resume/reason flags shared by edit and amend.
func editFlags(cmd *cobra.Command, in *editInput, addJSON, setJSON *string, resume *bool, reason *string) {
	cmd.Flags().StringVar(addJSON, "add", "",
		`rules to add, as a JSON list of rules or {"rule": …, "before": "<rule id>"} (a denial's suggested_rules fit as-is)`)
	cmd.Flags().StringVar(&in.before, "before", "", "rule id to insert the added rules before (first match wins); appended when omitted")
	cmd.Flags().StringSliceVar(&in.remove, "remove", nil, "rule ids to remove (from `jentic access show`)")
	cmd.Flags().StringVar(setJSON, "set", "", "the full rule list to replace the current one with, as JSON (not combined with --add/--remove)")
	cmd.Flags().StringSliceVar(&in.addScopes, "add-scopes", nil, "OAuth scopes to add (the approver signs in to the vendor again)")
	cmd.Flags().StringSliceVar(&in.removeScopes, "remove-scopes", nil, "OAuth scopes to remove")
	cmd.Flags().StringSliceVar(&in.setScopes, "set-scopes", nil, "the full OAuth scope list to replace the grant's with (not combined with --add-scopes/--remove-scopes)")
	cmd.Flags().BoolVar(resume, "resume", false, "switch a suspended binding back on")
	cmd.Flags().StringVar(reason, "reason", "", "why you need the change (shown to the approver; max 1024 chars)")
}

func finishEditInput(cmd *cobra.Command, in *editInput, addJSON, setJSON string, resume bool, reason string) error {
	if addJSON != "" {
		in.addJSON = json.RawMessage(addJSON)
	}
	if setJSON != "" {
		in.setJSON = json.RawMessage(setJSON)
	}
	if cmd.Flags().Changed("resume") {
		in.resume = ptr(resume)
	}
	if cmd.Flags().Changed("reason") {
		if len(reason) > connectReasonMax {
			return fmt.Errorf("--reason must be at most %d characters, got %d", connectReasonMax, len(reason))
		}
		in.reason = ptr(reason)
	}
	return nil
}

func newAccessEditCmd(a *app) *cobra.Command {
	var in editInput
	var addJSON, setJSON, reason string
	var resume, wait bool
	var timeout time.Duration
	cmd := &cobra.Command{
		Use:   "edit <credential_id>",
		Short: "Ask for a change to your access through one credential (a human approves it)",
		Long: "edit asks your human user to change what your binding to the credential lets you\n" +
			"do: rules to add (--add, optionally --before a rule id), remove (--remove) or\n" +
			"replace (--set), OAuth scopes to add, remove or replace (--add-scopes,\n" +
			"--remove-scopes, --set-scopes), or resuming a suspended binding\n" +
			"(--resume). Read your access first with `jentic access show`: rule ids come from\n" +
			"there, and a denial's suggested_rules fit --add as-is. Put every change you need\n" +
			"in one request. It prints the approval_url to relay; nothing changes until they\n" +
			"approve. If a request for this binding is already open, amend that one with\n" +
			"`jentic access amend <session_id>` instead. --wait polls until it is applied or ends.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
			if err := finishEditInput(cmd, &in, addJSON, setJSON, resume, reason); err != nil {
				return reportCoded(aud, &ux.CodedError{Code: ux.CodeMissingArgument, Msg: err.Error()})
			}
			if wait && timeout <= 0 {
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeMissingArgument, Msg: fmt.Sprintf("--timeout must be positive with --wait, got %s", timeout),
				})
			}
			created, openID, err := a.requestAccessChange(cmd.Context(), args[0], in)
			if err != nil {
				return reportCoded(aud, editRequestCoded(err, "the access change"))
			}
			if openID != "" {
				aud.Render(map[string]any{"session_id": openID, "already_open": true, "next_step": accessEditOpenNextStep(openID)})
				return nil
			}
			envelope := map[string]any{
				"session_id":    created.SessionID,
				"approval_url":  created.ApprovalURL,
				"revision_id":   created.RevisionID,
				"resolved_flow": created.ResolvedFlow,
				"next_step":     accessEditNextStep,
			}
			if !wait {
				aud.Render(envelope)
				return nil
			}
			fmt.Fprintln(a.Err, "Relay this approval_url to your human operator: "+created.ApprovalURL)
			client, err := a.controlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}
			final, err := a.waitForConnectSession(cmd.Context(), client, created.SessionID, created.PollToken, timeout, false)
			if errors.Is(err, errConnectSessionEnded) {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeResolveFailed,
					Msg:        fmt.Sprintf("access change %s ended", created.SessionID),
					Actionable: "Check `jentic access show " + args[0] + "`; ask your human user whether they rejected it before asking again.",
				})
			}
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}
			if final.ErrorCode == "conflicted" {
				return reportCoded(aud, accessEditConflicted(created.SessionID, args[0]))
			}
			if final.Status == "applied" {
				envelope["status"] = final.Status
				envelope["next_step"] = "Applied. Check it with `jentic access show " + args[0] + "` and retry the call that was blocked."
				aud.Render(envelope)
				return nil
			}
			return reportCoded(aud, connectOutcomeCoded(created.SessionID, final.Status, final.ErrorCode, "jentic access edit "+args[0]))
		},
	}
	editFlags(cmd, &in, &addJSON, &setJSON, &resume, &reason)
	cmd.Flags().BoolVar(&wait, "wait", false, "poll the request until it is applied or ends")
	cmd.Flags().DurationVar(&timeout, "timeout", connectWaitDefault, "how long --wait polls before exiting 3 (TIMEOUT_PENDING)")
	return cmd
}

func newAccessAmendCmd(a *app) *cobra.Command {
	var in editInput
	var addJSON, setJSON, reason, requestedRules string
	var resume bool
	cmd := &cobra.Command{
		Use:   "amend <session_id>",
		Short: "Change an open request you made, before it is approved",
		Long: "amend changes what an open request asks for; the reviewer sees the new version and\n" +
			"approves it once. The fields you pass replace those parts of the ask and the rest\n" +
			"stay. For a change to your access (`jentic access edit`) pass --add/--remove/--set,\n" +
			"--add-scopes/--remove-scopes/--set-scopes, --resume or --reason; for a new\n" +
			"connection (`jentic connect`) pass --requested-rules, --requested-scopes or\n" +
			"--reason. A request that was already approved or ended cannot be amended: ask\n" +
			"again instead.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
			if err := finishEditInput(cmd, &in, addJSON, setJSON, resume, reason); err != nil {
				return reportCoded(aud, &ux.CodedError{Code: ux.CodeMissingArgument, Msg: err.Error()})
			}
			if requestedRules != "" {
				in.requestedRulesJSON = json.RawMessage(requestedRules)
			}
			amended, err := a.amendRequest(cmd.Context(), args[0], in)
			if err != nil {
				return reportCoded(aud, editRequestCoded(err, "the amend"))
			}
			aud.Render(map[string]any{
				"session_id":      amended.SessionID,
				"revision_id":     amended.RevisionID,
				"revision_number": amended.RevisionNumber,
				"next_step": "Amended. The approval_url is unchanged; tell your human operator the " +
					"request changed so they review the new version.",
			})
			return nil
		},
	}
	editFlags(cmd, &in, &addJSON, &setJSON, &resume, &reason)
	cmd.Flags().StringVar(&requestedRules, "requested-rules", "", "for a new connection: the rules to ask for, as a JSON list (replaces the ask)")
	cmd.Flags().StringSliceVar(&in.requestedScopes, "requested-scopes", nil, "for a new connection: the scopes to ask for (replaces the ask)")
	return cmd
}
