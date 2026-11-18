package update

import (
	"bytes"
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// SystemPackageDir is where the .deb / .rpm / .apk packages built from
// cli/.goreleaser.yaml (the nfpms entries, bindir) install both binaries.
const SystemPackageDir = "/usr/bin"

// SystemPackageUpgradeHint is the user-facing instruction for updating a CLI
// that a system package manager owns.
const SystemPackageUpgradeHint = "install the newer .deb / .rpm / .apk from https://github.com/jentic/jentic-one/releases/latest with your package manager"

// SystemPackageManaged reports whether the symlink-resolved binary at target
// was installed by a system package (dpkg, rpm or apk).
//
// Swapping such a binary in place goes behind the package manager's back: the
// file no longer matches the package database, and the next package upgrade
// or removal clobbers it. A binary outside /usr/bin is never package-owned
// here: tools/install.sh installs to ~/.jentic/bin and links into
// /usr/local/bin, Homebrew uses its own prefix. A binary inside /usr/bin is
// confirmed with the package manager (dpkg, rpm or apk, whichever is
// installed): a package that owns the file means managed, a package manager
// that reports no owner means a hand-installed copy (a container COPY, or
// JENTIC_INSTALL_DIR=/usr/bin) that may be updated. When no package manager
// can answer, the directory alone decides, so /usr/bin counts as managed.
func SystemPackageManaged(target string) bool {
	return systemPackageManaged(target, SystemPackageDir)
}

// systemPackageManaged is the decision core behind SystemPackageManaged: it
// resolves symlinks in both target and pkgDir (a merged-/usr distro links /bin
// to /usr/bin), and for a target directly inside pkgDir asks the package
// manager who owns it. A missing target keeps its name and has only its
// directory resolved.
func systemPackageManaged(target, pkgDir string) bool {
	target = filepath.Clean(target)
	resolved, err := filepath.EvalSymlinks(target)
	if err != nil {
		resolved = target
		if dir, derr := filepath.EvalSymlinks(filepath.Dir(target)); derr == nil {
			resolved = filepath.Join(dir, filepath.Base(target))
		}
	}
	pkgDir = filepath.Clean(pkgDir)
	if r, err := filepath.EvalSymlinks(pkgDir); err == nil {
		pkgDir = r
	}
	if filepath.Dir(resolved) != pkgDir {
		return false
	}
	switch packageOwnership(resolved) {
	case ownerNo:
		return false
	default:
		// ownerYes, or ownerUnknown: no package manager could answer.
		return true
	}
}

type ownership int

const (
	ownerUnknown ownership = iota
	ownerYes
	ownerNo
)

// probeTimeout bounds each package-manager query.
const probeTimeout = 2 * time.Second

// packageToolDirs is where the package-manager tools are looked up. It is
// fixed rather than taken from the caller's PATH so a user-controlled PATH
// entry cannot stand in for dpkg, rpm or apk.
const packageToolDirs = "/usr/sbin:/usr/bin:/sbin:/bin"

// packageProbe is one ownership query: the tool and its argv before the path.
type packageProbe struct {
	tool string
	args []string
}

var packageProbes = []packageProbe{
	{"dpkg", []string{"-S", "--"}},
	{"rpm", []string{"-qf", "--"}},
	{"apk", []string{"info", "-W", "--"}},
}

// Seams over the process environment so tests need no real dpkg, rpm or apk.
var (
	// findPackageTool returns the absolute path of a package-manager tool, or
	// "" when it is not installed.
	findPackageTool = func(name string) string {
		for _, dir := range filepath.SplitList(packageToolDirs) {
			p := filepath.Join(dir, name)
			if fi, err := os.Stat(p); err == nil && !fi.IsDir() && fi.Mode()&0o111 != 0 {
				return p
			}
		}
		return ""
	}
	// runPackageTool runs bin with argv (no shell) under a minimal environment
	// and returns its combined output and exit code. A non-nil error means the
	// tool could not be run or timed out; a non-zero exit is not an error.
	runPackageTool = func(ctx context.Context, bin string, argv []string) (string, int, error) {
		ctx, cancel := context.WithTimeout(ctx, probeTimeout)
		defer cancel()
		// bin is resolved from fixed system dirs and argv is passed without a shell.
		cmd := exec.CommandContext(ctx, bin, argv...) //nolint:gosec // G204: see above
		cmd.Env = []string{"PATH=" + packageToolDirs, "LC_ALL=C"}
		var out bytes.Buffer
		cmd.Stdout, cmd.Stderr = &out, &out
		err := cmd.Run()
		var ee *exec.ExitError
		if errors.As(err, &ee) && ctx.Err() == nil {
			return out.String(), ee.ExitCode(), nil
		}
		if err != nil {
			return out.String(), -1, err
		}
		return out.String(), 0, nil
	}
)

// packageOwnership asks each installed package manager whether it owns path.
// Any owner wins; otherwise a manager that says "no owner" wins over silence;
// with no usable answer the result is ownerUnknown.
func packageOwnership(path string) ownership {
	result := ownerUnknown
	for _, p := range packageProbes {
		bin := findPackageTool(p.tool)
		if bin == "" {
			continue
		}
		argv := append(append([]string{}, p.args...), path)
		out, code, err := runPackageTool(context.Background(), bin, argv)
		if err != nil {
			continue
		}
		switch interpretProbe(p.tool, path, out, code) {
		case ownerYes:
			return ownerYes
		case ownerNo:
			result = ownerNo
		}
	}
	return result
}

// interpretProbe reads one tool's answer. Only an unambiguous owner line counts
// as owned and only a tool's own "no owner" wording counts as not owned; any
// other output (database errors, diversions without a match) is unknown.
func interpretProbe(tool, path, out string, code int) ownership {
	switch tool {
	case "dpkg":
		// Owned: a "<package>[:<arch>]: <path>" line for exactly this path.
		// dpkg also prints "diversion by ..." lines, which never match.
		re := regexp.MustCompile(`^[a-z0-9][a-z0-9+.\-]*(:[a-z0-9-]+)?(, [a-z0-9][a-z0-9+.\-]*(:[a-z0-9-]+)?)*: ` + regexp.QuoteMeta(path) + `$`)
		if code == 0 {
			for _, line := range strings.Split(out, "\n") {
				if re.MatchString(strings.TrimSpace(line)) {
					return ownerYes
				}
			}
			return ownerUnknown
		}
		if strings.Contains(out, "no path found matching pattern") {
			return ownerNo
		}
	case "rpm":
		if code == 0 && strings.TrimSpace(out) != "" && !strings.Contains(out, "is not owned by") {
			return ownerYes
		}
		if strings.Contains(out, "is not owned by any package") {
			return ownerNo
		}
	case "apk":
		if code == 0 && strings.Contains(out, " is owned by ") {
			return ownerYes
		}
		if code != 0 && strings.Contains(out, "Could not find owner package") {
			return ownerNo
		}
	}
	return ownerUnknown
}
