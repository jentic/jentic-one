package skillgen

import "os"

// Attributed reports whether the installed skill name at target carries
// provenance proving Jentic wrote it: a named managed block for a shared file
// (AGENTS.md), or a sidecar / legacy managed block for an owned SKILL.md. An
// owned SKILL.md with neither is unattributed — InstallStates still reports it
// installed (with UserEdits, so nothing overwrites it), but it is not ours to
// offer to delete.
func Attributed(a Adapter, target, name string) bool {
	data, err := os.ReadFile(target) //nolint:gosec // target comes from adapter rules + env, not user input.
	if err != nil {
		return false
	}
	norm := []byte(normalizeNewlines(string(data)))
	if a.OwnsWholeFile() {
		if _, ok := readSidecar(target); ok {
			return true
		}
		return findMigratableLegacyBlock(norm, name).found
	}
	return findBlock(norm, name).found || findMigratableLegacyBlock(norm, name).found
}
