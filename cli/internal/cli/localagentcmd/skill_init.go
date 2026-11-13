package localagentcmd

import (
	"errors"
	"fmt"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

func (a *Cmd) skillInit(cmd *cobra.Command, opts *skillOptions) error {
	asJSON := wantsJSON(cmd, opts.json)
	// In JSON mode stdout carries exactly one document, so the human side
	// reports (defaulted-target echo, picker notices, MCP registration) go to
	// stderr instead. The pickers themselves already draw on stderr, so
	// --json alone changes only the output shape: a human on a terminal is
	// still asked. Machine mode never prompts, so there a run with no
	// --operator/--all takes the same detected-operators default as --yes.
	human := a
	if asJSON {
		human = a.withOut(a.Err)
	}
	if isMachine(cmd) {
		scripted := *opts
		scripted.yes = true
		opts = &scripted
	}

	reg := skillgen.DefaultRegistry()
	env, err := a.detectEnv()
	if err != nil {
		return err
	}

	targets, err := human.chooseTargets(reg, env, opts)
	if err != nil {
		// Esc/Ctrl-C in a picker is "never mind", not a failure — same
		// Cancelled./exit-0 idiom as every other wizard in the CLI (and the
		// same outcome as confirming an empty selection).
		if errors.Is(err, huh.ErrUserAborted) {
			fmt.Fprintln(human.Out, theme.Dim.Render("Cancelled."))
			if asJSON {
				return writeSkillResults(a.Out, nil, emptyMCPReport())
			}
			return nil
		}
		return err
	}
	if len(targets) == 0 {
		if asJSON {
			return writeSkillResults(a.Out, nil, emptyMCPReport())
		}
		return nil
	}

	if asJSON {
		results, err := a.applySkills(targets, env, opts)
		if err != nil {
			return err
		}
		mcp := human.registerMCPEntries(cmd.Context(), targets, opts.dryRun)
		return writeSkillResults(a.Out, results, mcp.report())
	}
	if err := a.writeSkill(targets, env, opts); err != nil {
		return err
	}
	// 2-E3: skills tell the agent *how* to use Jentic; the MCP entry is what
	// lets it *reach* Jentic. Registering both from one command keeps a fresh
	// runtime a single `jentic skill init` away from working. Best-effort —
	// registration problems never fail the skill install that succeeded.
	a.registerMCPEntries(cmd.Context(), targets, opts.dryRun)
	return nil
}

// applySkills renders each selected skill into each target and returns one
// result per (operator, skill, scope), printing nothing. A per-target failure
// is recorded as an "error" result and the rest continue; only a failure to
// load the skill set itself is returned.
//
// For a shared AGENTS.md, each skill's named block is spliced in turn: Apply
// re-reads the file between skills, so sibling blocks written earlier in the
// loop are preserved and never clobbered.
func (a *Cmd) applySkills(targets []skillTarget, env skillgen.DetectEnv, opts *skillOptions) ([]skillResult, error) {
	set, err := a.canonicalSet(opts.baseURL, opts.skills)
	if err != nil {
		return nil, err
	}
	results := make([]skillResult, 0, len(targets)*len(set))
	shared := sharedArtifacts{}
	for _, t := range targets {
		for _, c := range set {
			op := string(t.adapter.Operator())
			if r, ok := shared.mirror(op, c.Name, t.adapter.Target(t.scope, c.Name, env)); ok {
				results = append(results, r)
				continue
			}
			out, aerr := skillgen.Apply(t.adapter, c, env, skillgen.ApplyOptions{
				Scope:  t.scope,
				Force:  opts.force,
				DryRun: opts.dryRun,
			})
			r := skillResult{
				Operator:  op,
				Skill:     c.Name,
				Scope:     string(t.scope),
				Path:      out.Path,
				Action:    applyAction(out, aerr),
				DryRun:    opts.dryRun,
				UserEdits: out.UserEdits,
				Error:     errString(aerr),
				err:       aerr,
				applied:   out,
			}
			r.setNewer(out)
			shared.record(r)
			results = append(results, r)
		}
	}
	return results, nil
}

// writeSkill renders each selected skill into each target and prints the
// per-operator, per-skill outcome plus a closing hint. It is the shared body of
// `skill init` and `setup` so both report writes identically. Targets are
// resolved by the caller (so selection errors surface before any side effects).
func (a *Cmd) writeSkill(targets []skillTarget, env skillgen.DetectEnv, opts *skillOptions) error {
	results, err := a.applySkills(targets, env, opts)
	if err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Heading.Render("Jentic skills"))

	var wrote int
	for _, r := range results {
		if r.AppliedVia != "" {
			a.reportShared(r)
			continue
		}
		if r.err != nil {
			fmt.Fprintln(a.Out, "  "+theme.Warnf("%s/%s: %v", r.Operator, r.Skill, r.err))
			continue
		}
		a.reportOutcome(r.applied, skillgen.Scope(r.Scope), opts.dryRun)
		if r.applied.Changed && !opts.dryRun {
			wrote++
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

// reportShared prints the line for an operator whose artifact was already
// handled by another operator's row in this run (a shared AGENTS.md).
func (a *Cmd) reportShared(r skillResult) {
	label := fmt.Sprintf("%s/%s", r.Operator, r.Skill)
	fmt.Fprintln(a.Out, "  "+theme.Dimf("%-24s %s — same file as %s/%s (%s)", label, prettyPath(r.Path), r.AppliedVia, r.Skill, r.Action))
}

// reportOutcome prints a single (operator, skill) result line, tagged with the
// scope the write resolved to so placement is always visible in the output.
func (a *Cmd) reportOutcome(out skillgen.Outcome, scope skillgen.Scope, dryRun bool) {
	label := fmt.Sprintf("%s/%s", out.Operator, out.Skill)
	rel := fmt.Sprintf("%s (%s scope)", prettyPath(out.Path), scope)
	switch {
	case out.UserEdits:
		fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %s — manual edits detected; re-run with --force to overwrite", label, rel))
	case out.NewerInstalled:
		fmt.Fprintln(a.Out, "  "+theme.Warnf("%-24s %s — %s", label, rel, newerInstalledNote(out.InstalledVersion, out.AppliedVersion)))
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
