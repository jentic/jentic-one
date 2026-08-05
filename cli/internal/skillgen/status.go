package skillgen

import "os"

// InstallState reports whether one skill is actually installed at one adapter
// target. "Detected" (the operator looks present) and "installed" (the skill
// artifact exists on disk) are different facts — see jentic-one#752, where
// conflating them made `skill list` claim a skill that was never written.
type InstallState struct {
	Skill     string // the skill name this state is for
	Scope     Scope  // placement the state was probed at
	Path      string // resolved target for that scope
	Installed bool   // the target holds this skill (named block, or owned file + sidecar)
	UserEdits bool   // the installed content no longer matches its recorded hash
}

// InstallStates probes every placement scope of one adapter for one skill and
// reports, per scope, whether that skill is actually installed there. Read
// errors are treated as "not installed": list is a status probe, not a write
// path, so a permission problem should degrade to a false rather than fail the
// whole listing.
//
// Operators can share a target (codex and generic both splice into the project
// AGENTS.md): a skill written via either is reported as installed for both.
// That is deliberate — "installed" describes the artifact on disk, which both
// runtimes genuinely load, not which operator name wrote it.
func InstallStates(a Adapter, name string, env DetectEnv) []InstallState {
	states := make([]InstallState, 0, 2)
	seen := map[string]bool{}
	for _, scope := range []Scope{a.DefaultScope(), otherScope(a.DefaultScope())} {
		target := a.Target(scope, name, env)
		if seen[target] {
			continue
		}
		seen[target] = true
		st := InstallState{Skill: name, Scope: scope, Path: target}
		if data, err := os.ReadFile(target); err == nil { //nolint:gosec // target comes from adapter rules + env, not user input.
			norm := []byte(normalizeNewlines(string(data)))
			st.Installed, st.UserEdits = probeInstall(a, target, norm, name)
		}
		states = append(states, st)
	}
	return states
}

// InstallStatesForSkills probes install state for several skills at once,
// flattening (skill × scope) into one slice.
func InstallStatesForSkills(a Adapter, names []string, env DetectEnv) []InstallState {
	out := make([]InstallState, 0, len(names)*2)
	for _, name := range names {
		out = append(out, InstallStates(a, name, env)...)
	}
	return out
}

// probeInstall reports whether skill name is installed in norm at target and
// whether it has user edits, honoring the adapter's output mode (named block
// for shared files; owned file + sidecar for dedicated ones).
func probeInstall(a Adapter, target string, norm []byte, name string) (installed, userEdits bool) {
	if a.OwnsWholeFile() {
		// The presence of the SKILL.md file (norm was read successfully) means
		// this skill is installed. Edit state comes from the sidecar; a legacy
		// in-file block is our own pre-migration write.
		if sc, ok := readSidecar(target); ok {
			return true, dedicatedFileHash(norm) != sc.BodyHash
		}
		if blk := findMigratableLegacyBlock(norm, name); blk.found {
			return true, blockUserEdited(norm, blk)
		}
		// A SKILL.md with no provenance we can attribute: report installed but
		// (unknown) user content, matching Apply's refuse-without-force stance.
		return true, true
	}

	blk := findBlock(norm, name)
	if !blk.found {
		blk = findMigratableLegacyBlock(norm, name)
	}
	if !blk.found {
		return false, false
	}
	return true, blockUserEdited(norm, blk)
}

// otherScope returns the opposite placement scope.
func otherScope(s Scope) Scope {
	if s == ScopeUser {
		return ScopeProject
	}
	return ScopeUser
}
