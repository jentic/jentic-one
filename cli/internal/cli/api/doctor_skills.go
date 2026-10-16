package api

import (
	"fmt"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/localagentcmd"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// checkSkills adds one aggregate "Skills" row comparing every installed
// skill's recorded content version with the bundled copy (#1342). It is
// read-only like the rest of doctor and never refreshes anything: a stale
// install is a warning (exit 0) whose hint is the explicit refresh command.
// With nothing installed there is nothing to compare and no row is added;
// installs that carry no readable version are counted as unknown rather
// than treated as a failure.
func (d *agentDoctor) checkSkills() {
	const section = "Skills"
	states, err := localagentcmd.New(d.app.App).InstalledSkills()
	if err != nil {
		d.add(section, "freshness", agentWarn, "cannot probe installed skills: "+err.Error(), "")
		return
	}
	if len(states) == 0 {
		return
	}
	status, detail, hint := summarizeSkillFreshness(states)
	d.add(section, "freshness", status, detail, hint)
}

// summarizeSkillFreshness folds installed skill artifacts into the doctor
// row's status, detail, and hint. Each artifact counts once, however many
// operators share it, and a stale entry names the operators that load it.
func summarizeSkillFreshness(states []skillgen.InstalledSkill) (agentCheckStatus, string, string) {
	var stale []string
	var unknown int
	for _, st := range states {
		switch st.Freshness {
		case skillgen.FreshnessStale:
			stale = append(stale, fmt.Sprintf("%s v%s→v%s (%s)",
				st.Skill, st.InstalledVersion, st.AvailableVersion, joinOperators(st.Operators)))
		case skillgen.FreshnessUnknown:
			unknown++
		}
	}
	suffix := ""
	if unknown > 0 {
		suffix = fmt.Sprintf(" (%d with unknown version)", unknown)
	}
	if len(stale) > 0 {
		return agentWarn,
			fmt.Sprintf("%d of %d installed skill(s) older than the bundled copy: %s%s",
				len(stale), len(states), strings.Join(stale, "; "), suffix),
			"refresh with `jentic skill update` (see `jentic skill list` for paths)"
	}
	return agentPass, fmt.Sprintf("%d installed skill(s) up to date%s", len(states), suffix), ""
}

// joinOperators renders an artifact's operators as "codex, generic".
func joinOperators(ops []skillgen.Operator) string {
	names := make([]string, len(ops))
	for i, op := range ops {
		names[i] = string(op)
	}
	return strings.Join(names, ", ")
}
