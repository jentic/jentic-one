package update

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// BrewUpgradeCommand is the user-facing command that updates a Homebrew-managed
// install of the CLIs (both binaries ship in the `jentic` cask).
const BrewUpgradeCommand = "brew upgrade jentic"

// BrewManaged reports whether the binary at target is managed by Homebrew.
//
// Self-updating a brew-managed binary desyncs brew's bookkeeping (`brew
// outdated` keeps reporting the old version and the next `brew upgrade`
// clobbers the swapped binary), so `update` refuses the CLI swap and points at
// BrewUpgradeCommand instead — the policy family flyctl, gh, and friends use:
// hint at brew rather than swapping behind its back.
//
// Two signals mark a brew-managed install:
//
//   - the symlink-resolved path has a Cellar or Caskroom segment (brew keeps
//     the real files there and links them into <prefix>/bin), or
//   - the resolved path sits directly in `$(brew --prefix)/bin` *and* the
//     jentic cask is present in the Caskroom — a regular file there is then a
//     brew link that an older self-update overwrote in place. The cask check
//     keeps a deliberate `JENTIC_INSTALL_DIR=/usr/local/bin` source install on
//     an Intel mac (where /usr/local is the brew prefix but not brew-owned)
//     from being misdetected.
//
// The decision deliberately uses the *resolved* path: a source install linked
// onto PATH by tools/install.sh (e.g. /usr/local/bin/jenticctl ->
// ~/.jentic/bin/jenticctl) resolves outside brew's tree and must not be
// misdetected just because /usr/local happens to be the brew prefix.
func BrewManaged(target string) bool {
	resolved := target
	if r, err := filepath.EvalSymlinks(target); err == nil {
		resolved = r
	}
	// Path-only signal first; it decides without spawning `brew --prefix`.
	if brewManaged(resolved, "", false) {
		return true
	}
	prefix := brewPrefix()
	if prefix == "" {
		return false
	}
	return brewManaged(resolved, prefix, dirExists(filepath.Join(prefix, "Caskroom", "jentic")))
}

// brewManaged is the pure decision core behind BrewManaged: resolved is the
// symlink-resolved binary path, prefix is the (symlink-resolved) brew prefix or
// "" when brew is not installed, and caskInstalled reports whether the jentic
// cask exists under <prefix>/Caskroom.
func brewManaged(resolved, prefix string, caskInstalled bool) bool {
	if hasPathSegment(resolved, "Cellar") || hasPathSegment(resolved, "Caskroom") {
		return true
	}
	return caskInstalled && prefix != "" && filepath.Dir(resolved) == filepath.Join(prefix, "bin")
}

// brewPrefix returns the symlink-resolved `$(brew --prefix)`, or "" when brew
// is not on PATH or cannot report its prefix. Resolving keeps the comparison
// against an EvalSymlinks'd binary path meaningful on setups where the prefix
// itself sits behind a symlink.
func brewPrefix() string {
	brew, err := exec.LookPath("brew")
	if err != nil {
		return ""
	}
	out, err := exec.Command(brew, "--prefix").Output() //nolint:gosec // brew resolved via LookPath; asking it for its prefix is the point.
	if err != nil {
		return ""
	}
	prefix := strings.TrimSpace(string(out))
	if prefix == "" {
		return ""
	}
	if resolved, err := filepath.EvalSymlinks(prefix); err == nil {
		prefix = resolved
	}
	return prefix
}

func dirExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

// hasPathSegment reports whether path contains dir as a whole path segment.
func hasPathSegment(path, dir string) bool {
	for _, seg := range strings.Split(filepath.ToSlash(path), "/") {
		if seg == dir {
			return true
		}
	}
	return false
}
