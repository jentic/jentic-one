package localagentcmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// NewSkillsCmd builds the `skills` command (list/show/init task skills).
func NewSkillsCmd(app *cmdcore.App) *cobra.Command {
	a := &Cmd{App: app}
	opts := &skillOptions{}
	cmd := &cobra.Command{
		Use:   "skills [name]",
		Short: "List or display embedded task skills",
		Long: "skills lists all embedded task skills (step-by-step guides for common\n" +
			"Jentic platform operations). Pass a skill name to print its full content.\n" +
			"Use \"skills init\" to install them into your agent's native layout.",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 0 {
				return a.skillsList(cmd, opts)
			}
			return a.skillsShow(args[0])
		},
	}
	cmd.Flags().BoolVar(&opts.json, "json", false, "output as JSON array")

	cmd.AddCommand(newSkillsInitCmd(a))
	return cmd
}

func newSkillsInitCmd(a *Cmd) *cobra.Command {
	opts := &skillOptions{}
	cmd := &cobra.Command{
		Use:   "init [name]",
		Short: "Install task skills into your agent's native layout",
		Long: "init writes task-skill markdown files into each detected agent runtime's\n" +
			"native layout. Pass a skill name to install only that one.",
		Example: "  jentic skills init\n" +
			"  jentic skills init register-agent\n" +
			"  jentic skills init --operator claude,cursor\n" +
			"  jentic skills init --all --yes\n" +
			"  jentic skills init --dry-run",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var nameFilter string
			if len(args) > 0 {
				nameFilter = args[0]
			}
			return a.skillsInit(cmd, opts, nameFilter)
		},
	}
	cmd.Flags().StringSliceVar(&opts.operators, "operator", nil, "operators to target (repeatable or comma-separated)")
	cmd.Flags().StringVar(&opts.scope, "scope", "", "placement scope: user or project (default: per-operator)")
	cmd.Flags().BoolVar(&opts.yes, "yes", false, "skip the interactive picker (use --operator/--all)")
	cmd.Flags().BoolVar(&opts.dryRun, "dry-run", false, "print target paths without writing")
	cmd.Flags().BoolVar(&opts.all, "all", false, "target every supported operator")
	return cmd
}

func (a *Cmd) skillsList(cmd *cobra.Command, opts *skillOptions) error {
	skills, err := skillgen.TaskSkills("jentic")
	if err != nil {
		return err
	}

	if cmdcore.JSONOrPretty(cmd, opts.json) {
		type row struct {
			Name        string `json:"name"`
			Version     string `json:"version"`
			Description string `json:"description"`
		}
		rows := make([]row, 0, len(skills))
		for _, ts := range skills {
			rows = append(rows, row{
				Name:        ts.Name,
				Version:     ts.Version,
				Description: ts.Description,
			})
		}
		return cmdcore.WriteJSON(a.Out, rows)
	}

	fmt.Fprintln(a.Out, theme.Heading.Render("Task skills"))
	for _, ts := range skills {
		ver := ts.Version
		if ver == "" {
			ver = "-"
		}
		fmt.Fprintf(a.Out, "  %-24s %-6s %s\n", ts.Name, ver, ts.Description)
	}
	return nil
}

func (a *Cmd) skillsShow(name string) error {
	ts, err := skillgen.TaskSkillByID("jentic", name)
	if err != nil {
		return err
	}
	fmt.Fprint(a.Out, ts.Raw)
	return nil
}

func (a *Cmd) skillsInit(_ *cobra.Command, opts *skillOptions, nameFilter string) error {
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

	skills, err := skillgen.TaskSkills("jentic")
	if err != nil {
		return err
	}
	if nameFilter != "" {
		var filtered []skillgen.TaskSkill
		for _, ts := range skills {
			if ts.TaskID == nameFilter {
				filtered = append(filtered, ts)
				break
			}
		}
		if len(filtered) == 0 {
			return fmt.Errorf("task skill %q not found", nameFilter)
		}
		skills = filtered
	}

	fmt.Fprintln(a.Out, theme.Heading.Render("Task skills"))

	for _, t := range targets {
		wrote, aerr := a.writeTaskSkills(t.adapter, env, t.scope, skills, opts.dryRun)
		if aerr != nil {
			fmt.Fprintln(a.Out, "  "+theme.Warnf("%s: %v", t.adapter.Operator(), aerr))
			continue
		}
		if opts.dryRun {
			fmt.Fprintln(a.Out, "  "+theme.Infof("%-8s would write %d skill(s)", t.adapter.Operator(), wrote))
		} else {
			fmt.Fprintln(a.Out, "  "+theme.Successf("%-8s wrote %d skill(s)", t.adapter.Operator(), wrote))
		}
	}

	if opts.dryRun {
		fmt.Fprintln(a.Out, theme.Dim.Render("Dry run — nothing was written."))
	}
	return nil
}

