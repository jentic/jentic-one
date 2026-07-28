package skillgen

import "os"

// InstallState reports whether one adapter target actually holds a managed
// Jentic skill block. "Detected" (the operator looks present) and "installed"
// (the skill file exists with our managed block) are different facts — see
// jentic-one#752, where conflating them made `skill list` claim a skill that
// was never written.
type InstallState struct {
	Scope     Scope  // placement the state was probed at
	Path      string // resolved target for that scope
	Installed bool   // the target exists and contains a managed block
	UserEdits bool   // the managed block's body no longer matches its hash
}

// InstallStates probes every placement scope of one adapter and reports, per
// scope, whether a managed skill block is actually installed there. Read
// errors are treated as "not installed": list is a status probe, not a write
// path, so a permission problem should degrade to a false rather than fail
// the whole listing.
func InstallStates(a Adapter, env DetectEnv) []InstallState {
	states := make([]InstallState, 0, 2)
	seen := map[string]bool{}
	for _, scope := range []Scope{a.DefaultScope(), otherScope(a.DefaultScope())} {
		target := a.Target(scope, env)
		if seen[target] {
			continue
		}
		seen[target] = true
		st := InstallState{Scope: scope, Path: target}
		if data, err := os.ReadFile(target); err == nil { //nolint:gosec // target comes from adapter rules + env, not user input.
			norm := []byte(normalizeNewlines(string(data)))
			if blk := findBlock(norm); blk.found {
				st.Installed = true
				st.UserEdits = blockUserEdited(norm, blk)
			}
		}
		states = append(states, st)
	}
	return states
}

// Installed reports whether any placement scope of the adapter holds a managed
// block, returning the first installed state when so.
func Installed(a Adapter, env DetectEnv) (InstallState, bool) {
	for _, st := range InstallStates(a, env) {
		if st.Installed {
			return st, true
		}
	}
	return InstallState{}, false
}

// otherScope returns the opposite placement scope.
func otherScope(s Scope) Scope {
	if s == ScopeUser {
		return ScopeProject
	}
	return ScopeUser
}
