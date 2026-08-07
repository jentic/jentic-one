package cmdcore

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// errOperatorAndAll is returned when --operator and --all are combined; they
// are mutually exclusive rather than silently letting --all win.
var errOperatorAndAll = errors.New("--operator and --all are mutually exclusive; pass one or the other")

// errNothingDetected is returned when a non-interactive run has no explicit
// selection and detection finds nothing. A sentinel so callers with a better
// escape hatch (bootstrap's --skip-skill) can append it to the message.
var errNothingDetected = errors.New("no operators given and none detected")

// promptable reports whether the huh pickers can actually run interactively.
// A TTY alone is not enough: on TERM=dumb (emacs shells, many agent
// harnesses) huh silently falls back to its "accessible" numbered prompts,
// which loop on stdin without honoring Esc, Ctrl-C, or the signal-cancelled
// command context — an inescapable picker (jentic-one#841). Those sessions
// are exactly the ones the #755 defaulting path is for, so treat them as
// non-interactive instead.
func promptable() bool {
	return term.IsTerminal(os.Stdin.Fd()) && os.Getenv("TERM") != "dumb"
}

// skillOptions are shared across the skill subcommands.
type skillOptions struct {
	baseURL   string
	operators []string
	skills    []string
	scope     string
	force     bool
	yes       bool
	dryRun    bool
	all       bool
	json      bool
}

// NewSkillCmd builds the `skill` command group (install/update/remove the
// agent-facing skill files). Shared by both trees via cmdcore.
func NewSkillCmd(app *App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "skill",
		Short: "Generate the Jentic skill set into your agent's native layout",
		Long: "skill writes the Jentic skill set into each supported agent runtime's\n" +
			"native layout — a clean, spec-conformant SKILL.md per skill for\n" +
			"claude-code, cursor, and hermes, or a named pointer block in AGENTS.md\n" +
			"for codex and generic — so the agent discovers how to use Jentic and the\n" +
			"spec-flywheel skills without you hand-writing anything.\n\n" +
			"The shipped set is jentic (how to use the CLI), contribute-spec-fix, and\n" +
			"import-new-api; pass --skill to install a subset.\n\n" +
			"Writes are idempotent: owned SKILL.md files carry provenance in a sidecar\n" +
			"and AGENTS.md content lives in named managed blocks, so re-running never\n" +
			"clobbers your own edits around them.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			// Bare `jentic skill` behaves like `skill init`.
			return app.skillInit(cmd, &skillOptions{})
		},
	}
	cmd.AddCommand(newSkillInitCmd(app))
	cmd.AddCommand(newSkillListCmd(app))
	cmd.AddCommand(newSkillUpdateCmd(app))
	cmd.AddCommand(newSkillRemoveCmd(app))
	return cmd
}

func newSkillInitCmd(app *App) *cobra.Command {
	opts := &skillOptions{}
	cmd := &cobra.Command{
		Use:   "init",
		Short: "Generate the Jentic skill set for one or more operators",
		Long: "init detects which agent runtimes you have, lets you pick the targets\n" +
			"and placement (or pass --operator/--scope), and writes the Jentic skill\n" +
			"set into each one's native layout.\n\n" +
			"Passing --operator or --all skips every prompt, including the placement\n" +
			"one: each operator uses its default scope unless --scope is given\n" +
			"(preview with --dry-run). --skill limits the set (default: all shipped\n" +
			"skills); there is no interactive skill picker.\n\n" +
			"Non-interactively (--yes, pipes, agent sessions) it defaults to the\n" +
			"detected operators, echoing each resolved path before writing.",
		Example: "  jentic skill init\n" +
			"  jentic skill init --operator claude,cursor\n" +
			"  jentic skill init --skill jentic --all --yes\n" +
			"  jentic skill init --operator generic --dry-run",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.skillInit(cmd, opts)
		},
	}
	cmd.Flags().StringSliceVar(&opts.operators, "operator", nil, "operators to target (repeatable or comma-separated)")
	cmd.Flags().StringSliceVar(&opts.skills, "skill", nil, "skills to install (repeatable or comma-separated; default: all shipped)")
	cmd.Flags().StringVar(&opts.scope, "scope", "", "placement scope: user or project (default: per-operator)")
	cmd.Flags().BoolVar(&opts.force, "force", false, "overwrite content you have manually edited")
	cmd.Flags().BoolVar(&opts.yes, "yes", false, "non-interactive: no pickers; with no --operator/--all, target the detected operators")
	cmd.Flags().BoolVar(&opts.dryRun, "dry-run", false, "print target paths without writing")
	cmd.Flags().BoolVar(&opts.all, "all", false, "target every supported operator")
	cmd.Flags().StringVar(&opts.baseURL, "base-url", "", "Jentic control-plane base URL")
	return cmd
}

