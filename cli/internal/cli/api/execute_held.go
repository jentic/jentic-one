package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// heldWaitDefault is how long `execute --wait` and `holds wait` wait for a
// held call's decision by default. A reviewer has the hold's whole window (a
// day by default) to decide, so past this the command exits 3
// (TIMEOUT_PENDING) and the call stays held: waiting again later is
// meaningful.
const heldWaitDefault = 10 * time.Minute

// heldPollTimeout bounds each control-plane read of a waited-for held
// execution.
const heldPollTimeout = 30 * time.Second

// executeHeld answers a call the broker held for human approval. Without
// --wait it prints the held envelope (the held execution and its review_url)
// as the execute data, shows the review link on stderr and exits 3; with
// --wait it waits for the held call to finish and prints the outcome exactly
// like a call that ran at once. It never re-sends the call.
func (a *app) executeHeld(cmd *cobra.Command, opts *executeOptions, res *agentops.ExecuteResult, held *agentops.Held) error {
	ctx := cmd.Context()
	ux.RenderHeld(ctx, a.Err, ux.HeldNotice{
		HeldExecutionID: held.HeldExecutionID, ReviewURL: held.ReviewURL, ExpiresAt: held.ExpiresAt, Waiting: opts.wait,
	})
	if !opts.wait {
		if err := a.executeOutput(cmd, opts, res); err != nil {
			return err
		}
		return heldPendingErr(held, "")
	}
	final, err := a.waitForHeldExecution(ctx, held.HeldExecutionID, opts.timeout)
	if err != nil {
		var timedOut *heldWaitTimeoutError
		if errors.As(err, &timedOut) {
			// Still held: the held envelope is the data an agent needs to
			// wait again or relay the link.
			if outErr := a.executeOutput(cmd, opts, res); outErr != nil {
				return outErr
			}
			return heldPendingErr(held, fmt.Sprintf(" after waiting %s", opts.timeout))
		}
		return err
	}
	return a.executeOutput(cmd, opts, final)
}

// heldPendingErr is the exit-3 outcome of a call still held for approval.
func heldPendingErr(held *agentops.Held, waited string) *ux.CodedError {
	return &ux.CodedError{
		Code: ux.CodeTimeoutPending,
		Msg: fmt.Sprintf("the call is held for human approval%s (held execution %s); it has not run",
			waited, held.HeldExecutionID),
		Actionable: fmt.Sprintf("Show the user the review_url (%s) — they approve or deny the call there. "+
			"Then run `jentic holds wait %s` for the result. Do not re-send the call.",
			held.ReviewURL, held.HeldExecutionID),
		Details: map[string]any{
			"held_execution_id": held.HeldExecutionID,
			"review_url":        held.ReviewURL,
			"expires_at":        held.ExpiresAt,
		},
	}
}

// heldWaitTimeoutError is the wait budget lapsing with the held call not yet
// finished.
type heldWaitTimeoutError struct {
	held *control.HeldExecutionDetailResponse
}

func (e *heldWaitTimeoutError) Error() string {
	return fmt.Sprintf("held execution %s is still %s", e.held.Id, heldExecutionStatus(e.held))
}

// heldExecutionStatus is where a held call stands (its execution_status), or
// "" when the control plane reports none.
func heldExecutionStatus(h *control.HeldExecutionDetailResponse) string {
	if h.ExecutionStatus == nil {
		return ""
	}
	return string(*h.ExecutionStatus)
}

// heldExecutionFinished reports whether a held call has nothing left to wait
// for: it ran, failed or was withdrawn — or it carries no execution status
// and its decision is no longer pending.
func heldExecutionFinished(h *control.HeldExecutionDetailResponse) bool {
	status := heldExecutionStatus(h)
	if status == "" {
		return h.State != control.ExecutionApprovalStatePending
	}
	return agentops.IsFinishedHeldStatus(status)
}

// waitForHeldExecution polls a held execution until it finishes or timeout
// lapses (a *heldWaitTimeoutError), then returns its outcome as the
// ExecuteResult the call would have produced synchronously. Heartbeats ride
// stderr so they never corrupt the JSON stdout an agent parses.
func (a *app) waitForHeldExecution(ctx context.Context, heldExecutionID string, timeout time.Duration) (*agentops.ExecuteResult, error) {
	client, err := a.controlClient(ctx)
	if err != nil {
		return nil, err
	}
	held, err := a.pollHeldExecution(ctx, client, heldExecutionID, timeout)
	if err != nil {
		return nil, err
	}
	return heldExecuteResult(ctx, client, held)
}

