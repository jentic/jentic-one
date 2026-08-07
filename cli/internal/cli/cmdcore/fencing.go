package cmdcore

import (
	"os"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// installInterceptor wires the audience-aware root PersistentPreRunE (impl/3.2 §2)
// onto a root command: resolve state -> resolve theme (Stage-0 mode gate) ->
// construct the Audience -> ENFORCE FENCING -> inject Audience + ActiveState into
// the context. It preserves the existing banner/nudge side effects.
//
// SCOPE (Phase 2): this enforces fencing and makes the Audience/ActiveState
// available in the context; the shipped commands still render through the legacy
// output path (the strangler-fig cutover to aud.Render is Phase 3). Resolution
// failures are non-fatal for everything except an explicit fenced-in-agent-mode
// block, so V1 behavior is preserved on un-migrated machines.
func installInterceptor(app *App, root *cobra.Command) {
	root.PersistentPreRunE = func(cmd *cobra.Command, _ []string) error {
		// Preserve the shipped banner + update nudge (previously PersistentPreRun).
		app.banner(cmd)
		app.maybeNudgeUpdate(cmd)

		// 1. Resolve active state (SDK + legacy adapter). On failure, degrade to a
		// default state rather than aborting — Phase 2 must not regress V1 for users
		// with no XDG config, and config-creating commands are bootstrap-safe. The
		// --context/--mode/--theme root flags land in Phase 3; the env fallbacks
		// ($JENTIC_CONTEXT here, $JENTIC_MODE inside resolveMode) keep working when
		// the flags are unset.
		contextOverride := flagValue(cmd, "context")
		if contextOverride == "" {
			contextOverride = os.Getenv("JENTIC_CONTEXT") // reserved: 14 BC-9
		}
		state, err := clictx.ResolveActiveState(contextOverride, flagValue(cmd, "mode"))
		if err != nil {
			// Degrade to a default state, but RE-APPLY THE MODE LADDER on the way
			// down: --mode/$JENTIC_MODE must still take effect with no config, so an
			// agent on an un-provisioned machine is still fenced. Hardcoding human
			// here would silently disable fencing (fail-OPEN) — the opposite of what
			// a safety guard must do. Theme degrades to no-color regardless.
			state = &clictx.ActiveState{
				Mode:      clictx.ResolveMode(flagValue(cmd, "mode"), ""),
				ThemeName: "no-color",
			}
		}

		// 2. Resolve theme. STAGE 0 — mode gate: agent/service-account force
		// no-color, beating --theme/JENTIC_THEME/NO_COLOR/config so machine output is
		// never corrupted by ANSI. Human falls through to the normal ladder.
		var palette ux.Palette
		if state.Mode == clictx.ModeAgent || state.Mode == clictx.ModeServiceAccount {
			palette = theme.Themes["no-color"]
		} else {
			palette = theme.ResolveTheme(flagValue(cmd, "theme"), state.ThemeName)
		}

		// 3. Construct the Audience. FAIL CLOSED on an unknown mode (typo'd
		// JENTIC_MODE / stale config): the most restrictive AgentUX, never the
		// unfenced HumanUX — otherwise JENTIC_MODE=agnet would bypass fencing.
		assumeYes := boolFlag(cmd, "yes")
		var audience ux.Audience
		switch state.Mode {
		case clictx.ModeHuman:
			audience = ux.NewHumanUX(palette, assumeYes)
		case clictx.ModeAgent, clictx.ModeServiceAccount:
			audience = ux.NewAgentUX(assumeYes)
		default:
			audience = ux.NewAgentUX(assumeYes)
		}

		// 4. FENCING (guardrail; the enforced boundary is server-side scope + OS
		// isolation). Block a fenced management command in a fenced mode.
		if audience.IsFenced() && cmd.Annotations["fenced"] == "true" {
			ferr := &ux.CodedError{
				Code: ux.CodeFenced,
				Msg:  "management commands are disabled in agent mode",
			}
			audience.ReportError(ferr, "Agents must operate within their provisioned environment. Do not attempt to switch contexts.")
			return ferr
		}

		// 5. Inject Audience + ActiveState (and mirror the palette for theme.FromContext).
		ctx := ux.WithAudience(cmd.Context(), audience)
		ctx = clictx.WithActiveState(ctx, state)
		ctx = theme.WithContext(ctx, palette)
		cmd.SetContext(ctx)
		return nil
	}
}

// flagValue returns the string value of a persistent flag if present and set,
// else "". Safe to call for flags a given root may not define.
func flagValue(cmd *cobra.Command, name string) string {
	if f := cmd.Flags().Lookup(name); f != nil {
		return f.Value.String()
	}
	return ""
}

// boolFlag returns the bool value of a flag if present, else false.
func boolFlag(cmd *cobra.Command, name string) bool {
	if f := cmd.Flags().Lookup(name); f != nil {
		if b, err := cmd.Flags().GetBool(name); err == nil {
			return b
		}
	}
	return false
}
