package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// explainedAgentDirective is explain_held_call's agent_directive once the
// reason is recorded. Kept byte-identical with EXPLAINED_AGENT_DIRECTIVE in
// src/jentic_one/shared/jobs/hold.py.
const explainedAgentDirective = "The reviewer will see your reason next to the request. Show the user the " +
	"review_url if you have not yet, and end your turn. When they reply, call get_execution_result once " +
	"with this held_execution_id. Do not re-send the call."

// maxExplainReasonRunes is the longest reason the control plane accepts before
// cleaning (MAX_AGENT_TEXT_INPUT_LEN in src/jentic_one/shared/agent_text.py);
// longer is refused, not cut.
const maxExplainReasonRunes = 4096

// explainHeldTimeout bounds the one :explain request.
const explainHeldTimeout = 30 * time.Second

// explainReasonErr refuses a reason the control plane would: blank, or longer
// than maxExplainReasonRunes. Checked before any client or network work, so
// both lanes answer it the same way whatever the setup state.
func explainReasonErr(heldExecutionID, reason string) error {
	details := map[string]any{"held_execution_id": heldExecutionID}
	if strings.TrimSpace(reason) == "" {
		return &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        "a reason is required",
			Actionable: "Give one or two sentences on why you are making this call; the reviewer sees them next to the request.",
			Details:    details,
		}
	}
	if n := utf8.RuneCountInString(reason); n > maxExplainReasonRunes {
		return &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        fmt.Sprintf("the reason was rejected: at most %d characters, got %d", maxExplainReasonRunes, n),
			Actionable: "Give one or two sentences of plain text on why you are making this call.",
			Details:    details,
		}
	}
	return nil
}

// explainHeldLane names the next step a failed explain points at: the CLI
// command or the MCP tool that follows the held call.
type explainHeldLane struct {
	poll string // how to follow the held call
}

var (
	explainLaneCLI = explainHeldLane{poll: "`jentic executions poll <held_execution_id>`"}
	explainLaneMCP = explainHeldLane{poll: "get_execution_result"}
)

// explainHeldCall POSTs /executions/holds/{id}:explain: the agent that filed a
// pending held call tells the reviewer why it made it, once. Callers check the
// reason with explainReasonErr first. A refusal comes back as a coded error
// naming lane's next step.
func explainHeldCall(ctx context.Context, client *control.ClientWithResponses, heldExecutionID, reason string, lane explainHeldLane) (*control.HeldExecutionResponse, error) {
	resp, callErr := client.ExplainHeldExecutionWithResponse(ctx, heldExecutionID, control.ExplainHeldExecutionRequest{Reason: reason})
	if err := apiErrorFor(resp, callErr); err != nil {
		return nil, explainHeldErr(err, heldExecutionID, lane)
	}
	if resp.JSON200 == nil {
		return nil, &ux.CodedError{
			Code:    ux.CodeInternalError,
			Msg:     fmt.Sprintf("unexpected backend response explaining held execution %s (status %d)", heldExecutionID, resp.StatusCode()),
			Details: map[string]any{"held_execution_id": heldExecutionID},
		}
	}
	return resp.JSON200, nil
}

// explainHeldErr maps the :explain refusals onto coded errors: an unknown id
// (or one the caller did not file) is a 404, a call already decided or
// already explained a 409, a reason with no readable text a 400. Transport
// failures pass through classified.
func explainHeldErr(err error, heldExecutionID string, lane explainHeldLane) error {
	details := map[string]any{"held_execution_id": heldExecutionID}
	var he *HTTPError
	if !errors.As(err, &he) {
		return classifyTransportErr(err)
	}
	switch he.StatusCode {
	case http.StatusNotFound:
		return &ux.CodedError{
			Code: ux.CodeResolveFailed,
			Msg:  fmt.Sprintf("held execution %q not found", heldExecutionID),
			Actionable: "Re-check the id — it is the held_execution_id of the held (202) execute response; " +
				"only the agent that made the call can explain it.",
			Details: details,
		}
	case http.StatusConflict:
		if pt, _ := he.Fields()["type"].(string); pt == "held_execution_already_explained" {
			return &ux.CodedError{
				Code:       ux.CodeResolveFailed,
				Msg:        fmt.Sprintf("held execution %s is already explained", heldExecutionID),
				Actionable: "The reviewer already has your reason; it is given once. Follow the call with " + lane.poll + " once the user replies.",
				Details:    details,
			}
		}
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("held execution %s is no longer waiting for a decision: %s", heldExecutionID, he.Detail()),
			Actionable: "It was decided, expired or withdrawn, so a reason no longer helps. Follow it with " + lane.poll + ".",
			Details:    details,
		}
	case http.StatusBadRequest, http.StatusUnprocessableEntity:
		return &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        "the reason was rejected: " + he.Detail(),
			Actionable: "Give one or two sentences of plain text on why you are making this call.",
			Details:    details,
		}
	default:
		return err
	}
}

