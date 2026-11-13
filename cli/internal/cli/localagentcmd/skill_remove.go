package localagentcmd

import (
	"errors"
	"fmt"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

func (a *Cmd) skillRemove(cmd *cobra.Command, opts *skillOptions) error {
	results, err := a.applySkillRemovals(opts)
	if err != nil {
		return err
	}
	if wantsJSON(cmd, opts.json) {
		return writeSkillResults(a.Out, results, nil)
	}
	a.reportSkillRemovals(results, opts.dryRun)
	return nil
}

// applySkillRemovals strips each selected skill from the target operators and
// scopes and returns one result per probed scope, printing nothing.
func (a *Cmd) applySkillRemovals(opts *skillOptions) ([]skillResult, error) {
	scope, err := resolveScope(opts.scope)
	if err != nil {
		return nil, err
	}
	skills, err := selectSkills(opts.skills)
	if err != nil {
		return nil, err
	}
	reg := skillgen.DefaultRegistry()
	env, err := a.detectEnv()
	if err != nil {
		return nil, err
	}

	var adapters []skillgen.Adapter
	switch {
	case opts.all && len(opts.operators) > 0:
		return nil, errOperatorAndAll
	case opts.all:
		adapters = reg.Adapters()
	case len(opts.operators) > 0:
		resolved, unknown := reg.ResolveAll(opts.operators)
		if len(unknown) > 0 {
			return nil, fmt.Errorf("unknown operator(s): %s (supported: %s)",
				strings.Join(unknown, ", "), strings.Join(reg.Names(), ", "))
		}
		adapters = resolved
	default:
		return nil, errors.New("no operators given; pass --operator <names> or --all")
	}

	results := make([]skillResult, 0)
	shared := sharedArtifacts{}
	for _, ad := range adapters {
		for _, name := range skills {
			c := skillgen.Canonical{Name: name}
			// No --scope means "remove my install", not "remove at the default
			// placement": probe both scopes and strip every install found, so
			// an install made with --scope project (or via the interactive
			// scope prompt) is removable without the user re-deriving its scope.
			// A scope whose artifact an earlier operator in this run already
			// removed (a shared AGENTS.md) is kept so its outcome is mirrored.
			op := string(ad.Operator())
			scopes := []skillgen.Scope{scope}
			if scope == "" {
				scopes = scopes[:0]
				for _, st := range skillgen.InstallStates(ad, name, env) {
					if _, done := shared.mirror(op, name, st.Path); st.Installed || done {
						scopes = append(scopes, st.Scope)
					}
				}
				if len(scopes) == 0 {
					continue
				}
			}
			for _, sc := range scopes {
				if r, ok := shared.mirror(op, name, ad.Target(sc, name, env)); ok {
					results = append(results, r)
					continue
				}
				out, rerr := skillgen.Remove(ad, c, env, skillgen.RemoveOptions{
					Scope:  sc,
					Force:  opts.force,
					DryRun: opts.dryRun,
				})
				r := skillResult{
					Operator:  op,
					Skill:     name,
					Scope:     string(sc),
					Path:      out.Path,
					Action:    removeAction(out, rerr),
					DryRun:    opts.dryRun,
					UserEdits: out.UserEdits,
					Error:     errString(rerr),
					err:       rerr,
				}
				// Only a result that touched the file is shared; a miss
				// leaves the artifact for a later operator to act on.
				if r.Action != skillActionNotInstalled {
					shared.record(r)
				}
				results = append(results, r)
			}
		}
	}
	return results, nil
}

// reportSkillRemovals prints the human summary for a skill remove run.
func (a *Cmd) reportSkillRemovals(results []skillResult, dryRun bool) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Remove Jentic skills"))
	var blocked int
	for _, r := range results {
		label := fmt.Sprintf("%s/%s", r.Operator, r.Skill)
		if r.AppliedVia != "" {
			a.reportShared(r)
			continue
		}
		switch r.Action {
		case skillActionError:
			fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %v", label, r.err))
		case skillActionUserEdits:
			blocked++
			fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %s — manual edits detected; re-run with --force to remove", label, prettyPath(r.Path)))
		case skillActionNotInstalled:
			// Nothing to remove for this (skill, scope): stay quiet to
			// avoid a wall of "nothing to remove" across the set.
		case skillActionRemoved:
			if dryRun {
				fmt.Fprintln(a.Out, "  "+theme.Infof("%-24s would remove from %s", label, prettyPath(r.Path)))
			} else {
				fmt.Fprintln(a.Out, "  "+theme.Successf("%-24s removed from %s", label, prettyPath(r.Path)))
			}
		}
	}
	if dryRun {
		fmt.Fprintln(a.Out, theme.Dim.Render("Dry run — nothing was removed."))
	}
	if blocked > 0 {
		fmt.Fprintln(a.Out, theme.Dim.Render("Re-run with --force to remove blocks you have edited."))
	}
}
