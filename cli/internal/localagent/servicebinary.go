package localagent

// servicebinary.go owns the ROOT-OWNED copy of the jentic binary that the
// sudo-shim MCP entry and its argv-pinned NOPASSWD sudoers line name. sudo
// runs whatever file sits at the pinned path as the service uid, so that file
// (and every directory above it) must be something only root can replace —
// never the operator's own install (a Homebrew prefix, ~/.local/bin, …),
// which the operator uid can rewrite. `jentic setup`'s isolation step installs
// the copy root-side (refreshing it whenever the operator's binary changed),
// and `jentic reset` removes it along with the service accounts.

import (
	"bytes"
	"crypto/sha256"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// serviceBinDir is the root-owned directory holding the pinned copy. libexec
// is the conventional home for binaries run by other programs rather than
// typed by users, and /usr/local stays writable by root on macOS under SIP.
// The same path is used on Linux (FHS: /usr/local/libexec for locally
// installed helpers).
const serviceBinDir = "/usr/local/libexec/jentic"

// serviceBinName is the file name of the pinned copy inside serviceBinDir.
const serviceBinName = "jentic"

// rootOwner is the chown spec for the pinned copy and any directory the
// install creates: uid 0 and gid 0 — root:wheel on macOS, root:root on Linux.
const rootOwner = "0:0"

// rootUID is the uid every directory on the pinned path must be owned by.
const rootUID = "0"

// ServiceBinaryDir is the root-owned directory holding the pinned copy.
func ServiceBinaryDir() string { return serviceBinDir }

// ServiceBinaryPath is the absolute path of the root-owned jentic copy the
// sudo-shim MCP entry runs and the MCP sudoers rule pins.
func ServiceBinaryPath() string { return serviceBinDir + "/" + serviceBinName }

// InstallServiceBinaryCmd returns the root-side command that installs src (the
// operator's running jentic binary) as the root-owned pinned copy. See
// serviceBinaryInstallScript for the guarantees. Runs as root; src is
// shell-quoted and only ever read. Callers validate src with
// ValidateServiceBinarySource first.
func InstallServiceBinaryCmd(src string) *exec.Cmd {
	script := serviceBinaryInstallScript(src, "/", serviceBinDir, rootOwner, rootUID)
	cmd := exec.Command("sudo", "sh", "-c", script) //nolint:gosec // src is an absolute, validated path, shell-quoted; the destination is a fixed root-owned path.
	cmd.Dir = "/"
	return cmd
}

// RemoveServiceBinaryCmd returns the root-side command that removes the pinned
// copy (a symlink at the path is removed as a link, never followed), any temp
// file an interrupted install left behind, and the managed directory once
// empty. A no-op when nothing is installed. Parent directories are left
// alone: they may predate jentic. Runs as root.
func RemoveServiceBinaryCmd() *exec.Cmd {
	return exec.Command("sudo", "sh", "-c", serviceBinaryRemoveScript(serviceBinDir)) //nolint:gosec // fixed root-owned path, shell-quoted.
}

// serviceBinaryRemoveScript is RemoveServiceBinaryCmd's script, parameterised
// on dir so tests can run it unprivileged against a temp tree.
func serviceBinaryRemoveScript(dir string) string {
	return fixedPATHPrefix + `d=` + shellQuote(dir) + `; ` +
		`if [ -L "$d" ] || [ ! -d "$d" ]; then exit 0; fi; ` +
		`rm -f -- "$d"/` + serviceBinName + ` "$d"/.` + serviceBinName + `.??????; ` +
		`rmdir -- "$d" 2>/dev/null || true`
}

// pathChain returns every directory from top down to and including dir (top
// first). top must be dir or one of its ancestors; production passes "/".
func pathChain(top, dir string) []string {
	top = filepath.Clean(top)
	var chain []string
	for d := filepath.Clean(dir); ; d = filepath.Dir(d) {
		chain = append(chain, d)
		if d == top || d == filepath.Dir(d) {
			break
		}
	}
	for i, j := 0, len(chain)-1; i < j; i, j = i+1, j-1 {
		chain[i], chain[j] = chain[j], chain[i]
	}
	return chain
}

// serviceBinaryInstallScript is InstallServiceBinaryCmd's script. top, owner
// and uid are parameters only so tests can run the real script unprivileged
// against a temp tree; production always passes "/", rootOwner and rootUID.
// In order:
//
//   - src must be a regular file;
//   - every directory from top down to dir must be a real directory (not a
//     symlink) owned by uid and not group- or world-writable. A missing one is
//     created 0755 under owner — safe because its parent already passed the
//     check, so no other uid can race the creation. Anything else fails the
//     install before a byte is written;
//   - an existing destination that is already a regular file owned by uid,
//     not group/world-writable, with identical content is left alone (a
//     re-run with an unchanged binary is a no-op);
//   - otherwise src is copied into a fresh mktemp file inside dir (exclusive
//     create, so never an existing path), chowned to owner, chmod 0755, and
//     renamed onto the destination. A symlink at the destination is removed
//     first (mv(1) resolves a link to a directory and would move into it),
//     and a directory there is an error.
func serviceBinaryInstallScript(src, top, dir, owner, uid string) string {
	var chain strings.Builder
	for _, p := range pathChain(top, dir) {
		chain.WriteString(" " + shellQuote(p))
	}
	return fixedPATHPrefix + `set -e; umask 022; ` +
		`src=` + shellQuote(src) + `; d=` + shellQuote(dir) + `; dest=` + shellQuote(dir+"/"+serviceBinName) + `; ` +
		`owned() { [ -n "$(find "$1" -prune -type "$2" -user ` + uid + ` ! -perm -020 ! -perm -002 2>/dev/null)" ]; }; ` +
		`[ -f "$src" ] || { echo "$src: not a regular file" >&2; exit 1; }; ` +
		`for p in` + chain.String() + `; do ` +
		`if [ ! -e "$p" ] && [ ! -L "$p" ]; then mkdir -m 0755 "$p"; chown ` + owner + ` "$p"; fi; ` +
		`owned "$p" d || { echo "$p: must be a directory owned by root and not writable by group or others" >&2; exit 1; }; ` +
		`done; ` +
		`if [ -L "$dest" ]; then rm -f "$dest"; ` +
		`elif [ -d "$dest" ]; then echo "$dest: is a directory" >&2; exit 1; ` +
		`elif owned "$dest" f && cmp -s "$src" "$dest"; then exit 0; fi; ` +
		`t="$(mktemp "$d/.` + serviceBinName + `.XXXXXX")"; ` +
		`trap 'rm -f "$t"' EXIT; ` +
		`cat < "$src" > "$t"; chown ` + owner + ` "$t"; chmod 0755 "$t"; mv -f "$t" "$dest"; trap - EXIT`
}

// ValidateServiceBinarySource guards the source path handed to
// InstallServiceBinaryCmd: absolute and free of control characters. It is
// only ever read (shell-quoted), never placed on a sudoers line, so no
// further charset constraint applies.
func ValidateServiceBinarySource(src string) error {
	if err := rejectControlChars("jentic binary path", src); err != nil {
		return err
	}
	if !filepath.IsAbs(src) {
		return fmt.Errorf("jentic binary path %q must be absolute", src)
	}
	return nil
}

// ServiceBinaryState reports the pinned copy relative to src: whether a copy
// is installed at all, and whether its content matches src. Read-only and
// unprivileged (the copy is 0755). A symlink or non-regular file at the path
// counts as installed-but-stale, so the next install replaces it. Ownership
// is not checked here — the root-side install enforces it on every run.
func ServiceBinaryState(src string) (installed, current bool) {
	return serviceBinaryState(src, ServiceBinaryPath())
}

// ServiceBinaryInstalled reports whether anything (file or link) occupies the
// pinned copy's path — the teardown survey.
func ServiceBinaryInstalled() bool {
	_, err := os.Lstat(ServiceBinaryPath())
	return err == nil
}

func serviceBinaryState(src, dest string) (installed, current bool) {
	info, err := os.Lstat(dest)
	if err != nil {
		return false, false
	}
	if !info.Mode().IsRegular() {
		return true, false
	}
	a, err := fileDigest(src)
	if err != nil {
		return true, false
	}
	b, err := fileDigest(dest)
	if err != nil {
		return true, false
	}
	return true, bytes.Equal(a, b)
}

// fileDigest is the SHA-256 of path's content.
func fileDigest(path string) ([]byte, error) {
	f, err := os.Open(path) //nolint:gosec // read-only digest of the operator's binary or the pinned copy.
	if err != nil {
		return nil, err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return nil, err
	}
	return h.Sum(nil), nil
}