func (a *Cmd) writeTaskSkills(ad skillgen.Adapter, env skillgen.DetectEnv, scope skillgen.Scope, skills []skillgen.TaskSkill, dryRun bool) (int, error) {
	switch ad.Operator() {
	case skillgen.OpClaude, skillgen.OpCursor:
		return a.writeTaskSkillsDir(ad, env, scope, skills, dryRun)
	case skillgen.OpHermes:
		return a.writeTaskSkillsHermes(env, scope, skills, dryRun)
	case skillgen.OpCodex, skillgen.OpGeneric:
		return a.writeTaskSkillsAgents(ad, env, scope, skills, dryRun)
	default:
		return 0, fmt.Errorf("unsupported operator %s for task skills", ad.Operator())
	}
}

func (a *Cmd) writeTaskSkillsDir(ad skillgen.Adapter, env skillgen.DetectEnv, scope skillgen.Scope, skills []skillgen.TaskSkill, dryRun bool) (int, error) {
	base := env.Home
	if scope == skillgen.ScopeProject {
		base = env.Cwd
	}
	var dir string
	switch ad.Operator() {
	case skillgen.OpClaude:
		dir = filepath.Join(base, ".claude", "skills", "jentic-tasks")
	case skillgen.OpCursor:
		dir = filepath.Join(base, ".cursor", "skills", "jentic-tasks")
	}

	if dryRun {
		return len(skills), nil
	}

	if err := os.MkdirAll(dir, 0o755); err != nil {
		return 0, err
	}
	for _, ts := range skills {
		target := filepath.Join(dir, ts.TaskID+".md")
		if err := os.WriteFile(target, []byte(ts.Raw), 0o644); err != nil {
			return 0, err
		}
	}
	return len(skills), nil
}

func (a *Cmd) writeTaskSkillsHermes(env skillgen.DetectEnv, scope skillgen.Scope, skills []skillgen.TaskSkill, dryRun bool) (int, error) {
	base := env.Home
	if scope == skillgen.ScopeProject {
		base = env.Cwd
	}

	if dryRun {
		return len(skills), nil
	}

	for _, ts := range skills {
		dir := filepath.Join(base, ".hermes", "skills", "jentic-tasks", ts.TaskID)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return 0, err
		}
		target := filepath.Join(dir, "SKILL.md")
		if err := os.WriteFile(target, []byte(ts.Raw), 0o644); err != nil {
			return 0, err
		}
	}
	return len(skills), nil
}

func (a *Cmd) writeTaskSkillsAgents(ad skillgen.Adapter, env skillgen.DetectEnv, scope skillgen.Scope, skills []skillgen.TaskSkill, dryRun bool) (int, error) {
	target := ad.Target(scope, "jentic-tasks", env)

	var b strings.Builder
	b.WriteString("## Task Skills\n\n")
	for _, ts := range skills {
		fmt.Fprintf(&b, "- **%s**: %s\n", ts.Name, ts.Description)
	}

	if dryRun {
		return len(skills), nil
	}

	dir := filepath.Dir(target)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return 0, err
	}

	var existing []byte
	if data, err := os.ReadFile(target); err == nil {
		existing = data
	}

	content := b.String()
	cleaned := removeTaskSkillsSection(string(existing))
	var out string
	if strings.TrimSpace(cleaned) == "" {
		out = content
	} else {
		out = strings.TrimRight(cleaned, "\n") + "\n\n" + content
	}

	if err := os.WriteFile(target, []byte(out), 0o644); err != nil {
		return 0, err
	}
	return len(skills), nil
}

func removeTaskSkillsSection(s string) string {
	idx := strings.Index(s, "## Task Skills")
	if idx < 0 {
		return s
	}
	rest := s[idx+len("## Task Skills"):]
	nextH2 := strings.Index(rest, "\n## ")
	if nextH2 < 0 {
		return s[:idx]
	}
	return s[:idx] + rest[nextH2+1:]
}

// taskSkillsDir returns the directory where task skills are installed for an
// adapter, or empty string if the adapter doesn't use a directory layout.
func taskSkillsDir(ad skillgen.Adapter, env skillgen.DetectEnv, scope skillgen.Scope) string {
	base := env.Home
	if scope == skillgen.ScopeProject {
		base = env.Cwd
	}
	switch ad.Operator() {
	case skillgen.OpClaude:
		return filepath.Join(base, ".claude", "skills", "jentic-tasks")
	case skillgen.OpCursor:
		return filepath.Join(base, ".cursor", "skills", "jentic-tasks")
	case skillgen.OpHermes:
		return filepath.Join(base, ".hermes", "skills", "jentic-tasks")
	default:
		return ""
	}
}
