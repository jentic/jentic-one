// The root-owned service-binary scripts are POSIX sh run through sudo —
// Unix-only, like the sudo-shim isolation they serve.
//
//go:build !windows

package localagent

import (
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"
)

// --- Root-owned MCP service binary (servicebinary.go) -----------------------
// Shape tests assert the privileged recipe without sudo; the script tests run
// the REAL install/remove scripts unprivileged against a temp tree, with the
// test's own uid standing in for root (production pins uid/gid 0).

func TestServiceBinaryPathIsRootManaged(t *testing.T) {
	if got := ServiceBinaryPath(); got != "/usr/local/libexec/jentic/jentic" {
		t.Fatalf("ServiceBinaryPath = %q", got)
	}
	if filepath.Dir(ServiceBinaryPath()) != ServiceBinaryDir() {
		t.Fatalf("pinned copy %q must live directly in %q", ServiceBinaryPath(), ServiceBinaryDir())
	}
	// The pinned path lands verbatim on the sudoers line.
	if err := ValidateMcpSudoersInputs(ServiceBinaryPath(), "cursor"); err != nil {
		t.Fatalf("pinned path must pass the sudoers-input guard: %v", err)
	}
}

func TestPathChain(t *testing.T) {
	got := pathChain("/", "/usr/local/libexec/jentic")
	want := []string{"/", "/usr", "/usr/local", "/usr/local/libexec", "/usr/local/libexec/jentic"}
	if !slices.Equal(got, want) {
		t.Fatalf("pathChain = %v, want %v", got, want)
	}
	if got := pathChain("/a/b", "/a/b/c/d"); !slices.Equal(got, []string{"/a/b", "/a/b/c", "/a/b/c/d"}) {
		t.Fatalf("pathChain with a top = %v", got)
	}
}

// TestInstallServiceBinaryCmdShape pins the production recipe: root-side,
// every directory from / down checked, uid/gid 0 ownership, 0755, a mktemp
// file in the managed dir renamed onto the destination.
func TestInstallServiceBinaryCmdShape(t *testing.T) {
	src := "/opt/homebrew/Cellar/jentic/1.0.0/bin/jentic"
	cmd := InstallServiceBinaryCmd(src)
	if len(cmd.Args) != 4 || cmd.Args[0] != "sudo" || cmd.Args[1] != "sh" || cmd.Args[2] != "-c" {
		t.Fatalf("expected sudo sh -c <script>, got %v", cmd.Args)
	}
	if cmd.Dir != "/" {
		t.Errorf("install must run from /, got %q", cmd.Dir)
	}
	// The source is streamed from this process, never named on the root side.
	if r, ok := cmd.Stdin.(*sourceReader); !ok || r.path != src {
		t.Fatalf("install must read the source on stdin via sourceReader(%q), got %#v", src, cmd.Stdin)
	}
	script := cmd.Args[3]
	if strings.Contains(script, src) {
		t.Errorf("the root-side script must never name the operator's source path:\n%s", script)
	}
	for _, needle := range []string{
		`cat > "$t"`,
		`[ -s "$t" ]`,
		`trap 'exit 1' HUP INT TERM`,
		"for p in '/' '/usr' '/usr/local' '/usr/local/libexec' '/usr/local/libexec/jentic'; do",
		"-user 0 ! -perm -020 ! -perm -002",
		`mktemp "$d/.jentic.XXXXXX"`,
		`chown 0:0 "$t"`,
		`chmod 0755 "$t"`,
		`mv -f "$t" "$dest"`,
		`if [ -L "$dest" ]; then rm -f "$dest"`,
		"PATH=" + agentSystemPATH,
	} {
		if !strings.Contains(script, needle) {
			t.Errorf("install script missing %q:\n%s", needle, script)
		}
	}
	// Never write through the destination or copy with a link-following tool.
	for _, forbidden := range []string{"cp ", "install ", "ln ", "chown -R", "chmod -R"} {
		if strings.Contains(script, forbidden) {
			t.Errorf("install script must not use %q:\n%s", forbidden, script)
		}
	}
}