// pollHeldExecution polls GET /executions/holds/{id} on the App's shared
// approval cadence until the held call finishes or timeout lapses.
func (a *app) pollHeldExecution(
	ctx context.Context, client *control.ClientWithResponses, heldExecutionID string, timeout time.Duration,
) (*control.HeldExecutionDetailResponse, error) {
	start := time.Now()
	deadline := start.Add(timeout)
	delay, maxDelay, step := a.PollCadence()
	const heartbeatAfter = 2 * time.Second
	nextHeartbeat := start.Add(heartbeatAfter)
	for {
		held, err := getHeldExecution(ctx, client, heldExecutionID)
		if err != nil {
			return nil, err
		}
		if heldExecutionFinished(held) {
			return held, nil
		}
		if time.Now().After(deadline) {
			return nil, &heldWaitTimeoutError{held: held}
		}
		if now := time.Now(); now.After(nextHeartbeat) {
			fmt.Fprintln(a.Err, theme.StylesFromContext(ctx).Dimf(
				"  waiting for held execution %s (%s, %ds elapsed) …",
				heldExecutionID, heldExecutionStatus(held), int(now.Sub(start).Seconds())))
			nextHeartbeat = now.Add(5 * time.Second)
		}
		select {
		case <-ctx.Done():
			return nil, fmt.Errorf("wait for held execution %s: %w", heldExecutionID, ctx.Err())
		case <-time.After(min(delay, max(time.Until(deadline), 0))):
		}
		if delay < maxDelay {
			delay += step
		}
	}
}

// getHeldExecution reads one held execution; an unknown id (or one outside
// the caller's visibility) is RESOLVE_FAILED.
func getHeldExecution(ctx context.Context, client *control.ClientWithResponses, heldExecutionID string) (*control.HeldExecutionDetailResponse, error) {
	cctx, cancel := context.WithTimeout(ctx, heldPollTimeout)
	defer cancel()
	resp, callErr := client.GetHeldExecutionWithResponse(cctx, heldExecutionID)
	if err := apiErrorFor(resp, callErr); err != nil {
		var he *HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
			return nil, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("held execution %q not found", heldExecutionID),
				Actionable: "Re-check the id — a held execute prints it as held_execution_id — and run " +
					"`jentic holds wait <held_execution_id>` with the exact value.",
			}
		}
		return nil, fmt.Errorf("failed to read held execution %s: %w", heldExecutionID, classifyTransportErr(err))
	}
	if resp.JSON200 == nil {
		return nil, &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  fmt.Sprintf("unexpected backend response reading held execution %s (status %d)", heldExecutionID, resp.StatusCode()),
		}
	}
	return resp.JSON200, nil
}

// heldExecuteResult maps a finished held execution onto the ExecuteResult its
// call would have produced synchronously (agentops.ExecutionResultFromHeld),
// or the coded error for one that finished without a result.
func heldExecuteResult(ctx context.Context, client *control.ClientWithResponses, held *control.HeldExecutionDetailResponse) (*agentops.ExecuteResult, error) {
	status := heldExecutionStatus(held)
	details := map[string]any{
		"held_execution_id": held.Id,
		"state":             string(held.State),
		"execution_status":  status,
	}
	if status == agentops.HeldStatusCancelled || held.State == control.ExecutionApprovalStateWithdrawn {
		return nil, &ux.CodedError{
			Code:       ux.CodeBrokerDenied,
			Msg:        fmt.Sprintf("held execution %s was withdrawn; the call never ran", held.Id),
			Actionable: "Nothing ran. Re-send the call only if it is still wanted; it is held for a new decision.",
			Details:    details,
		}
	}
	cctx, cancel := context.WithTimeout(ctx, heldPollTimeout)
	defer cancel()
	resp, callErr := client.GetHeldExecutionResultWithResponse(cctx, held.Id)
	if err := apiErrorFor(resp, callErr); err != nil {
		var he *HTTPError
		if !errors.As(err, &he) {
			return nil, fmt.Errorf("failed to read the result of held execution %s: %w", held.Id, classifyTransportErr(err))
		}
		switch he.StatusCode {
		case http.StatusConflict:
			// A failed call with no recorded result: fall through below.
		case http.StatusGone:
			return nil, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("the result of held execution %s has expired", held.Id),
				Actionable: "The call finished but its result is past retention. Check `jentic history` for its " +
					"outcome before re-sending it.",
				Details: details,
			}
		default:
			return nil, fmt.Errorf("failed to read the result of held execution %s: %w", held.Id, classifyTransportErr(err))
		}
	} else if res, ok := agentops.ExecutionResultFromHeld(resp.HTTPResponse.Header.Get("Content-Type"), resp.Body, deref(held.ExecutionId)); ok {
		return res, nil
	}
	return nil, &ux.CodedError{
		Code: ux.CodeTransportError,
		Msg: fmt.Sprintf("held execution %s %s without an upstream response (decision %s)",
			held.Id, valueOr(status, "finished"), held.State),
		Actionable: "The call did not get an upstream answer (a network or upstream failure). " +
			"Check `jentic history`, then re-send it if it is still wanted.",
		Details: details,
	}
}
