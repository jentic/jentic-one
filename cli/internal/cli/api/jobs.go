package api

import (
	"errors"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newJobsCmd is the `jentic jobs` group: the agent's own background jobs.
func newJobsCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "jobs",
		Short: "Wait on your background jobs (held executions)",
		Long: "jobs works with the background jobs your calls create — above all an\n" +
			"execution a permission rule held for human approval. `jentic execute`\n" +
			"prints a held call's job_id; `jentic jobs wait <job_id>` waits for it.",
	}
	cmd.AddCommand(newJobsWaitCmd(app))
	return cmd
}

// newJobsWaitCmd is `jentic jobs wait <job_id>`: poll an execution job until
// it settles and print its outcome exactly like `jentic execute`.
func newJobsWaitCmd(app *app) *cobra.Command {
	opts := &executeOptions{}
	cmd := &cobra.Command{
		Use:   "wait <job_id>",
		Short: "Wait for an execution job (e.g. a held call) and print its result",
		Long: "wait polls an execution job — typically a call the broker held for\n" +
			"human approval — until it settles, then prints the outcome exactly as\n" +
			"`jentic execute` prints a call that ran at once: the upstream response\n" +
			"(exit 0, even an upstream 4xx/5xx), or the denial when a reviewer denied\n" +
			"the call or its approval expired (exit 2). It never re-sends the call.\n\n" +
			"Exit codes:\n" +
			"  0 — the call ran; the upstream response is the data\n" +
			"  1 — local/transport failure, or the run got no upstream answer\n" +
			"  2 — the call never ran: denied, expired, withdrawn, or an unknown job id\n" +
			"  3 — still held (or queued) when --timeout lapsed (TIMEOUT_PENDING)",
		Example: "  jentic jobs wait job_2mXk... --json\n" +
			"  jentic jobs wait job_2mXk... --timeout 30m",
		Args: exactNamedArgs("<job_id>", "job_id"),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.jobsWaitE(cmd, opts, args[0])
		},
	}
	cmd.Flags().DurationVar(&opts.timeout, "timeout", heldWaitDefault, "how long to wait before exiting 3 (TIMEOUT_PENDING)")
	cmd.Flags().BoolVar(&opts.raw, "raw", false, "stream the response body directly to stdout")
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON envelope output")
	return cmd
}

func (a *app) jobsWaitE(cmd *cobra.Command, opts *executeOptions, jobID string) error {
	if opts.timeout <= 0 {
		return &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        fmt.Sprintf("--timeout must be positive, got %s", opts.timeout),
			Actionable: "Pass a positive --timeout (e.g. --timeout 10m).",
		}
	}
	res, err := a.waitForExecutionJob(cmd.Context(), jobID, opts.timeout)
	if err != nil {
		var timedOut *jobWaitTimeoutError
		if errors.As(err, &timedOut) {
			return jobStillPendingErr(timedOut, opts)
		}
		return err
	}
	return a.executeOutput(cmd, opts, res)
}

// jobStillPendingErr is the exit-3 outcome of `jobs wait` on a job that has
// not settled.
func jobStillPendingErr(t *jobWaitTimeoutError, opts *executeOptions) *ux.CodedError {
	details := map[string]any{"job_id": t.job.JobId, "job_status": t.job.Status}
	again := fmt.Sprintf("run `jentic jobs wait %s` again later; do not re-send the call.", t.job.JobId)
	actionable := "The job has not settled: " + again
	if agentops.IsHeldJobStatus(t.job.Status) {
		if t.job.ApprovalId != nil {
			details["approval_id"] = *t.job.ApprovalId
		}
		actionable = "The call is still waiting for a human decision. Remind the user to open the review_url " +
			"the held execute printed, then " + again
	}
	return &ux.CodedError{
		Code:       ux.CodeTimeoutPending,
		Msg:        fmt.Sprintf("job %s is still %s after waiting %s", t.job.JobId, t.job.Status, opts.timeout),
		Actionable: actionable,
		Details:    details,
	}
}
