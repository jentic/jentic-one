package localagent

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// AgentHasConfig reports whether the agent user already has any of the
// descriptor's config paths in its own home, so the caller only offers to seed
// them once (a re-run won't clobber the agent's evolved config).
func AgentHasConfig(ctx context.Context, agentUser string, desc Descriptor) bool {
	for _, p := range desc.ConfigPaths {
		if runAsAgent(ctx, agentUser, "test -e "+quoteProbePath(p)) == nil {
			return true
		}
	}
	return false
}

// ExistingConfigPaths expands the descriptor's tilde-relative ConfigPaths
// against operatorHome and returns those that actually exist on disk, so the
// caller only offers to copy what's there.
func ExistingConfigPaths(operatorHome string, desc Descriptor) []string {
	var found []string
	for _, p := range desc.ConfigPaths {
		abs := expandTilde(p, operatorHome)
		if abs == "" {
			continue
		}
		if _, err := os.Stat(abs); err == nil {
			found = append(found, abs)
		}
	}
	return found
}

// SafeSeedSources partitions candidate seed sources into those whose real path
// still resolves under the operator's home and those that escape it. It is the
// gate every provider/config copy passes before the tree leaves the operator's
// home: a source is only safe if its fully-resolved path (symlinks followed) is
// the operator home or a descendant. This catches a `~/.aws` that is actually a
// symlink to /etc, and a GOOGLE_APPLICATION_CREDENTIALS that points at an
// arbitrary absolute path outside the home — either of which would otherwise let
// the copy pull a file the operator never meant to hand the agent (and, run as
// root, read something even the operator can't). A source that cannot be resolved
// (broken symlink, vanished race) is treated as unsafe. skipped is returned so the
// caller can warn about exactly what it declined to copy rather than failing the
// whole seed.
func SafeSeedSources(operatorHome string, srcs []string) (safe, skipped []string) {
	realHome, err := filepath.EvalSymlinks(filepath.Clean(operatorHome))
	if err != nil || realHome == "" {
		// Can't establish the boundary — refuse everything rather than guess.
		return nil, srcs
	}
	for _, src := range srcs {
		resolved, err := filepath.EvalSymlinks(src)
		if err != nil || !IsUnderHome(realHome, resolved) {
			skipped = append(skipped, src)
			continue
		}
		safe = append(safe, src)
	}
	return safe, skipped
}

// CopyConfigCmd copies the operator's agent config paths (already expanded to
// absolute paths under the operator's home) into the agent user's home at the
// same tilde-relative location. Only the READ is privileged (it reaches into
// the operator's 700 home): root archives each source with tar and streams it
// into a tar running AS THE AGENT, which extracts it into the agent's home.
//
// Symlink safety: tar archives a symlink as a link (it never dereferences
// without -h), so a link nested in a copied tree lands in the agent's home as
// a link and can never make root copy the contents of whatever it points at
// (e.g. /etc/shadow). The top-level source is already constrained to the
// operator's home by SafeSeedSources. Because the WRITE side runs with the
// agent's own permissions, a symlink or hard link the agent already placed at
// (or above) a destination can't redirect a privileged write, and the
// extracted files are agent-owned without a recursive root chown.
//
// CAUTION: these files may carry provider-specific secrets (e.g. an API key the
// operator saved in the agent's own config). This deliberately hands the agent
// a copy of those; it is the operator's settings the agent is meant to inherit.
//
// The root side is bash with pipefail, not sh: a pipeline's status is
// otherwise the extracting side's, so a source root could not read (or any
// tar -c error) would be reported as a successful, empty or partial copy.
func CopyConfigCmd(agentUser, agentHome, operatorHome string, srcs []string) *exec.Cmd {
	script := copyConfigScript(asAgentPrefix(agentUser), agentHome, operatorHome, srcs)
	cmd := exec.Command("sudo", agentLaunchShell, "--noprofile", "--norc", "-c", script) //nolint:gosec // agentUser/paths are config/descriptor-derived and Go-resolved, shell-quoted.
	// The agent-side tar inherits the working directory; pin it to "/" (the
	// operator's cwd is typically unreadable to the agent, see agentCmd).
	cmd.Dir = "/"
	return cmd
}

// copyConfigScript is CopyConfigCmd's root-side (bash) script; asAgent is as
// for copyBinaryScript.
func copyConfigScript(asAgent, agentHome, operatorHome string, srcs []string) string {
	steps := make([]string, 0, len(srcs))
	for _, src := range srcs {
		rel := strings.TrimPrefix(src, filepath.Clean(operatorHome)+string(filepath.Separator))
		destParent := filepath.Dir(filepath.Join(agentHome, rel))
		steps = append(steps, operatorArchiveCmdline(src)+" | "+asAgent+
			shellQuote(agentLaunchShell)+" --noprofile --norc -c "+shellQuote(agentExtractScript(destParent)))
	}
	return fixedPATHPrefix + "set -o pipefail; " + strings.Join(steps, " && ")
}

// seedTokenCaches lists, per seeded config directory (keyed by its base name),
// the entries that hold the operator's login or session tokens rather than
// configuration. They are never archived, so the agent authenticates as itself:
// AWS keeps its SSO session and assumed-role credentials in sso/cache and
// cli/cache; gcloud keeps its OAuth credentials, access tokens and
// application-default credentials in the files below.
var seedTokenCaches = map[string][]string{
	".aws": {"sso/cache", "cli/cache"},
	"gcloud": {
		"credentials.db",
		"access_tokens.db",
		"legacy_credentials",
		"application_default_credentials.json",
	},
}