func newSkillListCmd(app *App) *cobra.Command {
	opts := &skillOptions{}
	cmd := &cobra.Command{
		Use:   "list",
		Short: "Show supported operators and where each skill is installed",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.skillList(cmd, opts)
		},
	}
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON output")
	return cmd
}

func newSkillUpdateCmd(app *App) *cobra.Command {
	opts := &skillOptions{}
	cmd := &cobra.Command{
		Use:   "update",
		Short: "Re-render installed skills with the current base URL",
		Long: "update re-renders every installed (skill, operator, scope) with the\n" +
			"currently resolved base URL and rewrites it when the recorded content\n" +
			"hash differs. A base-URL change therefore legitimately rewrites the\n" +
			"jentic skill (its body carries the interpolated URL). Manually-edited\n" +
			"content is reported and left alone unless --force.",
		Example: "  jentic skill update\n" +
			"  jentic skill update --operator claude\n" +
			"  jentic skill update --skill jentic --force",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.SkillUpdate(cmd, opts)
		},
	}
	cmd.Flags().StringSliceVar(&opts.operators, "operator", nil, "operators to update (repeatable or comma-separated; default: all)")
	cmd.Flags().StringSliceVar(&opts.skills, "skill", nil, "skills to update (repeatable or comma-separated; default: all shipped)")
	cmd.Flags().BoolVar(&opts.force, "force", false, "overwrite content you have manually edited")
	cmd.Flags().BoolVar(&opts.dryRun, "dry-run", false, "print what would change without writing")
	cmd.Flags().StringVar(&opts.baseURL, "base-url", "", "Jentic control-plane base URL")
	return cmd
}

func newSkillRemoveCmd(app *App) *cobra.Command {
	opts := &skillOptions{}
	cmd := &cobra.Command{
		Use:   "remove",
		Short: "Remove installed Jentic skills from one or more operators",
		Example: "  jentic skill remove --operator cursor\n" +
			"  jentic skill remove --skill import-new-api --operator cursor\n" +
			"  jentic skill remove --all --force",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.skillRemove(cmd, opts)
		},
	}
	cmd.Flags().StringSliceVar(&opts.operators, "operator", nil, "operators to clean up (repeatable or comma-separated)")
	cmd.Flags().StringSliceVar(&opts.skills, "skill", nil, "skills to remove (repeatable or comma-separated; default: all shipped)")
	cmd.Flags().StringVar(&opts.scope, "scope", "", "placement scope to remove from: user or project (default: every scope where the skill is installed)")
	cmd.Flags().BoolVar(&opts.all, "all", false, "remove from every supported operator")
	cmd.Flags().BoolVar(&opts.force, "force", false, "remove even content you have manually edited")
	cmd.Flags().BoolVar(&opts.dryRun, "dry-run", false, "print what would be removed without deleting anything")
	return cmd
}

// detectEnv resolves the skill detection environment: the injected probe when
// set (tests), otherwise the real OS — PATH and filesystem probes wired to the
// standard library. The real probe errors if home or working directory cannot
// be resolved, since every target path is rooted at one of them and proceeding
// with empty bases would write files to surprising places.
func (a *App) detectEnv() (skillgen.DetectEnv, error) {
	if a.DetectEnv != nil {
		return a.DetectEnv()
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return skillgen.DetectEnv{}, fmt.Errorf("resolve home directory: %w", err)
	}
	cwd, err := os.Getwd()
	if err != nil {
		return skillgen.DetectEnv{}, fmt.Errorf("resolve working directory: %w", err)
	}
	return skillgen.DetectEnv{
		Home: home,
		Cwd:  cwd,
		Lookup: func(name string) bool {
			_, err := exec.LookPath(name)
			return err == nil
		},
		Stat: func(path string) bool {
			_, err := os.Stat(path)
			return err == nil
		},
	}, nil
}

