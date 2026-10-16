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
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"runtime"
	"strings"

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

// MCP registration outcome vocabulary for the `skill init` JSON envelope.
const (
	mcpActionCreated   = "created"   // the runtime's config file was created with the entry
	mcpActionUpdated   = "updated"   // the entry was written into an existing config
	mcpActionUnchanged = "unchanged" // the entry was already current
	mcpActionPlanned   = "planned"   // dry run: the entry would be written or checked
	mcpActionError     = "error"     // this runtime failed; see error

	mcpSkippedNoRuntime     = "no_mcp_runtime"         // no selected operator has a detected MCP runtime
	mcpSkippedNoContext     = "no_active_context"      // nothing to pin with --context yet
	mcpSkippedNoBinaryPath  = "binary_path_unresolved" // the stable jentic binary path could not be resolved
	mcpSkippedEnvUnresolved = "env_unresolved"         // home or working directory could not be resolved
)

// mcpResult is one runtime's MCP registration outcome. Path is the config file
// this CLI writes. A runtime registered through its own CLI (claude-code) has
// no such file: Path is empty and Via names the command used instead.
type mcpResult struct {
	Runtime string `json:"runtime"`
	Path    string `json:"path,omitempty"`
	Via     string `json:"via,omitempty"`
	Action  string `json:"action"`
	DryRun  bool   `json:"dry_run"`
	Error   string `json:"error,omitempty"`
}

// newMCPResult starts the envelope row for rt with its config path or, for a
// CLI-registered runtime, the command it registers through.
func newMCPResult(rt mcpcfg.Runtime, env mcpcfg.Env, action string) mcpResult {
	r := mcpResult{Runtime: string(rt), Action: action}
	if rt == mcpcfg.RuntimeClaudeCode {
		r.Via = claudeCodeVia
	} else {
		r.Path = mcpTargetPath(rt, env)
	}
	return r
}

// mcpReport is the `mcp` member of the `skill init` envelope: every runtime
// considered, and why registration was skipped when it was.
type mcpReport struct {
	Results       []mcpResult `json:"results"`
	SkippedReason string      `json:"skipped_reason,omitempty"`
}

// emptyMCPReport is the report for a run that selected no targets.
func emptyMCPReport() *mcpReport {
	return &mcpReport{Results: []mcpResult{}}
}

// mcpRegistration is what registerMCPEntries did: the successful outcomes
// (setup's isolation step acts on them) plus the per-runtime report.
type mcpRegistration struct {
	outcomes      []mcpcfg.Outcome
	results       []mcpResult
	skippedReason string
}

// report renders the registration for the JSON envelope; results encode as
// [] (never null).
func (r mcpRegistration) report() *mcpReport {
	rep := &mcpReport{Results: r.results, SkippedReason: r.skippedReason}
	if rep.Results == nil {
		rep.Results = []mcpResult{}
	}
	return rep
}

// mcpOutcomeAction maps one successful registration onto the action vocabulary.
func mcpOutcomeAction(out mcpcfg.Outcome) string {
	switch {
	case out.Created:
		return mcpActionCreated
	case out.Changed:
		return mcpActionUpdated
	default:
		return mcpActionUnchanged
	}
}

// registerMCPEntries writes the MCP entry for every detected runtime among
// the selected targets and reports each write to the control plane
// (best-effort). It never returns an error to the caller's main flow — every
// failure is reported per-runtime and the rest continue; MCP registration
// must not block the identity/skill provisioning the operator came for.
func (a *Cmd) registerMCPEntries(ctx context.Context, targets []skillTarget, dryRun bool) mcpRegistration {
	var reg mcpRegistration
	env, err := a.mcpEnv()
	if err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("MCP registration skipped: %v", err))
		reg.skippedReason = mcpSkippedEnvUnresolved
		return reg
	}
	runtimes := mcpRuntimesFor(targets, env)
	if len(runtimes) == 0 {
		reg.skippedReason = mcpSkippedNoRuntime
		return reg
	}

	ctxName := activeContextName()
	if ctxName == "" {
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"No active context to pin — skipping MCP entry registration (re-run `jentic setup` once registered)."))
		reg.skippedReason = mcpSkippedNoContext
		return reg
	}
	binPath, err := mcpcfg.StableBinaryPath()
	if err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("MCP registration skipped (cannot resolve the jentic binary path): %v", err))
		reg.skippedReason = mcpSkippedNoBinaryPath
		return reg
	}
	entry := mcpcfg.PlainEntry(binPath, ctxName)

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Heading.Render("MCP registration"))
	if dryRun {
		for _, rt := range runtimes {
			fmt.Fprintln(a.Out, "  "+theme.Infof("%-16s would write %s", rt, a.mcpTargetLabel(rt, env)))
			row := newMCPResult(rt, env, mcpActionPlanned)
			row.DryRun = true
			reg.results = append(reg.results, row)
		}
		return reg
	}

	for _, rt := range runtimes {
		out, werr := a.applyMCPEntry(ctx, rt, env, entry)
		if werr != nil {
			fmt.Fprintln(a.Out, "  "+theme.Warnf("%-16s %v", rt, werr))
			row := newMCPResult(rt, env, mcpActionError)
			row.Error = werr.Error()
			reg.results = append(reg.results, row)
			continue
		}
		a.reportMCPOutcome(out)
		reg.outcomes = append(reg.outcomes, out)
		reg.results = append(reg.results, newMCPResult(rt, env, mcpOutcomeAction(out)))
		if out.Changed || out.Created {
			a.reportConfigRegistered(ctx, rt)
		}
	}

	if len(reg.outcomes) > 1 {
		// One agent per runtime (master §3.10): a shared context means shared
		// audit/permissions/revocation. Suggest, never force.
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"Tip: give each runtime its own agent — register a per-runtime context (e.g. `jentic register --name "+
				ctxName+"-cursor`), then re-run `jentic setup` with that context active."))
	}
	return reg
}

