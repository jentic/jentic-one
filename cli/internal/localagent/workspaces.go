package localagent

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
)

// TrustedWorkspaces returns the directories the operator has explicitly TRUSTED in
// the given agent's own config, so setup can offer to grant exactly those to the
// isolated agent instead of making the operator re-grant each project by hand. This
// is deliberately operator-specific — it reads the agent's own record of where the
// operator actually worked, which is a far stronger signal than scanning the home
// for marker files (most of which are dependencies, clones, or templates the
// operator never opened with the agent).
//
// Today only Claude Code is a known launchable agent; it records per-project state
// in ~/.claude.json under a `projects` map keyed by absolute directory path, and we
// take only those with `hasTrustDialogAccepted: true` (the operator answered "yes,
// I trust this folder"). Other operators (hermes, …) plug their own reader in here
// as they are added — the trusted-projects format is per-agent, so this dispatches
// on the descriptor rather than pretending one format is shared. See
// docs/security/same-host/local-agent-isolation.md ("Bringing workspaces over").
//
// The strict access rules always take precedence: every candidate is run through
// Classify and any banned one (HardBan subtree like ~/.ssh/~/.aws, or a SoftBan
// home root) is dropped, as is anything no longer on disk. So a workspace that
// conflicts with the permission model is never surfaced, and the offer can never
// propose something a later grant would reject. Best-effort throughout: a missing
// or unparseable config yields no candidates rather than an error.
func TrustedWorkspaces(operatorHome string, desc Descriptor) []string {
	if operatorHome == "" {
		return nil
	}
	var candidates []string
	switch desc.ID {
	case "claude":
		candidates = readClaudeTrustedProjects(operatorHome)
	default:
		return nil // unknown agent — no trusted-projects source wired up yet
	}

	home := filepath.Clean(operatorHome)
	var out []string
	seen := make(map[string]bool)
	for _, c := range candidates {
		abs, err := filepath.Abs(c)
		if err != nil {
			continue
		}
		abs = filepath.Clean(abs)
		if seen[abs] {
			continue
		}
		// The strict permission model wins: never offer a banned path.
		if Classify(abs, home).Banned() {
			continue
		}
		// Only offer directories that still exist (projects come and go).
		if fi, err := os.Stat(abs); err != nil || !fi.IsDir() {
			continue
		}
		seen[abs] = true
		out = append(out, abs)
	}
	// Collapse nested workspaces: a leaf grant is recursive + inherited, so granting
	// an ancestor already covers every trusted descendant. Offering the child too
	// would be a no-op ACL-wise and just clutter the picker — drop any candidate
	// contained by another in the set, keeping the enclosing one.
	return topLevelWorkspaces(out)
}

// topLevelWorkspaces drops any path contained by another in the set, keeping the
// enclosing directory (whose recursive grant already covers the descendant). Input
// is assumed already cleaned + deduped; output preserves sorted order for a stable
// picker.
func topLevelWorkspaces(dirs []string) []string {
	sorted := append([]string(nil), dirs...)
	sort.Strings(sorted)
	var tops []string
	for _, d := range sorted {
		contained := false
		for _, other := range sorted {
			if other != d && IsUnderHome(other, d) {
				contained = true
				break
			}
		}
		if !contained {
			tops = append(tops, d)
		}
	}
	return tops
}

// readClaudeTrustedProjects reads ~/.claude.json and returns the absolute paths of
// the projects the operator has trusted (hasTrustDialogAccepted). Returns nil when
// the file is absent or malformed — bringing workspaces over is best-effort.
func readClaudeTrustedProjects(operatorHome string) []string {
	data, err := os.ReadFile(filepath.Join(operatorHome, ".claude.json")) //nolint:gosec // operatorHome is the current user's resolved home dir, not user input.
	if err != nil {
		return nil
	}
	var c claudeConfig
	if err := json.Unmarshal(data, &c); err != nil {
		return nil
	}
	var trusted []string
	for path, proj := range c.Projects {
		if proj.HasTrustDialogAccepted {
			trusted = append(trusted, path)
		}
	}
	return trusted
}

// claudeConfig is the subset of ~/.claude.json we read: the per-project map keyed
// by absolute directory path, from which we take the trusted ones.
type claudeConfig struct {
	Projects map[string]claudeProject `json:"projects"`
}

// claudeProject is the subset of a ~/.claude.json project entry we read: whether
// the operator accepted Claude Code's trust dialog for that directory.
type claudeProject struct {
	HasTrustDialogAccepted bool `json:"hasTrustDialogAccepted"`
}