// resolveScope maps the --scope flag to a skillgen.Scope (empty = per-operator
// default).
func resolveScope(flag string) (skillgen.Scope, error) {
	switch strings.ToLower(strings.TrimSpace(flag)) {
	case "":
		return "", nil
	case "user":
		return skillgen.ScopeUser, nil
	case "project":
		return skillgen.ScopeProject, nil
	default:
		return "", fmt.Errorf("invalid --scope %q (want \"user\" or \"project\")", flag)
	}
}

// resolveBaseURL resolves the control-plane base URL from config + flag.
func (a *App) resolveBaseURL(baseURLFlag string) string {
	cfg, err := config.Load(a.Paths)
	baseURL := config.DefaultBaseURL
	if err == nil {
		baseURL = cfg.ResolvedBaseURLOr(baseURLFlag)
	} else if baseURLFlag != "" {
		baseURL = baseURLFlag
	}
	return baseURL
}

// selectSkills resolves which skills to act on: the --skill selection when
// given (validated against the shipped set, like --operator against the
// operator registry), otherwise the full bundled set. The returned names are
// in BundledNames() order for stable output.
func selectSkills(want []string) ([]string, error) {
	all := skillgen.BundledNames()
	if len(want) == 0 {
		return all, nil
	}
	known := map[string]bool{}
	for _, n := range all {
		known[n] = true
	}
	seen := map[string]bool{}
	var unknown []string
	for _, w := range want {
		w = strings.TrimSpace(w)
		if w == "" {
			continue
		}
		if !known[w] {
			unknown = append(unknown, w)
			continue
		}
		seen[w] = true
	}
	if len(unknown) > 0 {
		return nil, fmt.Errorf("unknown skill(s): %s (supported: %s)",
			strings.Join(unknown, ", "), strings.Join(all, ", "))
	}
	// Preserve the shipped (sorted) order.
	out := make([]string, 0, len(seen))
	for _, n := range all {
		if seen[n] {
			out = append(out, n)
		}
	}
	return out, nil
}

// canonicalSet loads the selected skills, stamped with the resolved base URL.
// The deployment also serves this same canonical content at /skills/<name>.md
// (#651); a hosted-fetch source can be wired here later. For now it always uses
// the bundled copies.
func (a *App) canonicalSet(baseURLFlag string, skills []string) ([]skillgen.Canonical, error) {
	names, err := selectSkills(skills)
	if err != nil {
		return nil, err
	}
	baseURL := a.resolveBaseURL(baseURLFlag)
	set := make([]skillgen.Canonical, 0, len(names))
	for _, name := range names {
		c, err := skillgen.Bundled(name, baseURL)
		if err != nil {
			return nil, err
		}
		set = append(set, c)
	}
	return set, nil
}

// skillTarget pairs an adapter with the concrete placement scope resolved for
// it (flag > interactive choice > adapter default), so the write path never
// has to re-derive placement.
type skillTarget struct {
	adapter skillgen.Adapter
	scope   skillgen.Scope
}

// chooseTargets resolves which operators to write and at which scope, from
// flags + detection + the interactive pickers. A nil, error-free return means
// the user dismissed the picker with nothing selected.
//
// Non-interactive runs (--yes or stdin is not promptable — pipes, CI, agent
// sessions, dumb terminals) with no explicit --operator/--all fall back to the
// *detected* operators instead of erroring (#755); the resolved targets are
// echoed before anything is written. When nothing is detected either, the
// error spells out the flags to pass.
func (a *App) chooseTargets(reg *skillgen.Registry, env skillgen.DetectEnv, opts *skillOptions) ([]skillTarget, error) {
	flagScope, err := resolveScope(opts.scope)
	if err != nil {
		return nil, err
	}
	if opts.all && len(opts.operators) > 0 {
		return nil, errOperatorAndAll
	}

	var adapters []skillgen.Adapter
	switch {
	case opts.all:
		adapters = reg.Adapters()
	case len(opts.operators) > 0:
		resolved, unknown := reg.ResolveAll(opts.operators)
		if len(unknown) > 0 {
			return nil, fmt.Errorf("unknown operator(s): %s (supported: %s)",
				strings.Join(unknown, ", "), strings.Join(reg.Names(), ", "))
		}
		adapters = resolved
	case opts.yes || !promptable():
		// #755: no selection and no way (or wish) to prompt — degrade to the
		// detected operators rather than aborting, so agent sessions and
		// scripts get a working install by default.
		adapters = reg.Detected(env)
		if len(adapters) == 0 {
			return nil, fmt.Errorf("%w; pass --operator <names> or --all (supported: %s)",
				errNothingDetected, strings.Join(reg.Names(), ", "))
		}
		targets := resolveTargets(adapters, flagScope)
		a.echoDefaultedTargets(targets, env)
		return targets, nil
	default:
		adapters, err = a.pickOperators(reg, env)
		if err != nil || len(adapters) == 0 {
			return nil, err
		}
		if flagScope == "" {
			// #552: placement is a real choice (user-global vs this repo), so
			// ask instead of deciding silently when we're interactive anyway.
			return a.pickScopes(adapters, env)
		}
	}
	return resolveTargets(adapters, flagScope), nil
}

