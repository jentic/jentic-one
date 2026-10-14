package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// heldWaitDefault is how long `execute --wait` and `executions poll` wait for
// a held call by default. A reviewer has the hold's whole window (a day by
// default) to decide, so past this the command exits 3 (TIMEOUT_PENDING) and
// the call stays held: polling again later is meaningful.
const heldWaitDefault = 30 * time.Second

// heldWaitMax caps --timeout: a longer wait belongs in a later poll, not one
// blocking command.
const heldWaitMax = 5 * time.Minute

// heldPollTimeout bounds each control-plane read of a followed held
// execution.
const heldPollTimeout = 30 * time.Second

// heldTimeout is the --timeout of `executions poll` and `execute --wait`: a Go
// duration ("30s", "1m") or a bare integer of seconds ("30"). 0 checks once
// and answers at once; anything above heldWaitMax is refused at parse time.
type heldTimeout time.Duration

func (t *heldTimeout) String() string { return time.Duration(*t).String() }

func (t *heldTimeout) Type() string { return "duration" }

func (t *heldTimeout) Set(s string) error {
	d, err := parseHeldTimeout(s)
	if err != nil {
		return err
	}
	*t = heldTimeout(d)
	return nil
}

// parseHeldTimeout parses a --timeout value: a bare integer is seconds,
// anything else a Go duration, within [0, heldWaitMax].
func parseHeldTimeout(s string) (time.Duration, error) {
	s = strings.TrimSpace(s)
	var d time.Duration
	if n, err := strconv.ParseInt(s, 10, 64); err == nil {
		if n > int64(heldWaitMax/time.Second) {
			return 0, fmt.Errorf("%s seconds exceeds the maximum of %s; poll again later instead of waiting longer", s, heldWaitMax)
		}
		d = time.Duration(n) * time.Second
	} else {
		parsed, perr := time.ParseDuration(s)
		if perr != nil {
			return 0, fmt.Errorf("want a duration like 30s or 1m, or a number of seconds like 30")
		}
		d = parsed
	}
	if d < 0 {
		return 0, fmt.Errorf("must not be negative (0 checks once and answers at once)")
	}
	if d > heldWaitMax {
		return 0, fmt.Errorf("%s exceeds the maximum of %s; poll again later instead of waiting longer", d, heldWaitMax)
	}
	return d, nil
}

// addHeldTimeoutFlag registers --timeout (default heldWaitDefault) on cmd,
// bound to *dst.
func addHeldTimeoutFlag(cmd *cobra.Command, dst *time.Duration, usage string) {
	*dst = heldWaitDefault
	cmd.Flags().Var((*heldTimeout)(dst), "timeout", usage)
}

// heldRef is what the CLI knows about a held call it follows: its id and,
// once seen, its review link and expiry.
type heldRef struct {
	ID        string
	ReviewURL string
	ExpiresAt string
}

func (r *heldRef) notice() ux.HeldNotice {
	return ux.HeldNotice{HeldExecutionID: r.ID, ReviewURL: r.ReviewURL, ExpiresAt: r.ExpiresAt}
}

// learn records the review link and expiry a held execution read reports.
func (r *heldRef) learn(h *control.HeldExecutionDetailResponse) {
	if h.ReviewUrl != "" {
		r.ReviewURL = h.ReviewUrl
	}
	if !h.ExpiresAt.IsZero() {
		r.ExpiresAt = h.ExpiresAt.UTC().Format(time.RFC3339)
	}
}

// pollCommand is the next step for a held call that has not finished.
func (r *heldRef) pollCommand() string {
	return "jentic executions poll " + r.ID
}

// details is the held call's identity for an error envelope's details.
func (r *heldRef) details() map[string]any {
	d := map[string]any{"held_execution_id": r.ID}
	if r.ReviewURL != "" {
		d["review_url"] = r.ReviewURL
	}
	if r.ExpiresAt != "" {
		d["expires_at"] = r.ExpiresAt
	}
	return d
}

// executeHeld answers a call the broker held for human approval. Without
// --wait it prints the held envelope (the held execution and its review_url)
// as the execute data, shows the review link on stderr and exits 3; with
// --wait it follows the held call up to --timeout and prints the outcome
// exactly like a call that ran at once. It never re-sends the call.
func (a *app) executeHeld(cmd *cobra.Command, opts *executeOptions, res *agentops.ExecuteResult, held *agentops.Held) error {
	ref := &heldRef{ID: held.HeldExecutionID, ReviewURL: held.ReviewURL, ExpiresAt: held.ExpiresAt}
	notice := ref.notice()
	notice.Waiting = opts.wait
	ux.RenderHeld(cmd.Context(), a.Err, notice)
	if !opts.wait {
		if err := a.executeOutput(cmd, opts, res); err != nil {
			return err
		}
		return heldPendingErr(ref)
	}
	// Still held at the timeout: the held envelope is the data an agent
	// needs to poll again or relay the link.
	return a.followHeld(cmd, opts, ref, func() error { return a.executeOutput(cmd, opts, res) })
}