// newExecutionsExplainCmd is `jentic executions explain <held_execution_id>
// <reason>`: tell the reviewer why a held call is needed.
func newExecutionsExplainCmd(app *app) *cobra.Command {
	return &cobra.Command{
		Use:   "explain <held_execution_id> <reason>",
		Short: "Tell the reviewer why a held call is needed",
		Long: "explain sends the reviewer of a held call — one a permission rule held\n" +
			"for human approval — your reason for making it, in a sentence or two.\n" +
			"They see it next to the request, as your own claim. Only the agent that\n" +
			"made the call can explain it, once, while it waits for a decision.\n\n" +
			"Exit codes:\n" +
			"  0 — the reason is recorded\n" +
			"  1 — local/transport failure, or an empty reason\n" +
			"  2 — an unknown held execution id, or a call already decided or explained",
		Example: "  jentic executions explain held_2mXk... \"Customer 4821 asked for a refund in ticket #88\"",
		Args:    exactNamedArgs("<held_execution_id> <reason>", "held_execution_id", "reason"),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.executionsExplainE(cmd, args[0], args[1])
		},
	}
}

// executionsExplainE records the reason and reports the held call it is on.
func (a *app) executionsExplainE(cmd *cobra.Command, heldExecutionID, reason string) error {
	ctx := cmd.Context()
	aud := ux.FromContext(ctx)
	if err := explainReasonErr(heldExecutionID, reason); err != nil {
		return reportCoded(aud, err)
	}
	client, err := a.controlClient(ctx)
	if err != nil {
		return reportCoded(aud, err)
	}
	cctx, cancel := context.WithTimeout(ctx, explainHeldTimeout)
	defer cancel()
	held, err := explainHeldCall(cctx, client, heldExecutionID, reason, explainLaneCLI)
	if err != nil {
		return reportCoded(aud, err)
	}
	aud.Render(ux.Result{
		Status:   ux.StatusUpdated,
		Resource: "held_execution",
		ID:       held.Id,
		Message:  "reason sent to the reviewer",
		Fields: map[string]any{
			"agent_reason": deref(held.AgentReason),
			"review_url":   held.ReviewUrl,
		},
	})
	return nil
}

// explainHeldCallParams are explain_held_call's arguments.
var explainHeldCallParams = []paramSpec{heldExecutionIDSpec, {name: "reason", kind: paramText}}

var explainHeldCallSchema = map[string]any{
	"type":     "object",
	"required": []string{"held_execution_id", "reason"},
	"properties": map[string]any{
		"held_execution_id": map[string]any{
			"type": "string",
			"description": "The held execution to explain (\"held_execution\" is an accepted alias): the " +
				"held_execution_id of a held (202) execute response.",
		},
		"reason": map[string]any{
			"type": "string",
			"description": "One or two sentences on why you are making this call, for the reviewer, e.g. " +
				"\"Customer 4821 asked for a refund in ticket #88\". Shown as your own claim; control and " +
				"text-direction characters are removed and it is cut to 1024 characters.",
		},
	},
}

// explainHeldCallSpec declares explain_held_call: optional, once per held call.
func (s *mcpServer) explainHeldCallSpec() mcpToolSpec {
	return mcpToolSpec{
		tool: &mcp.Tool{
			Name:  "explain_held_call",
			Title: "Tell the reviewer why a held call is needed",
			Description: "Tell the reviewer of a held call (a 202 HELD execute response) why you are making it, " +
				"in one or two sentences; they see it next to the request, as your own claim. Optional, and " +
				"once per held call: only the agent that made the call can explain it, while it waits for a " +
				"decision. Call it right after the held response, before you end your turn; then show the user " +
				"the review_url and end your turn as the held response says. Returns {held_execution_id, state, " +
				"status, agent_reason, review_url, agent_directive}. " +
				`Example: {"held_execution_id": "held_abc123", "reason": "Customer 4821 asked for a refund in ticket #88"}.`,
			InputSchema: explainHeldCallSchema,
			Annotations: &mcp.ToolAnnotations{},
		},
		handler: s.handleExplainHeldCall,
	}
}

func (s *mcpServer) handleExplainHeldCall(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())

	args, err := normalizeToolArgs(req.Params.Arguments, explainHeldCallParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	heldExecutionID, _ := args["held_execution_id"].(string)
	reason, _ := args["reason"].(string)
	if heldExecutionID == "" {
		return nil, invalidParams(errors.New(`explain_held_call requires "held_execution_id" (from a held 202 execute response)`))
	}
	if err := explainReasonErr(heldExecutionID, reason); err != nil {
		return s.softError(ctx, err), nil
	}

	cctx, cancel := context.WithTimeout(ctx, mcpCallTimeout)
	defer cancel()
	client, err := s.app.controlClient(cctx)
	if err != nil {
		s.logger.Warn("explain_held_call failed", "error", redactedErr(err))
		return s.softError(cctx, err), nil
	}
	held, err := explainHeldCall(cctx, client, heldExecutionID, reason, explainLaneMCP)
	if err != nil {
		var coded *ux.CodedError
		if errors.As(err, &coded) && coded.Code == ux.CodeResolveFailed {
			return s.softErrorNext(cctx, err, "get_execution_result"), nil
		}
		return s.softError(cctx, err), nil
	}
	var status any
	if held.ExecutionStatus != nil {
		status = string(*held.ExecutionStatus)
	}
	return s.result(cctx, map[string]any{
		"schema_version":    mcpSchemaVersion,
		"held_execution_id": held.Id,
		"state":             string(held.State),
		"status":            status,
		"agent_reason":      deref(held.AgentReason),
		"review_url":        held.ReviewUrl,
		"agent_directive":   explainedAgentDirective,
	}), nil
}