// resolveTargets pairs adapters with flagScope, or their per-operator default
// (the #552-ratified policy) when no --scope was given.
func resolveTargets(adapters []skillgen.Adapter, flagScope skillgen.Scope) []skillTarget {
	targets := make([]skillTarget, 0, len(adapters))
	for _, ad := range adapters {
		scope := flagScope
		if scope == "" {
			scope = ad.DefaultScope()
		}
		targets = append(targets, skillTarget{adapter: ad, scope: scope})
	}
	return targets
}

// displayTarget resolves a representative target path for one operator+scope
// for display/echo purposes. Owned-file operators write one file per skill, so
// this uses the shipped set's first skill name purely to locate the operator's
// skills directory; AGENTS.md operators write one shared file regardless of
// skill, so the name is immaterial there.
func displayTarget(ad skillgen.Adapter, scope skillgen.Scope, env skillgen.DetectEnv) string {
	names := skillgen.BundledNames()
	name := "jentic"
	if len(names) > 0 {
		name = names[0]
	}
	return ad.Target(scope, name, env)
}

// echoDefaultedTargets announces an auto-defaulted selection (non-interactive,
// nothing explicit) with each resolved scope+path *before* anything is
// written, so a silent default can never place a file somewhere surprising.
// A project-scope target inside a git worktree gets a real warning on top of
// the echo: nobody explicitly asked for that write, and the file will show up
// in `git status` — the #552 repo-pollution case.
func (a *App) echoDefaultedTargets(targets []skillTarget, env skillgen.DetectEnv) {
	fmt.Fprintln(a.Out, theme.Dim.Render("No --operator/--all given; defaulting to detected operators (--operator/--all overrides):"))
	for _, t := range targets {
		target := displayTarget(t.adapter, t.scope, env)
		fmt.Fprintln(a.Out, "  "+theme.Infof("%-8s -> %s (%s scope)", t.adapter.Operator(), prettyPath(target), t.scope))
		if t.scope == skillgen.ScopeProject && insideGitWorktree(filepath.Dir(target)) {
			fmt.Fprintln(a.Out, "  "+theme.Warnf("%-8s files written here are inside a git repo and will appear in git status; pass --scope user to keep them out of the checkout", t.adapter.Operator()))
		}
	}
}

// insideGitWorktree reports whether dir sits under a git checkout, by walking
// up to the first `.git` entry (a dir for a normal clone, a file for a
// worktree/submodule).
func insideGitWorktree(dir string) bool {
	for {
		if _, err := os.Stat(filepath.Join(dir, ".git")); err == nil {
			return true
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return false
		}
		dir = parent
	}
}

