package localagentcmd

import (
	"fmt"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// SkillUpdateDefault runs SkillUpdate with the default (empty) options. The ctl
// tree calls it to refresh installed skills after a CLI upgrade without needing
// to construct the unexported skillOptions.
func (a *Cmd) SkillUpdateDefault(cmd *cobra.Command) error {
	return a.SkillUpdate(cmd, &skillOptions{})
}

// SkillUpdate re-renders every installed (skill, operator, scope) with the
// currently resolved base URL and rewrites it when the recorded content hash
// differs. It only touches installs that already exist — it never creates a new
// one — so it is a refresh, not an install (#407).
func (a *Cmd) SkillUpdate(cmd *cobra.Command, opts *skillOptions) error {
	results, err := a.applySkillUpdates(opts)
	if err != nil {
		return err
	}
	if wantsJSON(cmd, opts.json) {
		return writeSkillResults(a.Out, results, nil)
	}
	a.reportSkillUpdates(results, opts.dryRun)
	return nil
}

// applySkillUpdates re-renders every installed (operator, skill, scope) and
// returns one result per probed install, printing nothing.
func (a *Cmd) applySkillUpdates(opts *skillOptions) ([]skillResult, error) {
	reg := skillgen.DefaultRegistry()
	env, err := a.detectEnv()
	if err != nil {
		return nil, err
	}

	var adapters []skillgen.Adapter
	if len(opts.operators) > 0 {
		resolved, unknown := reg.ResolveAll(opts.operators)
		if len(unknown) > 0 {
			return nil, fmt.Errorf("unknown operator(s): %s (supported: %s)",
				strings.Join(unknown, ", "), strings.Join(reg.Names(), ", "))
		}
		adapters = resolved
	} else {
		adapters = reg.Adapters()
	}

	set, err := a.canonicalSet(opts.baseURL, opts.skills)
	if err != nil {
		return nil, err
	}

	results := make([]skillResult, 0)
	shared := sharedArtifacts{}
	for _, ad := range adapters {
		for _, c := range set {
			for _, st := range skillgen.InstallStates(ad, c.Name, env) {
				// Check the shared set before the install probe: an earlier
				// operator in this run may have just rewritten this file.
				if r, ok := shared.mirror(string(ad.Operator()), c.Name, st.Path); ok {
					results = append(results, r)
					continue
				}
				if !st.Installed {
					continue
				}
				out, uerr := skillgen.Apply(ad, c, env, skillgen.ApplyOptions{
					Scope:  st.Scope,
					Force:  opts.force,
					DryRun: opts.dryRun,
				})
				// update only touches existing installs, so a write is always
				// an update (never a create) and a no-op is "unchanged".
				action := updateAction(out, uerr)
				r := skillResult{
					Operator:  string(ad.Operator()),
					Skill:     c.Name,
					Scope:     string(st.Scope),
					Path:      out.Path,
					Action:    action,
					DryRun:    opts.dryRun,
					UserEdits: out.UserEdits,
					Error:     errString(uerr),
					err:       uerr,
					applied:   out,
				}
				r.setNewer(out)
				shared.record(r)
				results = append(results, r)
			}
		}
	}
	return results, nil
}

// reportSkillUpdates prints the human summary for a skill update run.
func (a *Cmd) reportSkillUpdates(results []skillResult, dryRun bool) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Update Jentic skills"))
	var changed, blocked, newer int
	for _, r := range results {
		label := fmt.Sprintf("%s/%s", r.Operator, r.Skill)
		out := r.applied
		switch {
		case r.AppliedVia != "":
			a.reportShared(r)
		case r.err != nil:
			fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %v", label, r.err))
		case out.UserEdits:
			blocked++
			fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %s — manual edits detected; re-run with --force to overwrite", label, prettyPath(out.Path)))
		case out.NewerInstalled:
			newer++
			fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %s — %s", label, prettyPath(out.Path), newerInstalledNote(r.InstalledVersion, r.AvailableVersion)))
		case out.Changed && dryRun:
			changed++
			fmt.Fprintln(a.Out, "  "+theme.Infof("%-24s would update %s (%s scope)", label, prettyPath(out.Path), r.Scope))
		case out.Changed:
			changed++
			fmt.Fprintln(a.Out, "  "+theme.Successf("%-24s updated %s (%s scope)", label, prettyPath(out.Path), r.Scope))
		default:
			fmt.Fprintln(a.Out, "  "+theme.Dimf("%-24s %s — already up to date", label, prettyPath(out.Path)))
		}
	}
	if changed == 0 && blocked == 0 && newer == 0 {
		fmt.Fprintln(a.Out, theme.Dim.Render("No installed skills to update."))
	}
	if dryRun {
		fmt.Fprintln(a.Out, theme.Dim.Render("Dry run — nothing was written."))
	}
	if blocked > 0 {
		fmt.Fprintln(a.Out, theme.Dim.Render("Re-run with --force to overwrite content you have edited."))
	}
}
