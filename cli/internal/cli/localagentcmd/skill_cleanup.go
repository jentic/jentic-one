package localagentcmd

import (
	"errors"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// SkillCleanupTarget is one installed skill artifact found on disk, as
// `jenticctl uninstall` lists and removes it. Operators sharing a target
// (codex and generic both splice into the project AGENTS.md) yield one target
// naming all of them.
type SkillCleanupTarget struct {
	Operators []string // every operator that loads this artifact, registry order
	Skill     string
	Scope     skillgen.Scope
	Path      string
	Display   string // Path shortened to ~-relative form against the probed home
	// UserEdits reports a Jentic write whose content no longer matches its
	// recorded hash; it is kept unless removed with --force.
	UserEdits bool
	// Unattributed reports a SKILL.md at a Jentic skill path with no
	// provenance (no sidecar, no managed block): Jentic cannot prove it wrote
	// it, so it is never offered for removal.
	Unattributed bool
}

// Label is the operator/skill tag for output, e.g. "codex+generic/jentic".
func (t SkillCleanupTarget) Label() string {
	return strings.Join(t.Operators, "+") + "/" + t.Skill
}

// Removable reports whether uninstall may remove the target without --force.
func (t SkillCleanupTarget) Removable() bool { return !t.UserEdits && !t.Unattributed }

// SkillCleanupOutcome is the result of removing one target.
type SkillCleanupOutcome struct {
	Target  SkillCleanupTarget
	Removed bool  // the artifact was stripped (or would be, on a dry run)
	Kept    bool  // left in place: edited, unattributed, or edited since the probe
	Err     error // the removal failed; the artifact is still on disk
}

// SkillCleanupTargets probes every supported operator, bundled skill, and
// scope once and returns the installed artifacts, deduplicated by path. It is
// read-only. Project scope resolves against the current working directory.
func (a *Cmd) SkillCleanupTargets() ([]SkillCleanupTarget, error) {
	env, err := a.detectEnv()
	if err != nil {
		return nil, err
	}
	index := map[string]int{}
	var out []SkillCleanupTarget
	for _, ad := range skillgen.DefaultRegistry().Adapters() {
		for _, st := range skillgen.InstallStatesForSkills(ad, skillgen.BundledNames(), env) {
			if !st.Installed {
				continue
			}
			key := st.Skill + "\x00" + st.Path
			if i, ok := index[key]; ok {
				out[i].Operators = append(out[i].Operators, string(ad.Operator()))
				continue
			}
			index[key] = len(out)
			unattributed := !skillgen.Attributed(ad, st.Path, st.Skill)
			out = append(out, SkillCleanupTarget{
				Operators:    []string{string(ad.Operator())},
				Skill:        st.Skill,
				Scope:        st.Scope,
				Path:         st.Path,
				Display:      displayUnder(env.Home, st.Path),
				UserEdits:    st.UserEdits && !unattributed,
				Unattributed: unattributed,
			})
		}
	}
	return out, nil
}

// RemoveSkillCleanupTargets removes each removable target once through
// skillgen.Remove — the engine `jentic skill remove` uses — with the user-edit
// guard on. Edited and unattributed targets are kept, never forced. The
// returned error joins every per-target failure, so a partial removal is
// visible to the caller; outcomes are returned either way.
func (a *Cmd) RemoveSkillCleanupTargets(targets []SkillCleanupTarget, dryRun bool) ([]SkillCleanupOutcome, error) {
	env, err := a.detectEnv()
	if err != nil {
		return nil, err
	}
	reg := skillgen.DefaultRegistry()
	outcomes := make([]SkillCleanupOutcome, 0, len(targets))
	var errs []error
	for _, t := range targets {
		o := SkillCleanupOutcome{Target: t}
		if !t.Removable() {
			o.Kept = true
			outcomes = append(outcomes, o)
			continue
		}
		ad, ok := reg.Resolve(t.Operators[0])
		if !ok {
			o.Err = fmt.Errorf("unknown operator %q", t.Operators[0])
		} else {
			out, rerr := skillgen.Remove(ad, skillgen.Canonical{Name: t.Skill}, env, skillgen.RemoveOptions{
				Scope:  t.Scope,
				DryRun: dryRun,
			})
			switch {
			case rerr != nil:
				o.Err = rerr
			case out.UserEdits:
				o.Kept = true
			default:
				o.Removed = out.Removed || out.Missing
			}
		}
		if o.Err != nil {
			errs = append(errs, fmt.Errorf("%s at %s: %w", t.Label(), t.Display, o.Err))
		}
		outcomes = append(outcomes, o)
	}
	return outcomes, errors.Join(errs...)
}

// displayUnder shortens p to a ~-relative form when it lies under home (the
// probed home, so an injected environment displays consistently).
func displayUnder(home, p string) string {
	if home == "" {
		return p
	}
	if rel, err := filepath.Rel(home, p); err == nil && filepath.IsLocal(rel) {
		return filepath.Join("~", rel)
	}
	return p
}
