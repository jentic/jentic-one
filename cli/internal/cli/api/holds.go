package api

import (
	"errors"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newHoldsCmd is the `jentic holds` group: the agent's own held executions —
// calls a permission rule held for a human decision (the control plane's
// /executions/holds).
func newHoldsCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "holds",
		Short: "Wait on your held executions (calls awaiting human approval)",
		Long: "holds works with your held executions: calls a permission rule held for\n" +
			"a human to approve or deny. `jentic execute` prints a held call's\n" +
			"held_execution_id; `jentic holds wait <held_execution_id>` waits for it.",
	}
	cmd.AddCommand(newHoldsWaitCmd(app))
	return cmd
}

// newHoldsWaitCmd is `jentic holds wait <held_execution_id>`: poll a held
// execution until it finishes and print its outcome exactly like
// `jentic execute`.
func newHoldsWaitCmd(app *app) *cobra.Command {
	opts := &executeOptions{}
	cmd := &cobra.Command{
		Use:   "wait <held_execution_id>",
		Short: "Wait for a held execution's decision and print its result",
		Long: "wait polls a held execution — a call the broker held for human\n" +
			"approval — until it finishes, then prints the outcome exactly as\n" +
			"`jentic execute` prints a call that ran at once: the upstream response\n" +
			"(exit 0, even an upstream 4xx/5xx), or the denial when a reviewer denied\n" +
			"the call or it expired undecided (exit 2). It never re-sends the call.\n\n" +
			"Exit codes:\n" +
			"  0 — the call ran; the upstream response is the data\n" +
			"  1 — local/transport failure, or the run got no upstream answer\n" +
			"  2 — the call never ran (denied, expired, withdrawn), an unknown held\n" +
			"      execution id, or a result past retention\n" +
			"  3 — still held (or queued) when --timeout lapsed (TIMEOUT_PENDING)",
		Example: "  jentic holds wait exap_2mXk... --json\n" +
			"  jentic holds wait exap_2mXk... --timeout 30m",
		Args: exactNamedArgs("<held_execution_id>", "held_execution_id"),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.holdsWaitE(cmd, opts, args[0])
		},
	}
	cmd.Flags().DurationVar(&opts.timeout, "timeout", heldWaitDefault, "how long to wait before exiting 3 (TIMEOUT_PENDING)")
	cmd.Flags().BoolVar(&opts.raw, "raw", false, "stream the response body directly to stdout")
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON envelope output")
	return cmd
}

func (a *app) holdsWaitE(cmd *cobra.Command, opts *executeOptions, heldExecutionID string) error {
	if opts.timeout <= 0 {
		return &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        fmt.Sprintf("--timeout must be positive, got %s", opts.timeout),
			Actionable: "Pass a positive --timeout (e.g. --timeout 10m).",
		}
	}
	res, err := a.waitForHeldExecution(cmd.Context(), heldExecutionID, opts.timeout)
	if err != nil {
		var timedOut *heldWaitTimeoutError
		if errors.As(err, &timedOut) {
			return heldStillPendingErr(timedOut, opts)
		}
		return err
	}
	return a.executeOutput(cmd, opts, res)
}

// heldStillPendingErr is the exit-3 outcome of `holds wait` on a held call
// that has not finished.
func heldStillPendingErr(t *heldWaitTimeoutError, opts *executeOptions) *ux.CodedError {
	status := heldExecutionStatus(t.held)
	details := map[string]any{
		"held_execution_id": t.held.Id,
		"state":             string(t.held.State),
		"execution_status":  status,
	}
	again := fmt.Sprintf("run `jentic holds wait %s` again later; do not re-send the call.", t.held.Id)
	actionable := "The call was approved and has not finished: " + again
	if t.held.State == control.ExecutionApprovalStatePending {
		actionable = "The call is still waiting for a human decision. Remind the user to open the review_url " +
			"the held execute printed, then " + again
	}
	return &ux.CodedError{
		Code:       ux.CodeTimeoutPending,
		Msg:        fmt.Sprintf("held execution %s is still %s after waiting %s", t.held.Id, valueOr(status, string(t.held.State)), opts.timeout),
		Actionable: actionable,
		Details:    details,
	}
}
