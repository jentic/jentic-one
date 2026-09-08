package api

import (
	"github.com/spf13/cobra"
)

// fenced marks a command as host-mutating management surface that must NOT run in
// agent/service-account mode (impl/3.2 §2a). The root interceptor reads this
// annotation off the RESOLVED leaf command and blocks the command with a
// FENCED_COMMAND error when the audience IsFenced(). A command that forgets this
// annotation is silently NOT fenced — the arch guard Test1C asserts the canonical
// set (clitree.MustBeFenced) is annotated so a new admin command can't slip through.
func fenced(cmd *cobra.Command) *cobra.Command {
	if cmd.Annotations == nil {
		cmd.Annotations = map[string]string{}
	}
	cmd.Annotations["fenced"] = "true"
	return cmd
}

// bootstrapSafe marks a command that must run even when no configuration exists
// yet (the config-creating commands and the always-available help/version). For
// these, a state-resolution failure in the interceptor degrades to a default
// human/no-color state instead of aborting — otherwise the command that creates
// the config could never run (impl/3.2 §2 bootstrap exemption).
func bootstrapSafe(cmd *cobra.Command) *cobra.Command {
	if cmd.Annotations == nil {
		cmd.Annotations = map[string]string{}
	}
	cmd.Annotations["bootstrap-safe"] = "true"
	return cmd
}
