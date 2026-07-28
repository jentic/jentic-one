package cmd

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// errOperatorAndAll is returned when --operator and --all are combined; they
// are mutually exclusive rather than silently letting --all win.
var errOperatorAndAll = errors.New("--operator and --all are mutually exclusive; pass one or the other")

// skillOptions are shared across the skill subcommands.
type skillOptions struct {
	baseURL   string
	operators []string
	scope     string
	force     bool
	yes       bool
	dryRun    bool
	all       bool
	json      bool
}

func newSkillCmd(app *App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "skill",
		Short: "Generate the Jentic CLI-usage skill into your agent's native layout",
		Long: "skill writes a \"how to use Jentic via the CLI\" skill into each supported\n" +
			"agent runtime's native layout — a dedicated SKILL.md for claude-code,\n" +
			"cursor, and hermes, or a spliced block in AGENTS.md for codex and generic —\n" +
			"so the agent knows the platform loop (register -> request access ->\n" +
			"search/inspect/execute) without you hand-writing anything.\n\n" +
			"Writes are idempotent: generated content lives in a clearly-marked managed\n" +
			"block, so re-running never clobbers your own edits around it.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			// Bare `jentic skill` behaves like `skill init`.
			return app.skillInit(cmd, &skillOptions{})
		},
	}
	cmd.AddCommand(newSkillInitCmd(app))
	cmd.AddCommand(newSkillListCmd(app))
	cmd.AddCommand(newSkillRemoveCmd(app))
	return cmd
}

func newSkillInitCmd(app *App) *cobra.Command {
	opts := &skillOptions{}
	cmd := &cobra.Command{
		Use:   "init",
		Short: "Generate the Jentic skill for one or more operators",
		Long: "init detects which agent runtimes you have, lets you pick the targets\n" +
			"and placement (or pass --operator/--scope), and writes the Jentic\n" +
			"CLI-usage skill into each one's native layout.\n\n" +
			"Passing --operator or --all skips every prompt, including the placement\n" +
			"one: each operator uses its default scope unless --scope is given\n" +
			"(preview with --dry-run).\n\n" +
			"Non-interactively (--yes, pipes, agent sessions) it defaults to the\n" +
			"detected operators, echoing each resolved path before writing.",
		Example: "  jentic skill init\n" +
			"  jentic skill init --operator claude,cursor\n" +
			"  jentic skill init --all --yes\n" +
			"  jentic skill init --operator generic --dry-run",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.skillInit(cmd, opts)
		},
	}
	cmd.Flags().StringSliceVar(&opts.operators, "operator", nil, "operators to target (repeatable or comma-separated)")
	cmd.Flags().StringVar(&opts.scope, "scope", "", "placement scope: user or project (default: per-operator)")
	cmd.Flags().BoolVar(&opts.force, "force", false, "overwrite a managed block you have manually edited")
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
		Short: "Show supported operators: which are detected and where the skill is installed",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.skillList(cmd, opts)
		},
	}
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON output")
	return cmd
}

func newSkillRemoveCmd(app *App) *cobra.Command {
	opts := &skillOptions{}
	cmd := &cobra.Command{
		Use:   "remove",
		Short: "Remove the managed Jentic skill from one or more operators",
		Example: "  jentic skill remove --operator cursor\n" +
			"  jentic skill remove --operator cursor --dry-run\n" +
			"  jentic skill remove --all --force",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.skillRemove(cmd, opts)
		},
	}
	cmd.Flags().StringSliceVar(&opts.operators, "operator", nil, "operators to clean up (repeatable or comma-separated)")
	cmd.Flags().StringVar(&opts.scope, "scope", "", "placement scope to remove from: user or project (default: every scope where the skill is installed)")
	cmd.Flags().BoolVar(&opts.all, "all", false, "remove from every supported operator")
	cmd.Flags().BoolVar(&opts.force, "force", false, "remove even a managed block you have manually edited")
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

