package api

import (
	"context"
	"fmt"
	"net/http"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// Event types a vendor CLI command's trace can carry that explain a failure.
const (
	eventPBACDenied        = "broker.pbac_denied"
	eventApprovalRequested = "execution.approval_requested"
	eventApprovalJoined    = "execution.approval_joined"
)

// traceLookupLimit bounds each post-exit read: one command rarely makes more
// calls, and the directive needs only the first that explains the failure.
const traceLookupLimit = 100

// traceFindings is what Jentic recorded under one command's trace.
type traceFindings struct {
	held       *control.EventResponse
	denied     *control.EventResponse
	failed     *control.ExecutionResponse
	executions int
	readErr    error
}

// postExitDirective explains a failed vendor CLI command from its trace: a
// call held for approval, a call a rule denied, a call the vendor answered
// with an error, or none recorded at all. Best effort: when the trace cannot
// be read, the directive still names it.
func postExitDirective(ctx context.Context, client *control.ClientWithResponses, prof vendorProfile, traceID string) ux.VendorCLIDirective {
	f := readTrace(ctx, client, traceID)
	history := "jentic history export --trace " + traceID + " --include-failures"
	out := ux.VendorCLIDirective{TraceID: traceID}
	switch {
	case f.held != nil:
		params := map[string]any{}
		data := deref(f.held.Data)
		if id, ok := data["held_execution_id"].(string); ok && id != "" {
			params["held_execution_id"] = id
			params["suggested_command"] = "jentic executions poll " + id
		}
		if u, ok := data["review_url"].(string); ok && u != "" {
			params["review_url"] = u
		}
		out.Directive = ux.Directive{
			Strategy:   "prompt_human",
			Parameters: params,
			Instruction: fmt.Sprintf("A call this `%s` command made is held for human approval (%s). Show the user the review link; "+
				"do not re-run the command. Once approved the call runs once on its own; steps the command would have taken "+
				"after it did not happen.", prof.Binary, f.held.Summary),
		}
	case f.denied != nil:
		params := map[string]any{"suggested_command": "jentic whoami"}
		data := deref(f.denied.Data)
		for _, k := range []string{"reason", "operation_id", "operation_method", "operation_path"} {
			if v, ok := data[k].(string); ok && v != "" {
				params[k] = v
			}
		}
		out.Directive = ux.Directive{
			Strategy:   "prompt_human",
			Parameters: params,
			Instruction: fmt.Sprintf("Jentic denied a call this `%s` command made (%s). Your permission rules do not allow it: "+
				"change what you ask for, or ask your operator to allow it.", prof.Binary, f.denied.Summary),
		}
	case f.failed != nil:
		status := deref(f.failed.HttpStatus)
		params := map[string]any{"suggested_command": history, "execution_id": f.failed.ExecutionId}
		if status != 0 {
			params["http_status"] = status
		}
		op := deref(f.failed.OperationId)
		if op == "" {
			op = deref(f.failed.OperationMethod) + " " + deref(f.failed.OperationPath)
		}
		out.Directive = ux.Directive{
			Strategy:   "fatal",
			Parameters: params,
			Instruction: fmt.Sprintf("Jentic ran the call (%s) and %s answered HTTP %d. Read `%s`'s error output above; "+
				"%s", op, prof.Display, status, prof.Binary, failedHint(status, prof)),
		}
	case f.readErr != nil:
		out.Directive = ux.Directive{
			Strategy:   "fatal",
			Parameters: map[string]any{"suggested_command": history},
			Instruction: fmt.Sprintf("`%s` failed; Jentic's record of its calls could not be read (%s). Inspect the trace with the command below.",
				prof.Binary, oneLine(f.readErr)),
		}
	case f.executions > 0:
		out.Directive = ux.Directive{
			Strategy:   "fatal",
			Parameters: map[string]any{"suggested_command": history},
			Instruction: fmt.Sprintf("Every call this `%s` command made through Jentic succeeded; the failure is `%s`'s own. "+
				"Read its error output above.", prof.Binary, prof.Binary),
		}
	default:
		out.Directive = ux.Directive{
			Strategy:   "prompt_human",
			Parameters: map[string]any{"suggested_command": "jentic whoami"},
			Instruction: fmt.Sprintf("`%s` failed and Jentic recorded no call for it. Either it failed before calling %s, or Jentic "+
				"refused the call before running it: an endpoint not in Jentic's registry, or no %s credential connected for you "+
				"(see the `Jentic:` message in `%s`'s error output). If you have no %s credential, ask your operator to connect one "+
				"(`jentic connect`).", prof.Binary, prof.Display, prof.Display, prof.Binary, prof.Display),
		}
	}
	return out
}

func failedHint(status int, prof vendorProfile) string {
	switch status {
	case http.StatusUnauthorized, http.StatusForbidden:
		return fmt.Sprintf("the %s credential Jentic used may lack the permission this needs; ask your operator to check it.", prof.Display)
	case http.StatusNotFound:
		return "check the identifiers you passed."
	case http.StatusTooManyRequests:
		return "wait, then retry."
	}
	return "fix the request and retry."
}

// readTrace reads the trace's events (denials, holds) and executions with
// the agent's own events:read / executions:read, both scoped to the agent.
func readTrace(ctx context.Context, client *control.ClientWithResponses, traceID string) traceFindings {
	var f traceFindings
	callCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), vendorCLICallTimeout)
	defer cancel()

	types := []string{eventPBACDenied, eventApprovalRequested, eventApprovalJoined}
	evResp, err := client.ListEventsWithResponse(callCtx, &control.ListEventsParams{
		TraceId: &traceID, EventType: &types, Limit: ptr(traceLookupLimit),
	})
	if err := apiErrorFor(evResp, err); err != nil {
		f.readErr = err
	} else if evResp.JSON200 != nil {
		for i := range evResp.JSON200.Data {
			ev := &evResp.JSON200.Data[i]
			switch ev.Type {
			case eventApprovalRequested, eventApprovalJoined:
				if f.held == nil {
					f.held = ev
				}
			case eventPBACDenied:
				if f.denied == nil {
					f.denied = ev
				}
			}
		}
	}

	exResp, err := client.ListExecutionsWithResponse(callCtx, &control.ListExecutionsParams{
		TraceId: &traceID, Limit: ptr(traceLookupLimit),
	})
	if err := apiErrorFor(exResp, err); err != nil {
		if f.readErr == nil {
			f.readErr = err
		}
		return f
	}
	if exResp.JSON200 != nil {
		f.executions = len(exResp.JSON200.Data)
		for i := range exResp.JSON200.Data {
			ex := &exResp.JSON200.Data[i]
			if ex.Status == "failed" || deref(ex.HttpStatus) >= http.StatusBadRequest {
				f.failed = ex
				break
			}
		}
	}
	return f
}