// pickOperators runs the interactive multi-select with detected operators
// pre-checked.
func (a *App) pickOperators(reg *skillgen.Registry, env skillgen.DetectEnv) ([]skillgen.Adapter, error) {
	detected := map[skillgen.Operator]bool{}
	for _, d := range reg.Detected(env) {
		detected[d.Operator()] = true
	}

	var selected []string
	optsList := make([]huh.Option[string], 0, len(reg.Adapters()))
	for _, ad := range reg.Adapters() {
		name := string(ad.Operator())
		label := name
		if detected[ad.Operator()] {
			label += " (detected)"
			selected = append(selected, name)
		}
		optsList = append(optsList, huh.NewOption(label, name))
	}

	form := prompt.NewForm(huh.NewGroup(
		huh.NewMultiSelect[string]().
			Title("Generate the Jentic skill for which operators?").
			Description("Detected runtimes are pre-selected. Space toggles, Enter confirms.").
			Options(optsList...).
			Value(&selected),
	))
	if err := form.Run(); err != nil {
		return nil, err
	}
	if len(selected) == 0 {
		fmt.Fprintln(a.Out, theme.Dim.Render("No operators selected."))
		return nil, nil
	}
	resolved, _ := reg.ResolveAll(selected)
	return resolved, nil
}

// pickScopes asks, per selected operator, whether the skill goes user-global
// or into the current project, showing the exact resolved path for each
// choice. The #552-ratified per-operator default is pre-selected, so Enter
// through keeps today's behavior — but the placement is now a visible choice.
func (a *App) pickScopes(adapters []skillgen.Adapter, env skillgen.DetectEnv) ([]skillTarget, error) {
	values := make([]string, len(adapters))
	fields := make([]huh.Field, 0, len(adapters))
	for i, ad := range adapters {
		def := ad.DefaultScope()
		values[i] = string(def)

		userOpt := huh.NewOption(scopeLabel(ad, skillgen.ScopeUser, def, env), string(skillgen.ScopeUser))
		projOpt := huh.NewOption(scopeLabel(ad, skillgen.ScopeProject, def, env), string(skillgen.ScopeProject))
		ordered := []huh.Option[string]{userOpt, projOpt}
		if def == skillgen.ScopeProject {
			ordered = []huh.Option[string]{projOpt, userOpt}
		}

		fields = append(fields, huh.NewSelect[string]().
			Title(fmt.Sprintf("Where should the %s skill go?", ad.Operator())).
			Options(ordered...).
			Value(&values[i]))
	}

	if err := prompt.NewForm(huh.NewGroup(fields...)).Run(); err != nil {
		return nil, err
	}

	targets := make([]skillTarget, len(adapters))
	for i, ad := range adapters {
		targets[i] = skillTarget{adapter: ad, scope: skillgen.Scope(values[i])}
	}
	return targets, nil
}

// scopeLabel renders one scope choice with its resolved path and a default tag.
func scopeLabel(ad skillgen.Adapter, scope, def skillgen.Scope, env skillgen.DetectEnv) string {
	label := fmt.Sprintf("%s — %s", scope, prettyPath(displayTarget(ad, scope, env)))
	if scope == def {
		label += " (default)"
	}
	return label
}

func (a *App) skillInit(_ *cobra.Command, opts *skillOptions) error {
	reg := skillgen.DefaultRegistry()
	env, err := a.detectEnv()
	if err != nil {
		return err
	}

	targets, err := a.chooseTargets(reg, env, opts)
	if err != nil {
		// Esc/Ctrl-C in a picker is "never mind", not a failure — same
		// Cancelled./exit-0 idiom as every other wizard in the CLI (and the
		// same outcome as confirming an empty selection).
		if errors.Is(err, huh.ErrUserAborted) {
			fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
			return nil
		}
		return err
	}
	if len(targets) == 0 {
		return nil
	}

	return a.writeSkill(targets, env, opts)
}

// writeSkill renders each selected skill into each target and prints the
// per-operator, per-skill outcome plus a closing hint. It is the shared body of
// `skill init` and `bootstrap` so both report writes identically. Targets are
// resolved by the caller (so selection errors surface before any side effects).
//
// For a shared AGENTS.md, each skill's named block is spliced in turn: Apply
// re-reads the file between skills, so sibling blocks written earlier in the
// loop are preserved and never clobbered.
func (a *App) writeSkill(targets []skillTarget, env skillgen.DetectEnv, opts *skillOptions) error {
	set, err := a.canonicalSet(opts.baseURL, opts.skills)
	if err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Heading.Render("Jentic skills"))

	var wrote int
	for _, t := range targets {
		for _, c := range set {
			out, aerr := skillgen.Apply(t.adapter, c, env, skillgen.ApplyOptions{
				Scope:  t.scope,
				Force:  opts.force,
				DryRun: opts.dryRun,
			})
			if aerr != nil {
				fmt.Fprintln(a.Out, "  "+theme.Warnf("%s/%s: %v", t.adapter.Operator(), c.Name, aerr))
				continue
			}
			a.reportOutcome(out, t.scope, opts.dryRun)
			if out.Changed && !opts.dryRun {
				wrote++
			}
		}
	}

	if opts.dryRun {
		fmt.Fprintln(a.Out, theme.Dim.Render("Dry run — nothing was written."))
		return nil
	}
	if wrote > 0 {
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Dim.Render("Your agent picks the skills up on its next start. Re-run after a Jentic update to refresh."))
	}
	return nil
}

