// Package api — V1 → V2 deprecation alias layer (BC-2/BC-3, impl/1.3 §7).
//
// This file is the SINGLE removable block that translates the deprecated V1
// selection surface onto the V2 context model:
//
//   - `jentic profile <verb>`  → `jentic context <verb>` (delegated, not re-exec)
//   - `profile add-key`        → `jentic identity add --api-key` (guidance only)
//   - `--profile <p>` / `$JENTIC_PROFILE` → `--context <p>` / `$JENTIC_CONTEXT`
//
// ACTIVE since the activation release: the V1 `profile` command was removed and
// registerProfileAliasShims is registered on the tree in its place, so V1
// muscle memory lands on the successor instead of an "unknown command" error.
// Keeping the whole layer in one file means the post-window removal (14 BC-1
// EOL) is a single file deletion, verified by the clidocs drift check.
package api

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// profileVerbToContext maps a V1 `profile` subcommand verb onto its V2 `context`
// successor argv (BC-3 table). It returns the context argv and true for a mapped
// verb, or nil/false for an unmapped one (the caller then errors with guidance).
//
//	profile list        -> context list
//	profile use <p>     -> context use <p>
//	profile view [p]    -> context view           (active only; V1 [p] arg dropped)
//	profile delete <p>  -> context delete <p>
//	profile add-key <p> -> identity add <p> --api-key ...   (handled separately —
//	                       it targets identity, not context; see the shim RunE)
func profileVerbToContext(verb string, args []string) ([]string, bool) {
	switch verb {
	case "list", "ls":
		return []string{"context", "list"}, true
	case "use":
		return append([]string{"context", "use"}, args...), true
	case "view":
		// V1 `profile view [name]` showed an arbitrary profile; the V2 carve-out
		// `context view` is active-only by design (impl/1.3 §3). The name arg is
		// intentionally dropped — an agent introspects only itself.
		return []string{"context", "view"}, true
	case "delete":
		return append([]string{"context", "delete"}, args...), true
	default:
		return nil, false
	}
}

// remapProfileSelection implements the `--profile`/`$JENTIC_PROFILE` → context
// resolution (BC-3): migrated context names equal profile names (the BC-1 naming
// rule), so the mapping is identity. It returns the context override to use,
// preferring an explicit --profile value over $JENTIC_PROFILE, or "" when neither
// is set (the normal --context/$JENTIC_CONTEXT ladder then applies).
func remapProfileSelection(profileFlag, profileEnv string) string {
	if profileFlag != "" {
		return profileFlag
	}
	return profileEnv
}

// registerProfileAliasShims attaches the hidden `profile` shim command,
// delegating each verb to its V2 successor (never re-exec). It replaced the V1
// profile command at activation and removes as one unit at the BC-1 EOL.
func registerProfileAliasShims(root *cobra.Command, _ *app) {
	shim := &cobra.Command{
		Use:    "profile",
		Short:  "[deprecated] alias for `jentic context` (use context/env/identity)",
		Hidden: true,
		Args:   cobra.ArbitraryArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			verb := ""
			rest := args
			if len(args) > 0 {
				verb, rest = args[0], args[1:]
			}
			// add-key retargets IDENTITY, not context, and needs flags this
			// shim doesn't parse — point at the successor instead of guessing.
			if verb == "add-key" {
				name := ""
				if len(rest) > 0 {
					name = " " + rest[0]
				}
				return &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        "`jentic profile add-key` was removed in the V2 CLI: API-key credentials are stored on an identity, scoped to an environment",
					Actionable: fmt.Sprintf("jentic identity add%s --env <env> --api-key <jak_...>", name),
				}
			}
			ctxArgs, ok := profileVerbToContext(verb, rest)
			if !ok {
				return cmd.Help()
			}
			// Delegate by re-dispatching through the root so the successor's full
			// PersistentPreRunE (audience/fencing/gate) runs exactly as if invoked
			// directly.
			root.SetArgs(ctxArgs)
			return root.Execute()
		},
	}
	root.AddCommand(shim)
}
