package update

import (
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
// BrewUpgradeCommand instead — the same policy as flyctl, gh, and friends.
//
// Two signals mark a brew-managed install:
//
//   - the symlink-resolved path has a Cellar or Caskroom segment (brew keeps
//     the real files there and links them into <prefix>/bin), or
//   - the resolved path sits directly in `$(brew --prefix)/bin` — a regular
//     file there is brew territory (e.g. a brew link that an older self-update
//     overwrote in place).
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
	return brewManaged(resolved, brewBinDir())
}

// brewManaged is the pure decision core behind BrewManaged: resolved is the
// symlink-resolved binary path and brewBin is `$(brew --prefix)/bin`, or ""
// when brew is not installed.
func brewManaged(resolved, brewBin string) bool {
	if hasPathSegment(resolved, "Cellar") || hasPathSegment(resolved, "Caskroom") {
		return true
	}
	return brewBin != "" && filepath.Dir(resolved) == filepath.Clean(brewBin)
}

// brewBinDir returns `$(brew --prefix)/bin`, or "" when brew is not on PATH or
// cannot report its prefix.
func brewBinDir() string {
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
	return filepath.Join(prefix, "bin")
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
