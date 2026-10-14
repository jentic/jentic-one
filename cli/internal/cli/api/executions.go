package api

import (
	"github.com/spf13/cobra"
)

// newExecutionsCmd is the `jentic executions` group: following the agent's
// own executions — calls a permission rule held for a human decision (the
// control plane's /executions/holds).
func newExecutionsCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "executions",
		Short: "Follow your held executions (calls awaiting human approval)",
		Long: "executions follows your held executions: calls a permission rule held for\n" +
			"a human to approve or deny. `jentic execute` prints a held call's\n" +
			"held_execution_id; `jentic executions poll <held_execution_id>` collects\n" +
			"its outcome.",
	}
	cmd.AddCommand(newExecutionsPollCmd(app))
	return cmd
}

// newExecutionsPollCmd is `jentic executions poll <held_execution_id>`: follow
// a held execution up to --timeout and print its outcome exactly like
// `jentic execute`.
func newExecutionsPollCmd(app *app) *cobra.Command {
	opts := &executeOptions{}
	cmd := &cobra.Command{
		Use:   "poll <held_execution_id>",
		Short: "Collect a held execution's outcome, waiting up to --timeout",
		Long: "poll reads a held execution — a call the broker held for human\n" +
			"approval — and, while it has not finished, re-reads it for up to\n" +
			"--timeout (default 30s, max 5m; 0 checks once). A finished call prints\n" +
			"exactly as `jentic execute` prints a call that ran at once: the upstream\n" +
			"response (exit 0, even an upstream 4xx/5xx), or the denial when a\n" +
			"reviewer denied the call or it expired undecided (exit 2). A call still\n" +
			"held prints its held_execution_id, review_url and this command again\n" +
			"(exit 3): show the user the link, end your turn, and poll once more when\n" +
			"they reply. It never re-sends the call.\n\n" +
			"--timeout takes a duration (30s, 1m) or a number of seconds (30).\n\n" +
			"Exit codes:\n" +
			"  0 — the call ran; the upstream response is the data\n" +
			"  1 — local/transport failure, or the run got no upstream answer\n" +
			"  2 — the call never ran (denied, expired, withdrawn, refused at run\n" +
			"      time), an unknown held execution id, or a result past retention\n" +
			"  3 — still held, queued or running when --timeout lapsed (TIMEOUT_PENDING)",
		Example: "  jentic executions poll held_2mXk... --json\n" +
			"  jentic executions poll held_2mXk... --timeout 2m\n" +
			"  jentic executions poll held_2mXk... --timeout 0",
		Args: exactNamedArgs("<held_execution_id>", "held_execution_id"),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.followHeld(cmd, opts, &heldRef{ID: args[0]}, nil)
		},
	}
	addHeldTimeoutFlag(cmd, &opts.timeout, "how long to follow the held call before exiting 3 (TIMEOUT_PENDING): a duration (30s, 1m) or seconds (30); max 5m, 0 checks once")
	cmd.Flags().BoolVar(&opts.raw, "raw", false, "stream the response body directly to stdout")
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON envelope output")
	return cmd
}