// reportOutcome prints a single (operator, skill) result line, tagged with the
// scope the write resolved to so placement is always visible in the output.
func (a *App) reportOutcome(out skillgen.Outcome, scope skillgen.Scope, dryRun bool) {
	label := fmt.Sprintf("%s/%s", out.Operator, out.Skill)
	rel := fmt.Sprintf("%s (%s scope)", prettyPath(out.Path), scope)
	switch {
	case out.UserEdits:
		fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %s — manual edits detected; re-run with --force to overwrite", label, rel))
	case dryRun:
		verb := "would update"
		if out.Created {
			verb = "would create"
		}
		fmt.Fprintln(a.Out, "  "+theme.Infof("%-24s %s %s", label, verb, rel))
	case out.Skipped:
		fmt.Fprintln(a.Out, "  "+theme.Dimf("%-24s %s — already up to date", label, rel))
	case out.Created:
		fmt.Fprintln(a.Out, "  "+theme.Successf("%-24s created %s", label, rel))
	default:
		fmt.Fprintln(a.Out, "  "+theme.Successf("%-24s updated %s", label, rel))
	}
}

func (a *App) skillList(cmd *cobra.Command, opts *skillOptions) error {
	reg := skillgen.DefaultRegistry()
	env, err := a.detectEnv()
	if err != nil {
		return err
	}
	detected := map[skillgen.Operator]bool{}
	for _, d := range reg.Detected(env) {
		detected[d.Operator()] = true
	}

	if JSONOrPretty(cmd, opts.json) {
		return a.skillListJSON(reg, env, detected)
	}
	return a.skillListPretty(reg, env, detected)
}

// skillListPretty renders the human listing per skill. "Detected" (the runtime
// looks present) and "installed" (a skill artifact actually exists on disk) are
// reported separately — #752 — and each shipped skill is probed individually so
// a partial install is visible.
func (a *App) skillListPretty(reg *skillgen.Registry, env skillgen.DetectEnv, detected map[skillgen.Operator]bool) error {
	names := skillgen.BundledNames()
	fmt.Fprintln(a.Out, theme.Heading.Render("Supported operators"))
	for _, ad := range reg.Adapters() {
		glyph := theme.Dim.Render(theme.SelectOff)
		tag := ""
		if detected[ad.Operator()] {
			glyph = theme.Success.Render(theme.SelectOn)
			tag = " " + theme.Dim.Render("(detected)")
		}
		fmt.Fprintln(a.Out, glyph+" "+theme.Accent.Render(string(ad.Operator()))+tag)

		var shown int
		for _, name := range names {
			// Print every scope that actually holds this skill: user and
			// project installs can coexist, and hiding the second would make
			// `skill list` lie by omission (the same conflation #752 fixed).
			for _, st := range skillgen.InstallStates(ad, name, env) {
				if !st.Installed {
					continue
				}
				line := theme.Field(name, fmt.Sprintf("%s (%s scope)", prettyPath(st.Path), st.Scope))
				if st.UserEdits {
					line += " " + theme.Warn.Render("(manually edited)")
				}
				fmt.Fprintln(a.Out, "    "+line)
				shown++
			}
		}
		if shown == 0 {
			fmt.Fprintln(a.Out, "    "+theme.Field("installed", "no"))
			fmt.Fprintln(a.Out, "    "+theme.Field("target", prettyPath(displayTarget(ad, ad.DefaultScope(), env))))
		}
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("Install with: jentic skill init [--operator <names>] [--skill <names>]"))
	return nil
}

