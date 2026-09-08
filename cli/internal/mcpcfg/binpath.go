package mcpcfg

// binpath.go resolves the ABSOLUTE, STABLE path of the running jentic binary
// for the written entries. GUI runtimes spawn MCP servers with a minimal PATH
// (master plan §3.7.3), so the entry must carry an absolute path — and it must
// be the stable alias (e.g. Homebrew's /opt/homebrew/bin/jentic symlink), not
// the versioned Cellar realpath, or every brew upgrade would strand the entry
// on a deleted directory.

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
)

// stableBinDirs are well-known directories whose entries are stable aliases
// maintained across upgrades (brew relinks the symlink; installers replace
// the file in place). Probed in order when the PATH lookup doesn't already
// resolve to the running binary.
var stableBinDirs = []string{
	"/opt/homebrew/bin",
	"/usr/local/bin",
	"/usr/bin",
}

// StableBinaryPath resolves the running executable's stable absolute path.
func StableBinaryPath() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	home, _ := os.UserHomeDir()
	return stableBinaryPath(exe, home, exec.LookPath, filepath.EvalSymlinks)
}

// stableBinaryPath is the testable core: given the running executable path,
// prefer (in order) the PATH-resolved `jentic`, then a well-known stable bin
// dir entry, when either resolves (symlinks followed) to the same file as the
// executable. Otherwise the executable's own path is returned — already
// absolute, just not aliased.
func stableBinaryPath(exe, home string, lookPath func(string) (string, error), eval func(string) (string, error)) (string, error) {
	if exe == "" {
		return "", errors.New("cannot determine the running executable path")
	}
	realExe := resolveOr(exe, eval)

	sameAsExe := func(candidate string) bool {
		if candidate == "" || !filepath.IsAbs(candidate) {
			return false
		}
		return resolveOr(candidate, eval) == realExe
	}

	if p, err := lookPath("jentic"); err == nil && sameAsExe(p) {
		return p, nil
	}
	candidates := append([]string{}, stableBinDirs...)
	if home != "" {
		candidates = append(candidates, filepath.Join(home, ".local", "bin"))
	}
	for _, dir := range candidates {
		if p := filepath.Join(dir, "jentic"); sameAsExe(p) {
			return p, nil
		}
	}
	return exe, nil
}

// resolveOr fully resolves p's symlinks, falling back to the cleaned input
// when resolution fails (e.g. the candidate doesn't exist).
func resolveOr(p string, eval func(string) (string, error)) string {
	if r, err := eval(p); err == nil {
		return r
	}
	return filepath.Clean(p)
}
