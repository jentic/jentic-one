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
// serviceBinaryInstallScript for the guarantees. src never reaches the root
// side as a path: this process opens it with the operator's own permissions
// and streams the content on the command's stdin (sudo reads any password
// from the terminal, not stdin), so root never opens an operator-controlled
// path — a file swapped for a link to a root-only file after validation can
// only yield what the operator could already read. Callers validate src with
// ValidateServiceBinarySource first.
func InstallServiceBinaryCmd(src string) *exec.Cmd {
	script := serviceBinaryInstallScript("/", serviceBinDir, rootOwner, rootUID)
	cmd := exec.Command("sudo", "sh", "-c", script) //nolint:gosec // fixed script over fixed root-owned paths; the source content arrives on stdin.
	cmd.Dir = "/"
	cmd.Stdin = &sourceReader{path: src}
	return cmd
}

// sourceReader opens path on the first Read (so building the privileged plan
// never touches the file, and nothing is held open unless the step runs) and
// closes it at EOF. A non-regular file is an error. exec copies it into the
// child's stdin pipe; a failed open or read surfaces from Cmd.Run, and the
// script refuses empty input, so a failure never installs a partial copy
// silently.
type sourceReader struct {
	path string
	f    *os.File
	err  error
}

func (r *sourceReader) Read(p []byte) (int, error) {
	if r.err != nil {
		return 0, r.err
	}
	if r.f == nil {
		f, err := os.Open(r.path)
		if err != nil {
			r.err = err
			return 0, err
		}
		info, err := f.Stat()
		if err == nil && !info.Mode().IsRegular() {
			err = fmt.Errorf("%s: not a regular file", r.path)
		}
		if err != nil {
			_ = f.Close()
			r.err = err
			return 0, err
		}
		r.f = f
	}
	n, err := r.f.Read(p)
	if err != nil {
		_ = r.f.Close()
		r.err = err
	}
	return n, err
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

// serviceBinaryInstallScript is InstallServiceBinaryCmd's script; the new
// binary's content arrives on stdin. top, owner and uid are parameters only so
// tests can run the real script unprivileged against a temp tree; production
// always passes "/", rootOwner and rootUID. In order:
//
//   - every directory from top down to dir must be a real directory (not a
//     symlink — find(1) without -H/-L never follows its operand) owned by uid
//     and not group- or world-writable. A missing one is created 0755 under
//     owner — safe because its parent already passed the check, so no other
//     uid can race the creation, and once checked no other uid can alter it
//     before the write. Anything else fails the install before a byte is
//     written;
//   - stdin is written to a fresh mktemp file inside dir (exclusive create, so
//     never an existing path); empty input is an error. The temp file is
//     removed on any failure or signal;
//   - an existing destination that is already a regular file owned by uid,
//     not group/world-writable, with identical content is left alone (a
//     re-run with an unchanged binary is a no-op);
//   - otherwise the temp file is chowned to owner, chmod 0755, and renamed
//     onto the destination (atomic within dir). A symlink at the destination
//     is removed first (mv(1) resolves a link to a directory and would move
//     into it), and a directory there is an error.
func serviceBinaryInstallScript(top, dir, owner, uid string) string {
	var chain strings.Builder
	for _, p := range pathChain(top, dir) {
		chain.WriteString(" " + shellQuote(p))
	}
	return fixedPATHPrefix + `set -e; umask 022; ` +
		`d=` + shellQuote(dir) + `; dest=` + shellQuote(dir+"/"+serviceBinName) + `; ` +
		`owned() { [ -n "$(find "$1" -prune -type "$2" -user ` + uid + ` ! -perm -020 ! -perm -002 2>/dev/null)" ]; }; ` +
		`for p in` + chain.String() + `; do ` +
		`if [ ! -e "$p" ] && [ ! -L "$p" ]; then mkdir -m 0755 "$p"; chown ` + owner + ` "$p"; fi; ` +
		`owned "$p" d || { echo "$p: must be a real directory owned by root and not writable by group or others (see docs/security/same-host/mcp-same-host-hardening.md)" >&2; exit 1; }; ` +
		`done; ` +
		`if [ -d "$dest" ] && [ ! -L "$dest" ]; then echo "$dest: is a directory" >&2; exit 1; fi; ` +
		`t="$(mktemp "$d/.` + serviceBinName + `.XXXXXX")"; ` +
		`trap 'rm -f "$t"' EXIT; trap 'exit 1' HUP INT TERM; ` +
		`cat > "$t"; ` +
		`[ -s "$t" ] || { echo "no jentic binary content received" >&2; exit 1; }; ` +
		`if [ ! -L "$dest" ] && owned "$dest" f && cmp -s "$t" "$dest"; then exit 0; fi; ` +
		`chown ` + owner + ` "$t"; chmod 0755 "$t"; ` +
		`if [ -L "$dest" ]; then rm -f "$dest"; fi; ` +
		`mv -f "$t" "$dest"; trap - EXIT`
}

// ValidateServiceBinarySource guards the source path handed to
// InstallServiceBinaryCmd: absolute and free of control characters. It is
// only ever opened by this process (never passed to a shell or placed on a
// sudoers line), so no further charset constraint applies.
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