// operatorArchiveCmdline is the privileged half of CopyConfigCmd: a tar of src
// (as the single member ./<base>, relative to its parent) written to stdout,
// minus the base's seedTokenCaches entries. COPYFILE_DISABLE keeps macOS tar
// from adding AppleDouble ._ entries. The ./<base>/<entry> exclude patterns
// match the same members under bsdtar (macOS) and GNU tar (Linux).
func operatorArchiveCmdline(src string) string {
	base := filepath.Base(src)
	var excludes strings.Builder
	for _, entry := range seedTokenCaches[base] {
		excludes.WriteString(" --exclude=" + shellQuote("./"+base+"/"+entry))
	}
	return "COPYFILE_DISABLE=1 tar -C " + shellQuote(filepath.Dir(src)) + " -cf -" +
		excludes.String() + " " + shellQuote("./"+base)
}

// agentExtractScript is the agent-side half of CopyConfigCmd: create the
// destination parent and extract the tar on stdin into it.
func agentExtractScript(destParent string) string {
	return fixedPATHPrefix + "umask 022; mkdir -p " + shellQuote(destParent) + " && tar -C " + shellQuote(destParent) + " -xf -"
}

// ExpandedSecretPaths returns the descriptor's SecretConfigPaths expanded against
// agentHome — the discrete credential files that were seeded INTO the agent's
// home and must be scrubbed. Each result is guaranteed to lie under agentHome
// (a tilde-relative descriptor entry that somehow escaped is dropped), so a
// crafted descriptor can never turn the scrub into a delete outside the agent's
// own home.
func ExpandedSecretPaths(agentHome string, desc Descriptor) []string {
	cleanHome := filepath.Clean(agentHome)
	var out []string
	for _, p := range desc.SecretConfigPaths {
		abs := expandTilde(p, agentHome)
		// Must be a STRICT descendant: a descriptor entry of "~" (or one that
		// cleans to the home itself) must never make the scrub target the whole
		// home. IsUnderHome is true for the home itself, so exclude that case here.
		if abs == "" || filepath.Clean(abs) == cleanHome || !IsUnderHome(cleanHome, abs) {
			continue
		}
		out = append(out, abs)
	}
	return out
}

// ScrubSecretsCmd removes the given absolute secret files from the agent's home
// after seeding, so the operator's raw provider keys (Codex's auth.json, Hermes's
// .env) don't come to rest in the agent account: the agent inherits the settings
// but authenticates as itself. It runs as root because the seeded tree is owned
// by the agent and lives under a restricted home. `rm -f` on each exact,
// Go-resolved path — no globs, no recursion — and returns nil when there is
// nothing to scrub so callers can no-op cleanly. Paths are pre-constrained to the
// agent's home by ExpandedSecretPaths.
func ScrubSecretsCmd(paths []string) *exec.Cmd {
	if len(paths) == 0 {
		return nil
	}
	var b strings.Builder
	b.WriteString("rm -f --")
	for _, p := range paths {
		b.WriteString(" " + shellQuote(p))
	}
	return exec.Command("sudo", "sh", "-c", b.String()) //nolint:gosec // paths are descriptor-derived, home-constrained, and shell-quoted.
}

// SeededConfigDirs returns the absolute config/credential paths that config
// seeding could have copied INTO the agent's home — every runnable descriptor's
// ConfigPaths (e.g. ~/.claude, ~/.codex, ~/.hermes) plus the known provider dirs
// (~/.aws, ~/.config/gcloud) — expanded against agentHome and constrained to lie
// under it. `jentic reset` uses this to purge seeded operator credentials from a
// KEPT (re-owned) home, so a live API key the operator handed the agent doesn't
// outlive the account teardown. Deduplicated; order is stable for display.
func SeededConfigDirs(agentHome string) []string {
	cleanHome := filepath.Clean(agentHome)
	seen := map[string]bool{}
	var out []string
	add := func(rel string) {
		abs := expandTilde(rel, agentHome)
		// Strict descendant only: never let a "~" entry (which cleans to the home
		// itself) turn this into `rm -rf $HOME`. IsUnderHome accepts the home root,
		// so exclude it explicitly.
		if abs == "" || filepath.Clean(abs) == cleanHome || !IsUnderHome(cleanHome, abs) || seen[abs] {
			return
		}
		seen[abs] = true
		out = append(out, abs)
	}
	for _, id := range Known() {
		for _, p := range Registry[id].ConfigPaths {
			add(p)
		}
	}
	// Provider config dirs seeded by DetectProvider (Bedrock/Vertex).
	add("~/.aws")
	add("~/.config/gcloud")
	return out
}

// ScrubSeededConfigCmd removes the given absolute seeded-config paths from a kept
// agent home, run as root because the tree may still be agent-owned when this
// runs. Recursive (`rm -rf`) because these are directories, but every path is
// pre-constrained to the agent's home by SeededConfigDirs. Returns nil when there
// is nothing to scrub so the caller can skip the step cleanly.
func ScrubSeededConfigCmd(paths []string) *exec.Cmd {
	if len(paths) == 0 {
		return nil
	}
	// `--` ends option parsing so a path can never be mistaken for an rm flag
	// (belt-and-braces — every path here is an absolute home descendant).
	args := append([]string{"rm", "-rf", "--"}, paths...)
	return exec.Command("sudo", args...) //nolint:gosec // paths are descriptor-derived and home-constrained.
}