// heldPendingErr is the exit-3 outcome of a held execute without --wait.
func heldPendingErr(ref *heldRef) *ux.CodedError {
	details := ref.details()
	details["next_command"] = ref.pollCommand()
	return &ux.CodedError{
		Code: ux.CodeTimeoutPending,
		Msg:  fmt.Sprintf("the call is held for human approval (held execution %s); it has not run", ref.ID),
		Actionable: fmt.Sprintf("Show the user the review_url (%s) — they approve or deny the call there — "+
			"and end your turn. When they reply, run `%s` once for the result. Do not re-send the call.",
			ref.ReviewURL, ref.pollCommand()),
		Details: details,
	}
}

// followHeld follows a held call up to opts.timeout and renders its outcome:
// the finished call exactly like `jentic execute` (named by its held
// execution id), or — still unfinished, or the poll itself failed — the held
// execution id, review link and poll command on stderr and in the error
// envelope. pendingOutput, when set, renders the stdout data of a call still
// held at the timeout.
func (a *app) followHeld(cmd *cobra.Command, opts *executeOptions, ref *heldRef, pendingOutput func() error) error {
	ctx := cmd.Context()
	final, err := a.waitForHeldExecution(ctx, ref, opts.timeout)
	if err == nil {
		finished := *opts
		finished.heldExecutionID = ref.ID
		return a.executeOutput(cmd, &finished, final)
	}
	var timedOut *heldWaitTimeoutError
	if errors.As(err, &timedOut) {
		if pendingOutput != nil {
			if outErr := pendingOutput(); outErr != nil {
				return outErr
			}
		}
		approved := timedOut.held.State != control.HeldExecutionStatePending
		ux.RenderHeldUnfinished(ctx, a.Err, ref.notice(), heldUnfinishedHeadline(timedOut.held, opts.timeout))
		return heldStillPendingErr(ref, timedOut.held, opts.timeout, approved)
	}
	var pollErr *heldPollError
	if errors.As(err, &pollErr) {
		ux.RenderHeldUnfinished(ctx, a.Err, ref.notice(),
			fmt.Sprintf("Could not read held execution %s — the call may still be held:", ref.ID))
		return heldPollFailedErr(ref, pollErr.err)
	}
	return err
}

func heldUnfinishedHeadline(h *control.HeldExecutionDetailResponse, waited time.Duration) string {
	if h.State == control.HeldExecutionStatePending {
		return fmt.Sprintf("Still held after %s — this call is waiting for a human decision:", waited)
	}
	return fmt.Sprintf("Approved, not finished after %s — the call is %s:", waited, valueOr(heldExecutionStatus(h), "queued"))
}

// heldStillPendingErr is the exit-3 outcome of a followed held call that has
// not finished when the timeout lapses.
func heldStillPendingErr(ref *heldRef, h *control.HeldExecutionDetailResponse, waited time.Duration, approved bool) *ux.CodedError {
	status := heldExecutionStatus(h)
	details := ref.details()
	details["state"] = string(h.State)
	details["execution_status"] = status
	details["next_command"] = ref.pollCommand()
	actionable := fmt.Sprintf("The call is still waiting for a human decision. Show the user the review_url (%s) "+
		"again and end your turn. When they reply, run `%s` once. Do not re-send the call.",
		valueOr(ref.ReviewURL, "printed by the held execute"), ref.pollCommand())
	if approved {
		actionable = fmt.Sprintf("The call was approved and has not finished. Run `%s` again shortly. "+
			"Do not re-send the call.", ref.pollCommand())
	}
	return &ux.CodedError{
		Code: ux.CodeTimeoutPending,
		Msg: fmt.Sprintf("held execution %s is still %s after waiting %s; it has not finished",
			ref.ID, valueOr(status, string(h.State)), waited),
		Actionable: actionable,
		Details:    details,
	}
}

