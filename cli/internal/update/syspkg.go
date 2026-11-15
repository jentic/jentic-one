package update

import "path/filepath"

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
// or removal clobbers it. /usr/bin is reserved for the distribution's package
// manager — tools/install.sh installs to ~/.jentic/bin and links into
// /usr/local/bin, Homebrew uses its own prefix — so a binary there came from a
// package.
func SystemPackageManaged(target string) bool {
	return systemPackageManaged(target, SystemPackageDir)
}

// systemPackageManaged is the decision core behind SystemPackageManaged: it
// resolves symlinks in both target and pkgDir (a merged-/usr distro links /bin
// to /usr/bin) and reports whether target sits directly inside pkgDir. A
// missing target keeps its name and has only its directory resolved.
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
	return filepath.Dir(resolved) == pkgDir
}