// canonicalContent loads the bundled skill, stamped with the resolved base URL.
// The deployment also serves this same canonical content at /skills/jentic.md
// (#651); a hosted-fetch source can be wired here later. For now it always
// uses the bundled copy.
func (a *App) canonicalContent(baseURLFlag string) (skillgen.Canonical, error) {
	cfg, err := config.Load(a.Paths)
	baseURL := config.DefaultBaseURL
	if err == nil {
		baseURL = cfg.ResolvedBaseURLOr(baseURLFlag)
	} else if baseURLFlag != "" {
		baseURL = baseURLFlag
	}
	return skillgen.Bundled(baseURL)
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
// Non-interactive runs (--yes or stdin is not a terminal — pipes, CI, agent
// sessions) with no explicit --operator/--all fall back to the *detected*
// operators instead of erroring (#755); the resolved targets are echoed before
// anything is written. When nothing is detected either, the error spells out
// the flags to pass.
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
	case opts.yes || !term.IsTerminal(os.Stdin.Fd()):
		// #755: no selection and no way (or wish) to prompt — degrade to the
		// detected operators rather than aborting, so agent sessions and
		// scripts get a working install by default.
		adapters = reg.Detected(env)
		if len(adapters) == 0 {
			return nil, errors.New("no operators given and none detected; pass --operator <names> or --all (supported: " +
				strings.Join(reg.Names(), ", ") + ")")
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

// echoDefaultedTargets announces an auto-defaulted selection (non-interactive,
// nothing explicit) with each resolved scope+path *before* anything is
// written, so a silent default can never place a file somewhere surprising.
func (a *App) echoDefaultedTargets(targets []skillTarget, env skillgen.DetectEnv) {
	fmt.Fprintln(a.Out, theme.Dim.Render("No --operator/--all given; defaulting to detected operators (--operator/--all overrides):"))
	for _, t := range targets {
		fmt.Fprintln(a.Out, "  "+theme.Infof("%-8s -> %s (%s scope)", t.adapter.Operator(), prettyPath(t.adapter.Target(t.scope, env)), t.scope))
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

	form := install.NewForm(huh.NewGroup(
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

	if err := install.NewForm(huh.NewGroup(fields...)).Run(); err != nil {
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
	label := fmt.Sprintf("%s — %s", scope, prettyPath(ad.Target(scope, env)))
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
		return err
	}
	if len(targets) == 0 {
		return nil
	}

	return a.writeSkill(targets, env, opts)
}

// writeSkill renders the canonical skill into each target and prints the
// per-operator outcome plus a closing hint. It is the shared body of
// `skill init` and `bootstrap` so both report writes identically. Targets are
// resolved by the caller (so selection errors surface before any side effects).
func (a *App) writeSkill(targets []skillTarget, env skillgen.DetectEnv, opts *skillOptions) error {
	content, err := a.canonicalContent(opts.baseURL)
	if err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Heading.Render("Jentic skill"))

	var wrote int
	for _, t := range targets {
		out, aerr := skillgen.Apply(t.adapter, content, env, skillgen.ApplyOptions{
			Scope:  t.scope,
			Force:  opts.force,
			DryRun: opts.dryRun,
		})
		if aerr != nil {
			fmt.Fprintln(a.Out, "  "+theme.Warnf("%s: %v", t.adapter.Operator(), aerr))
			continue
		}
		a.reportOutcome(out, t.scope, opts.dryRun)
		if out.Changed && !opts.dryRun {
			wrote++
		}
	}

	if opts.dryRun {
		fmt.Fprintln(a.Out, theme.Dim.Render("Dry run — nothing was written."))
		return nil
	}
	if wrote > 0 {
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Dim.Render("Your agent picks the skill up on its next start. Re-run after a Jentic update to refresh."))
	}
	return nil
}

// reportOutcome prints a single adapter's result line, tagged with the scope
// the write resolved to so placement is always visible in the output.
func (a *App) reportOutcome(out skillgen.Outcome, scope skillgen.Scope, dryRun bool) {
	rel := fmt.Sprintf("%s (%s scope)", prettyPath(out.Path), scope)
	switch {
	case out.UserEdits:
		fmt.Fprintln(a.Out, "  "+theme.Warnf("%-8s %s — manual edits detected; re-run with --force to overwrite", out.Operator, rel))
	case dryRun:
		verb := "would update"
		if out.Created {
			verb = "would create"
		}
		fmt.Fprintln(a.Out, "  "+theme.Infof("%-8s %s %s", out.Operator, verb, rel))
	case out.Skipped:
		fmt.Fprintln(a.Out, "  "+theme.Dimf("%-8s %s — already up to date", out.Operator, rel))
	case out.Created:
		fmt.Fprintln(a.Out, "  "+theme.Successf("%-8s created %s", out.Operator, rel))
	default:
		fmt.Fprintln(a.Out, "  "+theme.Successf("%-8s updated %s", out.Operator, rel))
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

	if jsonOrPretty(cmd, opts.json) {
		return a.skillListJSON(reg, env, detected)
	}
	return a.skillListPretty(reg, env, detected)
}

// skillListPretty renders the human listing. "Detected" (the runtime looks
// present) and "installed" (a managed skill block actually exists on disk)
// are reported separately — #752.
func (a *App) skillListPretty(reg *skillgen.Registry, env skillgen.DetectEnv, detected map[skillgen.Operator]bool) error {
	fmt.Fprintln(a.Out, theme.Heading.Render("Supported operators"))
	for _, ad := range reg.Adapters() {
		glyph := theme.Dim.Render(theme.SelectOff)
		tag := ""
		if detected[ad.Operator()] {
			glyph = theme.Success.Render(theme.SelectOn)
			tag = " " + theme.Dim.Render("(detected)")
		}
		fmt.Fprintln(a.Out, glyph+" "+theme.Accent.Render(string(ad.Operator()))+tag)

		// Print every scope that actually holds a managed block: user and
		// project installs can coexist, and hiding the second would make
		// `skill list` lie by omission (the same conflation #752 fixed).
		var shown int
		for _, st := range skillgen.InstallStates(ad, env) {
			if !st.Installed {
				continue
			}
			line := theme.Field("installed", fmt.Sprintf("%s (%s scope)", prettyPath(st.Path), st.Scope))
			if st.UserEdits {
				line += " " + theme.Warn.Render("(manually edited)")
			}
			fmt.Fprintln(a.Out, "    "+line)
			shown++
		}
		if shown == 0 {
			fmt.Fprintln(a.Out, "    "+theme.Field("installed", "no"))
			fmt.Fprintln(a.Out, "    "+theme.Field("target", prettyPath(ad.Target(ad.DefaultScope(), env))))
		}
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("Install with: jentic skill init [--operator <names>]"))
	return nil
}

func (a *App) skillListJSON(reg *skillgen.Registry, env skillgen.DetectEnv, detected map[skillgen.Operator]bool) error {
	type installRow struct {
		Scope     string `json:"scope"`
		Path      string `json:"path"`
		Installed bool   `json:"installed"`
		UserEdits bool   `json:"user_edits"`
	}
	type row struct {
		Operator string `json:"operator"`
		Detected bool   `json:"detected"`
		// Installed reports whether a managed skill block exists at any scope
		// — detection alone never implies it (#752).
		Installed     bool         `json:"installed"`
		InstalledPath string       `json:"installed_path,omitempty"`
		Target        string       `json:"target"`
		Scope         string       `json:"scope"`
		Installs      []installRow `json:"installs"`
	}
	rows := make([]row, 0, len(reg.Adapters()))
	for _, ad := range reg.Adapters() {
		r := row{
			Operator: string(ad.Operator()),
			Detected: detected[ad.Operator()],
			Target:   ad.Target(ad.DefaultScope(), env),
			Scope:    string(ad.DefaultScope()),
		}
		for _, st := range skillgen.InstallStates(ad, env) {
			r.Installs = append(r.Installs, installRow{
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
		rows = append(rows, r)
	}
	return writeJSON(a.Out, map[string]any{"operators": rows})
}

func (a *App) skillRemove(_ *cobra.Command, opts *skillOptions) error {
	scope, err := resolveScope(opts.scope)
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

	fmt.Fprintln(a.Out, theme.Heading.Render("Remove Jentic skill"))
	var blocked int
	for _, ad := range adapters {
		// No --scope means "remove my install", not "remove at the default
		// placement": probe both scopes and strip every managed block found,
		// so an install made with --scope project (or via the interactive
		// scope prompt) is removable without the user re-deriving its scope.
		scopes := []skillgen.Scope{scope}
		if scope == "" {
			scopes = scopes[:0]
			for _, st := range skillgen.InstallStates(ad, env) {
				if st.Installed {
					scopes = append(scopes, st.Scope)
				}
			}
			if len(scopes) == 0 {
				fmt.Fprintln(a.Out, "  "+theme.Dimf("%-8s nothing to remove (%s)", ad.Operator(), prettyPath(ad.Target(ad.DefaultScope(), env))))
				continue
			}
		}
		for _, sc := range scopes {
			out, rerr := skillgen.Remove(ad, env, skillgen.RemoveOptions{
				Scope:  sc,
				Force:  opts.force,
				DryRun: opts.dryRun,
			})
			switch {
			case rerr != nil:
				fmt.Fprintln(a.Out, "  "+theme.Warnf("%-8s %v", ad.Operator(), rerr))
			case out.UserEdits:
				blocked++
				fmt.Fprintln(a.Out, "  "+theme.Warnf("%-8s %s — manual edits detected; re-run with --force to remove", ad.Operator(), prettyPath(out.Path)))
			case out.Missing:
				fmt.Fprintln(a.Out, "  "+theme.Dimf("%-8s nothing to remove (%s)", ad.Operator(), prettyPath(out.Path)))
			case opts.dryRun:
				fmt.Fprintln(a.Out, "  "+theme.Infof("%-8s would remove from %s", ad.Operator(), prettyPath(out.Path)))
			case out.Removed:
				fmt.Fprintln(a.Out, "  "+theme.Successf("%-8s removed from %s", ad.Operator(), prettyPath(out.Path)))
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
