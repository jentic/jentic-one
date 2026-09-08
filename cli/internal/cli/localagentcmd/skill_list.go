package localagentcmd

import (
	"fmt"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// skill_list.go holds the `jentic skill list` rendering concern extracted from
// skill.go (ARCH-24): resolving what is detected/installed and emitting the
// human and JSON views. skill.go keeps command wiring, target selection, and the
// init/update/remove verbs.

func (a *Cmd) skillList(cmd *cobra.Command, opts *skillOptions) error {
	reg := skillgen.DefaultRegistry()
	env, err := a.detectEnv()
	if err != nil {
		return err
	}
	detected := map[skillgen.Operator]bool{}
	for _, d := range reg.Detected(env) {
		detected[d.Operator()] = true
	}

	if cmdcore.JSONOrPretty(cmd, opts.json) {
		return a.skillListJSON(reg, env, detected)
	}
	return a.skillListPretty(reg, env, detected)
}

// skillListPretty renders the human listing per skill. "Detected" (the runtime
// looks present) and "installed" (a skill artifact actually exists on disk) are
// reported separately — #752 — and each shipped skill is probed individually so
// a partial install is visible.
func (a *Cmd) skillListPretty(reg *skillgen.Registry, env skillgen.DetectEnv, detected map[skillgen.Operator]bool) error {
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

func (a *Cmd) skillListJSON(reg *skillgen.Registry, env skillgen.DetectEnv, detected map[skillgen.Operator]bool) error {
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
	return cmdcore.WriteJSON(a.Out, map[string]any{"operators": rows})
}
