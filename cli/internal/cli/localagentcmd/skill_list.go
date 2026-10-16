package localagentcmd

import (
	"fmt"
	"strings"

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

	sharers := artifactOperators(reg, env)
	if cmdcore.JSONOrPretty(cmd, opts.json) {
		return a.skillListJSON(reg, env, detected, sharers)
	}
	return a.skillListPretty(reg, env, detected, sharers)
}

// skillListSchemaVersion pins the `skill list --json` document shape.
const skillListSchemaVersion = "1"

// artifactOperators maps each installed artifact (skill, path) to every
// operator that loads it, so the list can count a shared AGENTS.md block once
// and say which other operators share it.
func artifactOperators(reg *skillgen.Registry, env skillgen.DetectEnv) map[string][]skillgen.Operator {
	out := map[string][]skillgen.Operator{}
	for _, inst := range skillgen.InstalledStates(reg, env) {
		out[skillgen.ArtifactKey(inst.Skill, inst.Path)] = inst.Operators
	}
	return out
}

// sharedWith returns the operators other than self that load the artifact at
// (skill, path), in registry order; nil when it is not shared.
func sharedWith(sharers map[string][]skillgen.Operator, self skillgen.Operator, skill, path string) []string {
	var out []string
	for _, op := range sharers[skillgen.ArtifactKey(skill, path)] {
		if op != self {
			out = append(out, string(op))
		}
	}
	return out
}

// skillListPretty renders the human listing per skill. "Detected" (the runtime
// looks present) and "installed" (a skill artifact actually exists on disk) are
// reported separately — #752 — and each shipped skill is probed individually so
// a partial install is visible.
func (a *Cmd) skillListPretty(reg *skillgen.Registry, env skillgen.DetectEnv, detected map[skillgen.Operator]bool, sharers map[string][]skillgen.Operator) error {
	names := skillgen.BundledNames()
	// Stale artifacts, keyed by (skill, path): a shared AGENTS.md block is
	// listed under every operator that loads it but counted once, matching
	// `jentic doctor`.
	stale := map[string]bool{}
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
				line += freshnessMarker(st)
				if others := sharedWith(sharers, ad.Operator(), st.Skill, st.Path); len(others) > 0 {
					line += " " + theme.Dimf("(shared with %s)", strings.Join(others, ", "))
				}
				if st.Stale() {
					stale[skillgen.ArtifactKey(st.Skill, st.Path)] = true
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
	if len(stale) > 0 {
		fmt.Fprintln(a.Out, theme.Warnf("%d installed skill(s) are older than the bundled copy; refresh with: jentic skill update", len(stale)))
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("Install with: jentic skill init [--operator <names>] [--skill <names>]"))
	return nil
}

// freshnessMarker renders the per-install version suffix for the human list:
// a warning for a stale install, a dim note otherwise.
func freshnessMarker(st skillgen.InstallState) string {
	switch st.Freshness {
	case skillgen.FreshnessStale:
		return " " + theme.Warnf("(stale: v%s installed, v%s available)", st.InstalledVersion, st.AvailableVersion)
	case skillgen.FreshnessNewer:
		return " " + theme.Dimf("(v%s, newer than bundled v%s)", st.InstalledVersion, st.AvailableVersion)
	case skillgen.FreshnessCurrent:
		return " " + theme.Dimf("(v%s)", st.InstalledVersion)
	default:
		return " " + theme.Dim.Render("(version unknown)")
	}
}

func (a *Cmd) skillListJSON(reg *skillgen.Registry, env skillgen.DetectEnv, detected map[skillgen.Operator]bool, sharers map[string][]skillgen.Operator) error {
	// installRow is now per (skill, scope): the multi-skill split adds a
	// `skill` dimension to the row shape (a JSON compat surface — called out in
	// the PR body).
	type installRow struct {
		Skill     string `json:"skill"`
		Scope     string `json:"scope"`
		Path      string `json:"path"`
		Installed bool   `json:"installed"`
		UserEdits bool   `json:"user_edits"`
		// Version fields (#1342). available_version is always the bundled
		// copy's; installed_version and freshness are present only for an
		// installed row, with installed_version "unknown" when the install
		// records no readable stamp.
		Stale            bool   `json:"stale"`
		Freshness        string `json:"freshness,omitempty"`
		InstalledVersion string `json:"installed_version,omitempty"`
		AvailableVersion string `json:"available_version"`
		// SharedWith lists the other operators that load this same
		// artifact (codex and generic share the project AGENTS.md).
		SharedWith []string `json:"shared_with,omitempty"`
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
			Installs: make([]installRow, 0, len(names)*2),
		}
		for _, name := range names {
			for _, st := range skillgen.InstallStates(ad, name, env) {
				ir := installRow{
					Skill:            st.Skill,
					Scope:            string(st.Scope),
					Path:             st.Path,
					Installed:        st.Installed,
					UserEdits:        st.UserEdits,
					Stale:            st.Stale(),
					AvailableVersion: st.AvailableVersion,
				}
				if st.Installed {
					ir.SharedWith = sharedWith(sharers, ad.Operator(), st.Skill, st.Path)
					ir.Freshness = string(st.Freshness)
					ir.InstalledVersion = st.InstalledVersion
					if ir.InstalledVersion == "" {
						ir.InstalledVersion = string(skillgen.FreshnessUnknown)
					}
				}
				r.Installs = append(r.Installs, ir)
				if st.Installed && !r.Installed {
					r.Installed = true
					r.InstalledPath = st.Path
				}
			}
		}
		rows = append(rows, r)
	}
	return cmdcore.WriteJSON(a.Out, struct {
		SchemaVersion string `json:"schema_version"`
		Operators     []row  `json:"operators"`
	}{skillListSchemaVersion, rows})
}
