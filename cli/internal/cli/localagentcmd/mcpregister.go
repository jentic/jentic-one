package localagentcmd

// mcpregister.go writes the `jentic mcp` server entry into each detected
// runtime's native MCP config (local-MCP item 2-E3), shared by `jentic setup`
// and `jentic skill init`. Entries always carry the ABSOLUTE stable binary
// path (GUI spawns see a minimal PATH, master §3.7.3) and always PIN
// `--context <name>` (master §3.10): a bare `jentic mcp` follows the
// operator's *active* context, so a later `jentic context use` would silently
// re-point the runtime at a different agent/instance. After each entry is
// written the control plane is told (best-effort — a failed report never
// blocks setup) so the config-written → first-session → first-execute
// adoption funnel has its first leg.

import (
	"context"
	"fmt"
	"os/exec"
	"runtime"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/mcpcfg"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// mcpRuntimesFor maps the selected skill operators onto the MCP runtimes to
// register, filtered by presence: "claude" fans out to Claude Code (needs the
// claude CLI) and Claude Desktop (needs the app's config dir) — they keep
// separate MCP configs; hermes/generic have no MCP config to write.
func mcpRuntimesFor(targets []skillTarget, env mcpcfg.Env) []mcpcfg.Runtime {
	var out []mcpcfg.Runtime
	add := func(r mcpcfg.Runtime) {
		if mcpcfg.Detect(r, env) {
			out = append(out, r)
		}
	}
	for _, t := range targets {
		switch t.adapter.Operator() {
		case skillgen.OpCursor:
			add(mcpcfg.RuntimeCursor)
		case skillgen.OpClaude:
			add(mcpcfg.RuntimeClaudeCode)
			add(mcpcfg.RuntimeClaudeDesktop)
		case skillgen.OpCodex:
			add(mcpcfg.RuntimeCodex)
		}
	}
	return out
}

// mcpEnv builds the mcpcfg probe environment from the (test-injectable)
// skillgen detection env plus a real PATH resolver.
func (a *Cmd) mcpEnv() (mcpcfg.Env, error) {
	env, err := a.detectEnv()
	if err != nil {
		return mcpcfg.Env{}, err
	}
	return mcpcfg.Env{
		Home: env.Home,
		GOOS: runtime.GOOS,
		Stat: env.Stat,
		LookPath: func(name string) (string, error) {
			if env.Lookup != nil && !env.Lookup(name) {
				return "", exec.ErrNotFound
			}
			return exec.LookPath(name)
		},
	}, nil
}

// activeContextName resolves the name of the operator's active context from
// config.yaml — the name the written entries pin with --context. Empty when
// no on-disk context is active (file-less sessions have nothing to pin).
func activeContextName() string {
	cfg, err := sdkconfig.Load()
	if err != nil || cfg == nil {
		return ""
	}
	if _, ok := cfg.Contexts[cfg.ActiveContext]; !ok {
		return ""
	}
	return cfg.ActiveContext
}

// registerMCPEntries writes the MCP entry for every detected runtime among
// the selected targets and reports each write to the control plane
// (best-effort). It never returns an error to the caller's main flow — every
// failure is reported per-runtime and the rest continue; MCP registration
// must not block the identity/skill provisioning the operator came for.
// Returns the per-runtime outcomes so setup's isolation step can act on them.
func (a *Cmd) registerMCPEntries(ctx context.Context, targets []skillTarget, dryRun bool) []mcpcfg.Outcome {
	env, err := a.mcpEnv()
	if err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("MCP registration skipped: %v", err))
		return nil
	}
	runtimes := mcpRuntimesFor(targets, env)
	if len(runtimes) == 0 {
		return nil
	}

	ctxName := activeContextName()
	if ctxName == "" {
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"No active context to pin — skipping MCP entry registration (re-run `jentic setup` once registered)."))
		return nil
	}
	binPath, err := mcpcfg.StableBinaryPath()
	if err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("MCP registration skipped (cannot resolve the jentic binary path): %v", err))
		return nil
	}
	entry := mcpcfg.PlainEntry(binPath, ctxName)

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Heading.Render("MCP registration"))
	if dryRun {
		for _, rt := range runtimes {
			fmt.Fprintln(a.Out, "  "+theme.Infof("%-16s would write %s", rt, a.mcpTargetLabel(rt, env)))
		}
		return nil
	}

	var outcomes []mcpcfg.Outcome
	for _, rt := range runtimes {
		out, werr := a.applyMCPEntry(ctx, rt, env, entry)
		if werr != nil {
			fmt.Fprintln(a.Out, "  "+theme.Warnf("%-16s %v", rt, werr))
			continue
		}
		a.reportMCPOutcome(out)
		outcomes = append(outcomes, out)
		if out.Changed || out.Created {
			a.reportConfigRegistered(ctx, rt)
		}
	}

	if len(outcomes) > 1 {
		// One agent per runtime (master §3.10): a shared context means shared
		// audit/permissions/revocation. Suggest, never force.
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"Tip: give each runtime its own agent — register a per-runtime context (e.g. `jentic register --name "+
				ctxName+"-cursor`), then re-run `jentic setup` with that context active."))
	}
	return outcomes
}