func (a *App) skillListJSON(reg *skillgen.Registry, env skillgen.DetectEnv, detected map[skillgen.Operator]bool) error {
	// installRow is now per (skill, scope): the multi-skill split adds a
	// `skill` dimension to the row shape (a JSON compat surface — called out in
	// the PR body).
	type installRow struct {
		Skill     string `json:"skill"`
		Scope     string `json:"scope"`
		Path      string `json:"path"`
		Installed bool   `json:"installed"`
		UserEdits bool   `json:"user_edits"`
	}
	type row struct {
		Operator string `json:"operator"`
		Detected bool   `json:"detected"`
		// Installed reports whether any shipped skill exists at any scope —
		// detection alone never implies it (#752).
		Installed     bool         `json:"installed"`
		InstalledPath string       `json:"installed_path,omitempty"`
		Target        string       `json:"target"`
		Scope         string       `json:"scope"`
		Installs      []installRow `json:"installs"`
	}
	names := skillgen.BundledNames()
	rows := make([]row, 0, len(reg.Adapters()))
	for _, ad := range reg.Adapters() {
		r := row{
			Operator: string(ad.Operator()),
			Detected: detected[ad.Operator()],
			Target:   displayTarget(ad, ad.DefaultScope(), env),
			Scope:    string(ad.DefaultScope()),
		}
		for _, name := range names {
			for _, st := range skillgen.InstallStates(ad, name, env) {
				r.Installs = append(r.Installs, installRow{
					Skill:     st.Skill,
					Scope:     string(st.Scope),
					Path:      st.Path,
					Installed: st.Installed,
					UserEdits: st.UserEdits,
				})
				if st.Installed && !r.Installed {
					r.Installed = true
					r.InstalledPath = st.Path
				}
			}
		}
		rows = append(rows, r)
	}
	return WriteJSON(a.Out, map[string]any{"operators": rows})
}

// SkillUpdateDefault runs SkillUpdate with the default (empty) options. The ctl
// tree calls it to refresh installed skills after a CLI upgrade without needing
// to construct the unexported skillOptions.
func (a *App) SkillUpdateDefault(cmd *cobra.Command) error {
	return a.SkillUpdate(cmd, &skillOptions{})
}

// SkillUpdate re-renders every installed (skill, operator, scope) with the
// currently resolved base URL and rewrites it when the recorded content hash
// differs. It only touches installs that already exist — it never creates a new
// one — so it is a refresh, not an install (#407).
func (a *App) SkillUpdate(_ *cobra.Command, opts *skillOptions) error {
	reg := skillgen.DefaultRegistry()
	env, err := a.detectEnv()
	if err != nil {
		return err
	}

	var adapters []skillgen.Adapter
	if len(opts.operators) > 0 {
		resolved, unknown := reg.ResolveAll(opts.operators)
		if len(unknown) > 0 {
			return fmt.Errorf("unknown operator(s): %s (supported: %s)",
				strings.Join(unknown, ", "), strings.Join(reg.Names(), ", "))
		}
		adapters = resolved
	} else {
		adapters = reg.Adapters()
	}

	set, err := a.canonicalSet(opts.baseURL, opts.skills)
	if err != nil {
		return err
	}
	byName := map[string]skillgen.Canonical{}
	for _, c := range set {
		byName[c.Name] = c
	}

	fmt.Fprintln(a.Out, theme.Heading.Render("Update Jentic skills"))
	var changed, blocked int
	for _, ad := range adapters {
		for _, c := range set {
			for _, st := range skillgen.InstallStates(ad, c.Name, env) {
				if !st.Installed {
					continue
				}
				out, uerr := skillgen.Apply(ad, c, env, skillgen.ApplyOptions{
					Scope:  st.Scope,
					Force:  opts.force,
					DryRun: opts.dryRun,
				})
				label := fmt.Sprintf("%s/%s", ad.Operator(), c.Name)
				switch {
				case uerr != nil:
					fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %v", label, uerr))
				case out.UserEdits:
					blocked++
					fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %s — manual edits detected; re-run with --force to overwrite", label, prettyPath(out.Path)))
				case out.Changed && opts.dryRun:
					changed++
					fmt.Fprintln(a.Out, "  "+theme.Infof("%-24s would update %s (%s scope)", label, prettyPath(out.Path), st.Scope))
				case out.Changed:
					changed++
					fmt.Fprintln(a.Out, "  "+theme.Successf("%-24s updated %s (%s scope)", label, prettyPath(out.Path), st.Scope))
				default:
					fmt.Fprintln(a.Out, "  "+theme.Dimf("%-24s %s — already up to date", label, prettyPath(out.Path)))
				}
			}
		}
	}
	if changed == 0 && blocked == 0 {
		fmt.Fprintln(a.Out, theme.Dim.Render("No installed skills to update."))
	}
	if opts.dryRun {
		fmt.Fprintln(a.Out, theme.Dim.Render("Dry run — nothing was written."))
	}
	if blocked > 0 {
		fmt.Fprintln(a.Out, theme.Dim.Render("Re-run with --force to overwrite content you have edited."))
	}
	return nil
}

