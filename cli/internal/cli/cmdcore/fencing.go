package cmdcore

import (
	"context"
	"os"
	"time"

	"github.com/spf13/cobra"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// LongRunningAnnotation marks a command whose lifetime is owned by its caller
// (e.g. `jentic mcp`, a stdio server the MCP client spawns and keeps for the
// whole session). The interceptor's non-interactive wall-clock deadline (see
// installInterceptor) is skipped for these commands: it exists to bound a
// SINGLE control-plane call, and applying it to a server process would kill
// the session after 60s. Long-running commands own their per-call deadlines.
const LongRunningAnnotation = "long-running"

// installInterceptor wires the audience-aware root PersistentPreRunE (impl/3.2 §2)
// onto a root command: resolve state -> resolve theme (Stage-0 mode gate) ->
// construct the Audience -> ENFORCE FENCING -> inject Audience + ActiveState into
// the context. It preserves the existing banner/nudge side effects.
//
// SCOPE: this enforces fencing and makes the Audience/ActiveState available in
// the context. Resolution failures are non-fatal for everything except an
// explicit fenced-in-agent-mode block, so machines with no XDG config still
// work and config-creating commands stay bootstrap-safe.
func installInterceptor(app *App, root *cobra.Command) {
	// agentTimeout bounds a single control-plane call in non-interactive mode so a
	// wedged server can't hang an agent forever (F3, review round-3 #7 /
	// cli-conventions §"Context timeouts"). Human mode is left undeadlined so
	// interactive prompts and paginators aren't aborted mid-flow.
	const agentTimeout = 60 * time.Second
	// cancelTimeout holds the current invocation's timeout cancel (if any) so the
	// PersistentPostRunE below can release it — avoids a leaked context timer
	// without threading the cancel through the whole command tree. Safe as a
	// captured single value: one cobra invocation runs one command to completion.
	var cancelTimeout context.CancelFunc

	root.PersistentPostRunE = func(_ *cobra.Command, _ []string) error {
		if cancelTimeout != nil {
			cancelTimeout()
			cancelTimeout = nil
		}
		return nil
	}

	root.PersistentPreRunE = func(cmd *cobra.Command, _ []string) error {
		// Preserve the shipped banner + update nudge.
		app.banner(cmd)
		app.maybeNudgeUpdate(cmd)

		// 1. Resolve active state (SDK + legacy adapter). On failure, degrade to a
		// default state rather than aborting — users with no XDG config must not be
		// blocked, and config-creating commands are bootstrap-safe. The
		// --context/--mode/--theme root flags take precedence; the env fallbacks
		// ($JENTIC_CONTEXT here, $JENTIC_MODE inside resolveMode) apply when
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
			//
			// The embedded ResolvedState must be non-nil: commands that reach
			// clictx.GetControlClient on this degraded state would otherwise
			// nil-deref (panic, runtime exit 2 — colliding with ExitDenied).
			fallbackMode, fallbackExplicit := clictx.ResolveModeExplicit(flagValue(cmd, "mode"), "")
			state = &clictx.ActiveState{
				ResolvedState: &sdkconfig.ResolvedState{},
				Mode:          fallbackMode,
				ModeExplicit:  fallbackExplicit,
				ThemeName:     "no-color",
			}
		}

		// 2. Resolve theme. STAGE 0 — mode gate: agent/service-account force
		// no-color, beating --theme/JENTIC_THEME/NO_COLOR/config so machine output is
		// never corrupted by ANSI. Human falls through to the normal ladder.
		var palette ux.Palette
		var themeName string
		if state.Mode == clictx.ModeAgent || state.Mode == clictx.ModeServiceAccount {
			palette, themeName = theme.Themes["no-color"], "no-color"
		} else {
			palette, themeName = theme.ResolveThemeWithName(flagValue(cmd, "theme"), state.ThemeName)
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

		// 3.5. Diagnostics bootstrap (impl/3.2 §2d): install the mode-dependent
		// slog default (text for human, JSON for agent/service-account), always to
		// stderr and redacted. This is the ONLY slog.SetDefault call in the process,
		// so every later log line — including the SDK's via the default logger —
		// carries the mode-appropriate, secret-scrubbed handler.
		setupSlog(app, state.Mode, boolFlag(cmd, "verbose"))

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

		// 4.5. MIGRATE GATE (activation): on a machine that still carries an
		// unmigrated V1 profile store, stop every gated command and demand
		// `jentic migrate`. Reported through the audience so agents get the
		// machine envelope with actionable_step and humans a styled line.
		if gerr := migrateGateError(app, cmd); gerr != nil {
			audience.ReportError(gerr, gerr.Actionable)
			return gerr
		}

		// 5. Inject Audience + ActiveState (and mirror the palette + resolved theme
		// name for theme.FromContext / logo-gradient lookup).
		ctx := ux.WithAudience(cmd.Context(), audience)
		ctx = clictx.WithActiveState(ctx, state)
		ctx = theme.WithContext(ctx, palette)
		ctx = theme.WithThemeName(ctx, themeName)

		// Non-interactive modes get a wall-clock deadline (F3, review round-3 #7):
		// an agent/service-account orchestrating jentic against an unresponsive
		// Control Plane would otherwise hang forever (the shared control client
		// leaves http.Client.Timeout zero, deferring to per-call contexts that
		// today carry no deadline). Human mode stays undeadlined so interactive
		// prompts/paginators aren't cut off. Long-running commands (annotation
		// above) are exempt: their lifetime is caller-owned and they bound their
		// own per-call contexts. The cancel is released in PersistentPostRunE
		// above.
		if (state.Mode == clictx.ModeAgent || state.Mode == clictx.ModeServiceAccount) &&
			cmd.Annotations[LongRunningAnnotation] != "true" {
			//nolint:gosec // G118: the cancel is stored in cancelTimeout and invoked in the root PersistentPostRunE above (one invocation runs one command to completion); a leaked timer would in any case be reclaimed at process exit.
			ctx, cancelTimeout = context.WithTimeout(ctx, agentTimeout)
		}

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
