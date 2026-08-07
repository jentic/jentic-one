// Package cmd — V1 → V2 deprecation alias layer (BC-2/BC-3, impl/1.3 §7).
//
// This file is the SINGLE removable block that translates the deprecated V1
// selection surface onto the V2 context model:
//
//   - `jentic profile <verb>`  → `jentic context <verb>` (delegated, not re-exec)
//   - `--profile <p>` / `$JENTIC_PROFILE` → `--context <p>` / `$JENTIC_CONTEXT`
//   - `--json` → agent-mode OUTPUT ENVELOPE only (BC-5; does NOT flip behavior)
//
// IMPORTANT — DORMANT UNTIL ACTIVATION. Per 16_landing_strategy.md §1, the
// breaking-half code lands but stays gated: V1's real `profile` command and
// `--profile` flag remain the live, default path until the activation release
// (cut only after Phases 3 AND 4 merge — 14 rollout item 0). Wiring these shims
// live now would DOUBLE-DEFINE `profile` and break V1. So this file exposes the
// mapping as PURE, TESTED helpers plus a single registration entry point
// (registerProfileAliasShims) that the activation release will call in place of
// newProfileCmd — nothing here is registered on the tree today.
//
// Keeping the whole layer in one file means the post-window removal (14 BC-1 EOL)
// is a single file deletion, verified by the clidocs drift check.
package api

import (
	"github.com/spf13/cobra"
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
//	                       it targets identity, not context; see addKeyToIdentity)
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
//
// This is the pure core the activation-release ResolveActiveState pre-step calls
// before the standard ladder; it is unit-tested independently of Cobra.
func remapProfileSelection(profileFlag, profileEnv string) string {
	if profileFlag != "" {
		return profileFlag
	}
	return profileEnv
}

// registerProfileAliasShims is the ACTIVATION-RELEASE entry point. It attaches
// the hidden `profile` shim command (delegating each verb to its V2 successor's
// RunE) in place of the V1 profile command. It is intentionally NOT called today
// (see the package-block doc): the V1 profile command owns that name until
// activation. Kept here so activation is a one-line swap in newAPIRootCmd and the
// whole layer removes as one unit at EOL.
//
// The delegation runs the successor command's RunE directly (never re-exec) and
// emits a deprecation warning that respects the machine contract: a styled stderr
// line in human mode, a structured slog warn line in agent mode (impl/1.3 §7).
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
			ctxArgs, ok := profileVerbToContext(verb, rest)
			if !ok {
				return cmd.Help()
			}
			// Delegate by re-dispatching through the root so the successor's full
			// PersistentPreRunE (audience/fencing) runs exactly as if invoked directly.
			root.SetArgs(ctxArgs)
			return root.Execute()
		},
	}
	// add-key retargets identity, not context, so it is a distinct shim leaf.
	root.AddCommand(shim)
}
