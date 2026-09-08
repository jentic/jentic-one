package api

import (
	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// planFlags registers --dry-run/--export-plan on a mutating API-calling command
// (impl/5.0 §5). They are LOCAL, not persistent-root flags: the shipped V1 tree
// already has local --dry-run flags with different (file-preview) semantics on
// setup/skill, so a persistent root flag would shadow them.
func planFlags(cmd *cobra.Command) {
	cmd.Flags().Bool("dry-run", false, "Validate and print the request that WOULD be sent, without sending it")
	cmd.Flags().Bool("export-plan", false, "Emit a machine-readable execution plan (implies --dry-run)")
}

// maybeEmitPlan renders the resolved request as a plan through the Audience and
// returns true when execution should STOP (i.e. --dry-run or --export-plan was
// set). Mutating commands call it immediately before the client call:
//
//	if maybeEmitPlan(cmd, "importApis", body) { return nil }
//	// ... fire the client
//
// operation should mirror the generated client method the command invokes; a
// table-driven test keeps the literal honest (impl/5.0 §5).
func maybeEmitPlan(cmd *cobra.Command, operation string, payload any) bool {
	dryRun, _ := cmd.Flags().GetBool("dry-run")
	exportPlan, _ := cmd.Flags().GetBool("export-plan")
	if !dryRun && !exportPlan {
		return false
	}
	ux.FromContext(cmd.Context()).Render(ux.Plan{Operation: operation, DryRun: true, Payload: payload})
	return true
}