// heldPollFailedErr is a failure reading a followed held call (transport,
// timeout, auth): the call itself may still be held, so the envelope keeps
// the held execution id, review link and poll command.
func heldPollFailedErr(ref *heldRef, err error) *ux.CodedError {
	coded := asCoded(classifyTransportErr(err))
	details := ref.details()
	for k, v := range coded.Details {
		if _, ok := details[k]; !ok {
			details[k] = v
		}
	}
	details["next_command"] = ref.pollCommand()
	actionable := fmt.Sprintf("The held call is unaffected: it may still be waiting for a human decision. "+
		"Show the user the review_url (%s) if they have not approved it yet, then run `%s` to retry. "+
		"Do not re-send the call.", valueOr(ref.ReviewURL, "printed by the held execute"), ref.pollCommand())
	if coded.Actionable != "" && coded.Code != ux.CodeTransportError {
		actionable = coded.Actionable + " Then run `" + ref.pollCommand() + "`; do not re-send the call."
	}
	return &ux.CodedError{
		Code:       coded.Code,
		Msg:        fmt.Sprintf("failed to read held execution %s: %s", ref.ID, coded.Msg),
		Actionable: actionable,
		Details:    details,
		Cause:      err,
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

// heldPollError is a failure to read a followed held call: the call's own
// state is unknown, unlike an outcome the control plane reported.
type heldPollError struct {
	err error
}

func (e *heldPollError) Error() string { return e.err.Error() }

func (e *heldPollError) Unwrap() error { return e.err }

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
		return h.State != control.HeldExecutionStatePending
	}
	return agentops.IsFinishedHeldStatus(status)
}

// waitForHeldExecution polls a held execution until it finishes or timeout
// lapses (a *heldWaitTimeoutError), then returns its outcome as the
// ExecuteResult the call would have produced synchronously. A failed read is
// a *heldPollError. Each read updates ref with the review link and expiry.
// Heartbeats ride stderr so they never corrupt the JSON stdout an agent
// parses.
func (a *app) waitForHeldExecution(ctx context.Context, ref *heldRef, timeout time.Duration) (*agentops.ExecuteResult, error) {
	client, err := a.controlClient(ctx)
	if err != nil {
		return nil, &heldPollError{err: err}
	}
	held, err := a.pollHeldExecution(ctx, client, ref, timeout)
	if err != nil {
		return nil, err
	}
	return heldExecuteResult(ctx, client, held)
}

// pollHeldExecution polls GET /executions/holds/{id} on the App's shared
// approval cadence until the held call finishes or timeout lapses; a zero
// timeout reads it once.
func (a *app) pollHeldExecution(
	ctx context.Context, client *control.ClientWithResponses, ref *heldRef, timeout time.Duration,
) (*control.HeldExecutionDetailResponse, error) {
	start := time.Now()
	deadline := start.Add(timeout)
	delay, maxDelay, step := a.PollCadence()
	const heartbeatAfter = 2 * time.Second
	nextHeartbeat := start.Add(heartbeatAfter)
	for {
		held, err := getHeldExecution(ctx, client, ref.ID)
		if err != nil {
			return nil, err
		}
		ref.learn(held)
		if heldExecutionFinished(held) {
			return held, nil
		}
		if !time.Now().Before(deadline) {
			return nil, &heldWaitTimeoutError{held: held}
		}
		if now := time.Now(); now.After(nextHeartbeat) {
			fmt.Fprintln(a.Err, theme.StylesFromContext(ctx).Dimf(
				"  waiting for held execution %s (%s, %ds elapsed) …",
				ref.ID, heldExecutionStatus(held), int(now.Sub(start).Seconds())))
			nextHeartbeat = now.Add(5 * time.Second)
		}
		select {
		case <-ctx.Done():
			return nil, &heldPollError{err: fmt.Errorf("wait for held execution %s: %w", ref.ID, ctx.Err())}
		case <-time.After(min(delay, max(time.Until(deadline), 0))):
		}
		if delay < maxDelay {
			delay += step
		}
	}
}

// getHeldExecution reads one held execution; an unknown id (or one outside
// the caller's visibility) is RESOLVE_FAILED, any other failure a
// *heldPollError.
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
					"`jentic executions poll <held_execution_id>` with the exact value.",
				Details: map[string]any{"held_execution_id": heldExecutionID},
			}
		}
		return nil, &heldPollError{err: fmt.Errorf("failed to read held execution %s: %w", heldExecutionID, classifyTransportErr(err))}
	}
	if resp.JSON200 == nil {
		return nil, &heldPollError{err: &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  fmt.Sprintf("unexpected backend response reading held execution %s (status %d)", heldExecutionID, resp.StatusCode()),
		}}
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
	if status == agentops.HeldStatusCancelled || held.State == control.HeldExecutionStateWithdrawn {
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
			return nil, &heldPollError{err: fmt.Errorf("failed to read the result of held execution %s: %w", held.Id, classifyTransportErr(err))}
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
			return nil, &heldPollError{err: fmt.Errorf("failed to read the result of held execution %s: %w", held.Id, classifyTransportErr(err))}
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