func TestRemoveServiceBinaryCmdShape(t *testing.T) {
	cmd := RemoveServiceBinaryCmd()
	if cmd.Args[0] != "sudo" || cmd.Args[1] != "sh" {
		t.Fatalf("expected sudo sh -c, got %v", cmd.Args)
	}
	script := cmd.Args[3]
	if !strings.Contains(script, "d='/usr/local/libexec/jentic'") {
		t.Errorf("remove script must target the managed dir:\n%s", script)
	}
	if strings.Contains(script, "rm -rf") || strings.Contains(script, "/usr/local/libexec'") {
		t.Errorf("remove script must only remove the copy and the managed dir, never recursively or above it:\n%s", script)
	}
}

// serviceBinTree builds an owner-controlled top dir (0755) and a source
// binary, returning top, the managed dir under it, and src.
func serviceBinTree(t *testing.T) (top, dir, src string) {
	t.Helper()
	top = filepath.Join(t.TempDir(), "root")
	if err := os.Mkdir(top, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(top, 0o755); err != nil {
		t.Fatal(err)
	}
	src = filepath.Join(t.TempDir(), "jentic-src")
	if err := os.WriteFile(src, []byte("binary-v1"), 0o755); err != nil {
		t.Fatal(err)
	}
	return top, filepath.Join(top, "libexec", "jentic"), src
}

// runServiceBinInstall runs the real install script as the current user,
// with that user standing in for root, feeding src through the same
// sourceReader production uses.
func runServiceBinInstall(t *testing.T, src, top, dir string) (string, error) {
	t.Helper()
	uid, gid := strconv.Itoa(os.Getuid()), strconv.Itoa(os.Getgid())
	cmd := exec.Command("sh", "-c", serviceBinaryInstallScript(top, dir, uid+":"+gid, uid))
	cmd.Stdin = &sourceReader{path: src}
	out, err := cmd.CombinedOutput()
	return string(out), err
}

func inode(t *testing.T, p string) uint64 {
	t.Helper()
	info, err := os.Lstat(p)
	if err != nil {
		t.Fatal(err)
	}
	return info.Sys().(*syscall.Stat_t).Ino //nolint:forcetypeassert // Unix-only test.
}

func TestServiceBinaryInstallScriptInstallsRefreshesAndIsIdempotent(t *testing.T) {
	top, dir, src := serviceBinTree(t)
	dest := filepath.Join(dir, "jentic")

	if out, err := runServiceBinInstall(t, src, top, dir); err != nil {
		t.Fatalf("install failed: %v\n%s", err, out)
	}
	for _, d := range []string{filepath.Dir(dir), dir} {
		info, err := os.Lstat(d)
		if err != nil || !info.IsDir() || info.Mode().Perm() != 0o755 {
			t.Fatalf("created dir %s = %v (%v), want a 0755 directory", d, info.Mode(), err)
		}
	}
	info, err := os.Lstat(dest)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != 0o755 {
		t.Fatalf("installed copy = %v (%v), want a regular 0755 file", info.Mode(), err)
	}
	if data, _ := os.ReadFile(dest); string(data) != "binary-v1" {
		t.Fatalf("installed copy content = %q", data)
	}
	if installed, current := serviceBinaryState(src, dest); !installed || !current {
		t.Fatalf("state after install = (%v, %v), want (true, true)", installed, current)
	}

	// Re-run with an unchanged binary: a no-op (same file, not rewritten).
	before := inode(t, dest)
	if out, err := runServiceBinInstall(t, src, top, dir); err != nil {
		t.Fatalf("re-install failed: %v\n%s", err, out)
	}
	if inode(t, dest) != before {
		t.Error("re-run with an unchanged binary must leave the copy alone")
	}

	// Upgrade: the operator's binary changed → the copy is replaced by rename.
	if err := os.WriteFile(src, []byte("binary-v2"), 0o755); err != nil {
		t.Fatal(err)
	}
	if installed, current := serviceBinaryState(src, dest); !installed || current {
		t.Fatalf("state after upgrade = (%v, %v), want (true, false)", installed, current)
	}
	if out, err := runServiceBinInstall(t, src, top, dir); err != nil {
		t.Fatalf("refresh failed: %v\n%s", err, out)
	}
	if data, _ := os.ReadFile(dest); string(data) != "binary-v2" {
		t.Fatalf("refreshed copy content = %q", data)
	}
	if inode(t, dest) == before {
		t.Error("refresh must rename a new file into place, not rewrite the old one")
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Errorf("managed dir must hold only the copy (no temp leftovers), got %v", entries)
	}
}

// TestServiceBinaryInstallScriptReplacesLink: a symlink at the destination is
// replaced, never written through — its target stays untouched.
func TestServiceBinaryInstallScriptReplacesLink(t *testing.T) {
	top, dir, src := serviceBinTree(t)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	other := filepath.Join(t.TempDir(), "other")
	if err := os.WriteFile(other, []byte("original"), 0o600); err != nil {
		t.Fatal(err)
	}
	dest := filepath.Join(dir, "jentic")
	if err := os.Symlink(other, dest); err != nil {
		t.Fatal(err)
	}
	if installed, current := serviceBinaryState(src, dest); !installed || current {
		t.Fatalf("a link at the path must read as installed-but-stale, got (%v, %v)", installed, current)
	}
	if out, err := runServiceBinInstall(t, src, top, dir); err != nil {
		t.Fatalf("install failed: %v\n%s", err, out)
	}
	if data, _ := os.ReadFile(other); string(data) != "original" {
		t.Fatalf("link target was written through: %q", data)
	}
	info, err := os.Lstat(dest)
	if err != nil || !info.Mode().IsRegular() {
		t.Fatalf("destination must now be a regular file, got %v (%v)", info.Mode(), err)
	}
}

// TestServiceBinaryInstallScriptRefusesUnsafeTree: anything on the path that
// another uid could write through fails the install before a byte is written.
func TestServiceBinaryInstallScriptRefusesUnsafeTree(t *testing.T) {
	t.Run("group-writable ancestor", func(t *testing.T) {
		top, dir, src := serviceBinTree(t)
		libexec := filepath.Dir(dir)
		if err := os.Mkdir(libexec, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(libexec, 0o775); err != nil {
			t.Fatal(err)
		}
		if out, err := runServiceBinInstall(t, src, top, dir); err == nil {
			t.Fatalf("install must refuse a group-writable ancestor:\n%s", out)
		}
		if _, err := os.Lstat(dir); !os.IsNotExist(err) {
			t.Errorf("nothing may be created under an unsafe ancestor (%v)", err)
		}
	})
	t.Run("world-writable managed dir", func(t *testing.T) {
		top, dir, src := serviceBinTree(t)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(dir, 0o757); err != nil {
			t.Fatal(err)
		}
		if out, err := runServiceBinInstall(t, src, top, dir); err == nil {
			t.Fatalf("install must refuse a world-writable managed dir:\n%s", out)
		}
		if _, err := os.Lstat(filepath.Join(dir, "jentic")); !os.IsNotExist(err) {
			t.Errorf("no copy may be written into an unsafe dir (%v)", err)
		}
	})
	t.Run("symlinked ancestor", func(t *testing.T) {
		top, dir, src := serviceBinTree(t)
		elsewhere := t.TempDir()
		if err := os.Symlink(elsewhere, filepath.Dir(dir)); err != nil {
			t.Fatal(err)
		}
		if out, err := runServiceBinInstall(t, src, top, dir); err == nil {
			t.Fatalf("install must refuse a symlinked ancestor:\n%s", out)
		}
		if entries, _ := os.ReadDir(elsewhere); len(entries) != 0 {
			t.Errorf("nothing may be written through a symlinked ancestor, found %v", entries)
		}
	})
	t.Run("directory at the destination", func(t *testing.T) {
		top, dir, src := serviceBinTree(t)
		if err := os.MkdirAll(filepath.Join(dir, "jentic"), 0o755); err != nil {
			t.Fatal(err)
		}
		if out, err := runServiceBinInstall(t, src, top, dir); err == nil {
			t.Fatalf("install must refuse a directory at the destination:\n%s", out)
		}
	})
	for name, src := range map[string]func(t *testing.T, top string) string{
		"missing source": func(_ *testing.T, top string) string { return filepath.Join(top, "missing") },
		"empty source": func(t *testing.T, _ string) string {
			p := filepath.Join(t.TempDir(), "empty")
			if err := os.WriteFile(p, nil, 0o755); err != nil {
				t.Fatal(err)
			}
			return p
		},
		"directory source": func(t *testing.T, _ string) string { return t.TempDir() },
	} {
		t.Run(name, func(t *testing.T) {
			top, dir, _ := serviceBinTree(t)
			if out, err := runServiceBinInstall(t, src(t, top), top, dir); err == nil {
				t.Fatalf("install must fail:\n%s", out)
			}
			if _, err := os.Lstat(filepath.Join(dir, "jentic")); !os.IsNotExist(err) {
				t.Errorf("a failed install must not leave a copy (%v)", err)
			}
			if entries, _ := os.ReadDir(dir); len(entries) != 0 {
				t.Errorf("a failed install must not leave temp files, found %v", entries)
			}
		})
	}
}

// TestServiceBinaryInstallScriptOddSourcePath: a source path with spaces,
// quotes and shell metacharacters installs verbatim — it never reaches a
// shell.
func TestServiceBinaryInstallScriptOddSourcePath(t *testing.T) {
	top, dir, _ := serviceBinTree(t)
	odd := filepath.Join(t.TempDir(), `it's a "$(dir)"; x`)
	if err := os.MkdirAll(odd, 0o755); err != nil {
		t.Fatal(err)
	}
	src := filepath.Join(odd, "jentic")
	if err := os.WriteFile(src, []byte("odd-binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	if out, err := runServiceBinInstall(t, src, top, dir); err != nil {
		t.Fatalf("install failed: %v\n%s", err, out)
	}
	if data, _ := os.ReadFile(filepath.Join(dir, "jentic")); string(data) != "odd-binary" {
		t.Fatalf("installed copy content = %q", data)
	}
}

func TestServiceBinaryRemoveScript(t *testing.T) {
	top, dir, src := serviceBinTree(t)
	if out, err := runServiceBinInstall(t, src, top, dir); err != nil {
		t.Fatalf("install failed: %v\n%s", err, out)
	}
	// An interrupted install's temp file is swept too.
	if err := os.WriteFile(filepath.Join(dir, ".jentic.AbC123"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command("sh", "-c", serviceBinaryRemoveScript(dir)).CombinedOutput(); err != nil {
		t.Fatalf("remove failed: %v\n%s", err, out)
	}
	if _, err := os.Lstat(dir); !os.IsNotExist(err) {
		t.Fatalf("managed dir must be gone after removal (%v)", err)
	}
	if _, err := os.Lstat(filepath.Dir(dir)); err != nil {
		t.Fatalf("parent dirs must be left alone: %v", err)
	}
	// Absent: a clean no-op.
	if out, err := exec.Command("sh", "-c", serviceBinaryRemoveScript(dir)).CombinedOutput(); err != nil {
		t.Fatalf("remove of an absent copy must be a no-op: %v\n%s", err, out)
	}

	// A symlinked managed dir is never followed.
	elsewhere := t.TempDir()
	keep := filepath.Join(elsewhere, "jentic")
	if err := os.WriteFile(keep, []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(elsewhere, dir); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command("sh", "-c", serviceBinaryRemoveScript(dir)).CombinedOutput(); err != nil {
		t.Fatalf("remove failed: %v\n%s", err, out)
	}
	if _, err := os.Stat(keep); err != nil {
		t.Fatalf("remove must not follow a symlinked managed dir: %v", err)
	}
}

func TestServiceBinaryStateAbsent(t *testing.T) {
	_, _, src := serviceBinTree(t)
	if installed, current := serviceBinaryState(src, filepath.Join(t.TempDir(), "absent")); installed || current {
		t.Fatalf("absent copy = (%v, %v), want (false, false)", installed, current)
	}
}

func TestValidateServiceBinarySource(t *testing.T) {
	if err := ValidateServiceBinarySource("/opt/homebrew/Cellar/jentic/1.0.0/bin/jentic"); err != nil {
		t.Fatalf("valid source rejected: %v", err)
	}
	// Spaces and quotes are fine here: the source is only ever opened by this
	// process, never handed to a shell.
	if err := ValidateServiceBinarySource("/Users/a b/it's/.local/bin/jentic"); err != nil {
		t.Fatalf("a quoted-only source with a space must be accepted: %v", err)
	}
	for _, bad := range []string{"", "jentic", "./jentic", "/bin/jentic\n/etc"} {
		if err := ValidateServiceBinarySource(bad); err == nil {
			t.Errorf("ValidateServiceBinarySource(%q) should fail", bad)
		}
	}
}