// mcpTargetLabel is the display target for one runtime (dry-run and reports).
func (a *Cmd) mcpTargetLabel(rt mcpcfg.Runtime, env mcpcfg.Env) string {
	return prettyPath(mcpTargetPath(rt, env))
}

// mcpTargetPath is the absolute config path one runtime's entry is written to;
// for claude-code it describes the claude CLI route.
func mcpTargetPath(rt mcpcfg.Runtime, env mcpcfg.Env) string {
	switch rt {
	case mcpcfg.RuntimeCursor:
		return mcpcfg.CursorConfigPath(env.Home)
	case mcpcfg.RuntimeClaudeDesktop:
		return mcpcfg.ClaudeDesktopConfigPath(env.Home, env.GOOS)
	case mcpcfg.RuntimeClaudeCode:
		return claudeCodeRoute
	case mcpcfg.RuntimeCodex:
		return mcpcfg.CodexConfigPath(env.Home)
	default:
		return string(rt)
	}
}

// claudeCodeRoute describes how the claude-code entry is registered: through
// the claude CLI at user scope, not a config file this CLI writes. It is the
// human-facing label; claudeCodeVia is the envelope's `via` value.
const claudeCodeRoute = "claude mcp add (user scope)"

// claudeCodeVia is the command claude-code's MCP entry is registered through.
const claudeCodeVia = "claude mcp add --scope user"

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

// runClaudeCodeSteps converges Claude Code's user-scope config on entry via
// its own CLI (Claude Code owns its config file locking; going through its
// CLI is the supported write path). A read-only `claude mcp get` probe runs
// first: an already-convergent entry is a reported no-op (re-runs never
// rewrite), a stale entry is removed then re-added, an absent entry is added
// directly — so a working entry is only ever removed when a change is
// genuinely needed. Failures carry claude's own stderr, not a bare exit code.
func (a *Cmd) runClaudeCodeSteps(ctx context.Context, env mcpcfg.Env, entry mcpcfg.Entry) (mcpcfg.Outcome, error) {
	out := mcpcfg.Outcome{Runtime: mcpcfg.RuntimeClaudeCode, Path: claudeCodeRoute}
	claudePath, err := env.LookPath("claude")
	if err != nil {
		return out, fmt.Errorf("claude binary not found on PATH: %w", err)
	}

	probe := mcpcfg.ClaudeCodeProbeArgv(claudePath)
	probeOut, probeErr := exec.CommandContext(ctx, probe[0], probe[1:]...).Output() //nolint:gosec // argv is the resolved claude path plus fixed words.
	entryExists := probeErr == nil
	if entryExists && mcpcfg.ClaudeCodeConverged(probeOut, entry) {
		return out, nil // already up to date — no destructive rewrite
	}

	for _, step := range mcpcfg.ClaudeCodeSteps(claudePath, entry, entryExists) {
		cmd := exec.CommandContext(ctx, step.Argv[0], step.Argv[1:]...) //nolint:gosec // argv is assembled from the resolved claude path and fixed words + validated names.
		var stderr bytes.Buffer
		cmd.Stdout, cmd.Stderr = nil, &stderr
		if err := cmd.Run(); err != nil {
			if step.BestEffort {
				continue // removing an already-gone entry legitimately fails
			}
			if diag := strings.TrimSpace(stderr.String()); diag != "" {
				return out, fmt.Errorf("%s: %w: %s", step.What, err, diag)
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
	resp, err := cli.ReportMcpConfigRegistrationWithResponse(ctx, body)
	switch {
	case err != nil:
		fmt.Fprintln(a.Out, theme.Dimf("  (could not report the %s registration to the control plane: %v)", rt, err))
	case resp.StatusCode() < 200 || resp.StatusCode() > 299:
		// The generated client errors only on transport failure — an HTTP
		// 401/422/500 comes back as err == nil, so the promised dim note must
		// key off the status code too.
		fmt.Fprintln(a.Out, theme.Dimf("  (could not report the %s registration to the control plane: HTTP %d)", rt, resp.StatusCode()))
	}
}