func (a *App) skillRemove(_ *cobra.Command, opts *skillOptions) error {
	scope, err := resolveScope(opts.scope)
	if err != nil {
		return err
	}
	skills, err := selectSkills(opts.skills)
	if err != nil {
		return err
	}
	reg := skillgen.DefaultRegistry()
	env, err := a.detectEnv()
	if err != nil {
		return err
	}

	var adapters []skillgen.Adapter
	switch {
	case opts.all && len(opts.operators) > 0:
		return errOperatorAndAll
	case opts.all:
		adapters = reg.Adapters()
	case len(opts.operators) > 0:
		resolved, unknown := reg.ResolveAll(opts.operators)
		if len(unknown) > 0 {
			return fmt.Errorf("unknown operator(s): %s (supported: %s)",
				strings.Join(unknown, ", "), strings.Join(reg.Names(), ", "))
		}
		adapters = resolved
	default:
		return errors.New("no operators given; pass --operator <names> or --all")
	}

	fmt.Fprintln(a.Out, theme.Heading.Render("Remove Jentic skills"))
	var blocked int
	for _, ad := range adapters {
		for _, name := range skills {
			c := skillgen.Canonical{Name: name}
			// No --scope means "remove my install", not "remove at the default
			// placement": probe both scopes and strip every install found, so
			// an install made with --scope project (or via the interactive
			// scope prompt) is removable without the user re-deriving its scope.
			scopes := []skillgen.Scope{scope}
			if scope == "" {
				scopes = scopes[:0]
				for _, st := range skillgen.InstallStates(ad, name, env) {
					if st.Installed {
						scopes = append(scopes, st.Scope)
					}
				}
				if len(scopes) == 0 {
					continue
				}
			}
			label := fmt.Sprintf("%s/%s", ad.Operator(), name)
			for _, sc := range scopes {
				out, rerr := skillgen.Remove(ad, c, env, skillgen.RemoveOptions{
					Scope:  sc,
					Force:  opts.force,
					DryRun: opts.dryRun,
				})
				switch {
				case rerr != nil:
					fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %v", label, rerr))
				case out.UserEdits:
					blocked++
					fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %s — manual edits detected; re-run with --force to remove", label, prettyPath(out.Path)))
				case out.Missing:
					// Nothing to remove for this (skill, scope): stay quiet to
					// avoid a wall of "nothing to remove" across the set.
				case opts.dryRun:
					fmt.Fprintln(a.Out, "  "+theme.Infof("%-24s would remove from %s", label, prettyPath(out.Path)))
				case out.Removed:
					fmt.Fprintln(a.Out, "  "+theme.Successf("%-24s removed from %s", label, prettyPath(out.Path)))
				}
			}
		}
	}
	if opts.dryRun {
		fmt.Fprintln(a.Out, theme.Dim.Render("Dry run — nothing was removed."))
	}
	if blocked > 0 {
		fmt.Fprintln(a.Out, theme.Dim.Render("Re-run with --force to remove blocks you have edited."))
	}
	return nil
}

// prettyPath shortens an absolute path under $HOME to a ~-relative form for
// display.
func prettyPath(p string) string {
	home, err := os.UserHomeDir()
	if err == nil && home != "" {
		if rel, rerr := filepath.Rel(home, p); rerr == nil && !strings.HasPrefix(rel, "..") {
			return filepath.Join("~", rel)
		}
	}
	return p
}
