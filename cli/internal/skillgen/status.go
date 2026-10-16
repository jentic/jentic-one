package skillgen

import (
	"os"
	"strconv"
	"strings"
)

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
	// InstalledVersion is the content version the install records (sidecar,
	// SKILL.md frontmatter, or the AGENTS.md block's version token); "" when
	// the install carries no readable stamp.
	InstalledVersion string
	// AvailableVersion is the bundled copy's content version.
	AvailableVersion string
	// Freshness compares InstalledVersion with AvailableVersion (#1342); it
	// is only meaningful when Installed.
	Freshness Freshness
}

// Freshness is how an installed skill's recorded version compares with the
// bundled copy's.
type Freshness string

// Freshness values. Unknown covers a missing or unparseable version on either
// side: a status probe reports it rather than failing.
const (
	FreshnessCurrent Freshness = "current" // installed version == bundled version
	FreshnessStale   Freshness = "stale"   // installed version < bundled version
	FreshnessNewer   Freshness = "newer"   // installed version > bundled version (written by a newer CLI)
	FreshnessUnknown Freshness = "unknown" // no comparable version on one side
)

// Stale reports whether the install is older than the bundled copy.
func (s InstallState) Stale() bool {
	return s.Installed && s.Freshness == FreshnessStale
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
	available := BundledVersion(name)
	states := make([]InstallState, 0, 2)
	seen := map[string]bool{}
	for _, scope := range []Scope{a.DefaultScope(), otherScope(a.DefaultScope())} {
		target := a.Target(scope, name, env)
		if seen[target] {
			continue
		}
		seen[target] = true
		st := InstallState{Skill: name, Scope: scope, Path: target, AvailableVersion: available}
		if data, err := os.ReadFile(target); err == nil { //nolint:gosec // target comes from adapter rules + env, not user input.
			norm := []byte(normalizeNewlines(string(data)))
			st.Installed, st.UserEdits, st.InstalledVersion = probeInstall(a, target, norm, name)
		}
		if st.Installed {
			st.Freshness = CompareVersions(st.InstalledVersion, available)
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

// InstalledStates probes every adapter in reg for every bundled skill and
// returns only the installed states, one per distinct (skill, path): codex and
// generic share the project AGENTS.md, so a shared artifact is reported once.
// Order follows the registry, then BundledNames, then scope.
func InstalledStates(reg *Registry, env DetectEnv) []InstallState {
	names := BundledNames()
	seen := map[string]bool{}
	var out []InstallState
	for _, a := range reg.Adapters() {
		for _, st := range InstallStatesForSkills(a, names, env) {
			key := st.Skill + "\x00" + st.Path
			if !st.Installed || seen[key] {
				continue
			}
			seen[key] = true
			out = append(out, st)
		}
	}
	return out
}

// probeInstall reports whether skill name is installed in norm at target,
// whether it has user edits, and the content version it records, honoring the
// adapter's output mode (named block for shared files; owned file + sidecar
// for dedicated ones).
func probeInstall(a Adapter, target string, norm []byte, name string) (installed, userEdits bool, version string) {
	if a.OwnsWholeFile() {
		// The presence of the SKILL.md file (norm was read successfully) means
		// this skill is installed. Edit state comes from the sidecar; a legacy
		// in-file block is our own pre-migration write.
		_, fm := splitFrontmatter(string(norm))
		version = versionToken(fm["version"])
		if sc, ok := readSidecar(target); ok {
			if v := versionToken(sc.Version); v != "" {
				version = v
			}
			return true, dedicatedFileHash(norm) != sc.BodyHash, version
		}
		if blk := findMigratableLegacyBlock(norm, name); blk.found {
			return true, blockUserEdited(norm, blk), version
		}
		// A SKILL.md with no provenance we can attribute: report installed but
		// (unknown) user content, matching Apply's refuse-without-force stance.
		return true, true, version
	}

	blk := findBlock(norm, name)
	if !blk.found {
		blk = findMigratableLegacyBlock(norm, name)
	}
	if !blk.found {
		return false, false, ""
	}
	return true, blockUserEdited(norm, blk), versionToken(blk.version)
}

// BundledVersion returns the content version of the bundled skill name ("1"
// when its frontmatter carries no stamp, matching parseSkill), or "" when the
// name is not bundled.
func BundledVersion(name string) string {
	raw, err := RawBundled(name)
	if err != nil {
		return ""
	}
	return versionToken(ParseDocMeta(raw).Version)
}

// CompareVersions classifies an installed content version against the
// available one. Versions are dot-separated non-negative integers (`6`,
// `1.2`), optionally prefixed with `v`; missing trailing components count as
// zero. Anything else — or an empty side — is FreshnessUnknown.
func CompareVersions(installed, available string) Freshness {
	iv, ok := parseVersion(installed)
	if !ok {
		return FreshnessUnknown
	}
	av, ok := parseVersion(available)
	if !ok {
		return FreshnessUnknown
	}
	for i := range max(len(iv), len(av)) {
		var x, y int
		if i < len(iv) {
			x = iv[i]
		}
		if i < len(av) {
			y = av[i]
		}
		switch {
		case x < y:
			return FreshnessStale
		case x > y:
			return FreshnessNewer
		}
	}
	return FreshnessCurrent
}

// parseVersion splits a content version into its numeric components.
func parseVersion(v string) ([]int, bool) {
	v = strings.TrimPrefix(strings.TrimSpace(v), "v")
	if v == "" {
		return nil, false
	}
	parts := strings.Split(v, ".")
	out := make([]int, 0, len(parts))
	for _, p := range parts {
		n, err := strconv.Atoi(p)
		if err != nil || n < 0 || strings.HasPrefix(p, "+") {
			return nil, false
		}
		out = append(out, n)
	}
	return out, true
}

// versionToken normalizes a content version for recording in a sidecar or a
// managed-block BEGIN line: trimmed, and "" unless it is a short run of
// [A-Za-z0-9._-] — so a hostile or malformed value can never inject text into
// the marker line findBlock parses.
func versionToken(v string) string {
	v = strings.Trim(strings.TrimSpace(v), `"'`)
	if v == "" || len(v) > 32 {
		return ""
	}
	for _, r := range v {
		if (r < 'a' || r > 'z') && (r < 'A' || r > 'Z') && (r < '0' || r > '9') && r != '.' && r != '-' && r != '_' {
			return ""
		}
	}
	return v
}

// otherScope returns the opposite placement scope.
func otherScope(s Scope) Scope {
	if s == ScopeUser {
		return ScopeProject
	}
	return ScopeUser
}