// mcpTargetLabel is the display target for one runtime (dry-run and reports).
func (a *Cmd) mcpTargetLabel(rt mcpcfg.Runtime, env mcpcfg.Env) string {
	switch rt {
	case mcpcfg.RuntimeCursor:
		return prettyPath(mcpcfg.CursorConfigPath(env.Home))
	case mcpcfg.RuntimeClaudeDesktop:
		return prettyPath(mcpcfg.ClaudeDesktopConfigPath(env.Home, env.GOOS))
	case mcpcfg.RuntimeClaudeCode:
		return "claude mcp add (user scope)"
	case mcpcfg.RuntimeCodex:
		return prettyPath(mcpcfg.CodexConfigPath(env.Home))
	default:
		return string(rt)
	}
}

// applyMCPEntry writes entry for one runtime via its native mechanism.
func (a *Cmd) applyMCPEntry(ctx context.Context, rt mcpcfg.Runtime, env mcpcfg.Env, entry mcpcfg.Entry) (mcpcfg.Outcome, error) {
	switch rt {
	case mcpcfg.RuntimeCursor:
		return mcpcfg.WriteJSONEntry(rt, mcpcfg.CursorConfigPath(env.Home), entry)
	case mcpcfg.RuntimeClaudeDesktop:
		path := mcpcfg.ClaudeDesktopConfigPath(env.Home, env.GOOS)
		if path == "" {
			return mcpcfg.Outcome{Runtime: rt}, fmt.Errorf("claude desktop config location is not supported on %s", env.GOOS)
		}
		return mcpcfg.WriteJSONEntry(rt, path, entry)
	case mcpcfg.RuntimeClaudeCode:
		return a.runClaudeCodeSteps(ctx, env, entry)
	case mcpcfg.RuntimeCodex:
		return mcpcfg.WriteCodexEntry(mcpcfg.CodexConfigPath(env.Home), entry)
	default:
		return mcpcfg.Outcome{Runtime: rt}, fmt.Errorf("unknown runtime %q", rt)
	}
}

// runClaudeCodeSteps executes the `claude mcp` exec plan (remove-then-add so
// re-runs converge). Claude Code owns its own config file locking; going
// through its CLI is the supported write path.
func (a *Cmd) runClaudeCodeSteps(ctx context.Context, env mcpcfg.Env, entry mcpcfg.Entry) (mcpcfg.Outcome, error) {
	out := mcpcfg.Outcome{Runtime: mcpcfg.RuntimeClaudeCode, Path: "claude mcp add (user scope)"}
	claudePath, err := env.LookPath("claude")
	if err != nil {
		return out, fmt.Errorf("claude binary not found on PATH: %w", err)
	}
	for _, step := range mcpcfg.ClaudeCodeSteps(claudePath, entry) {
		cmd := exec.CommandContext(ctx, step.Argv[0], step.Argv[1:]...) //nolint:gosec // argv is assembled from the resolved claude path and fixed words + validated names.
		cmd.Stdout, cmd.Stderr = nil, nil
		if err := cmd.Run(); err != nil {
			if step.BestEffort {
				continue // removing a not-yet-registered entry legitimately fails
			}
			return out, fmt.Errorf("%s: %w", step.What, err)
		}
	}
	out.Changed = true
	return out, nil
}

// reportMCPOutcome prints one runtime's registration result.
func (a *Cmd) reportMCPOutcome(out mcpcfg.Outcome) {
	label := string(out.Runtime)
	switch {
	case out.Created:
		fmt.Fprintln(a.Out, "  "+theme.Successf("%-16s created %s", label, prettyPath(out.Path)))
	case out.Changed:
		fmt.Fprintln(a.Out, "  "+theme.Successf("%-16s updated %s", label, prettyPath(out.Path)))
	default:
		fmt.Fprintln(a.Out, "  "+theme.Dimf("%-16s %s — already up to date", label, prettyPath(out.Path)))
	}
}

// reportConfigRegistered tells the control plane one runtime's MCP entry was
// written (the mcp.config_registered funnel leg). Strictly best-effort: no
// client, no network, or a non-2xx response never surfaces beyond a dim note —
// a telemetry hiccup must not color an otherwise successful setup.
func (a *Cmd) reportConfigRegistered(ctx context.Context, rt mcpcfg.Runtime) {
	cli, err := clictx.GetControlClient(ctx)
	if err != nil {
		return // unconfigured/degraded session: nothing to report with
	}
	body := control.ReportMcpConfigRegistrationJSONRequestBody{
		Runtime: control.McpConfigRuntime(rt.WireTag()),
	}
	if _, err := cli.ReportMcpConfigRegistrationWithResponse(ctx, body); err != nil {
		fmt.Fprintln(a.Out, theme.Dimf("  (could not report the %s registration to the control plane: %v)", rt, err))
	}
}
