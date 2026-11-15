package update

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestSystemPackageManaged(t *testing.T) {
	stubPackageTools(t, nil)
	tests := []struct {
		name   string
		target string
		want   bool
	}{
		{"deb/rpm/apk install", "/usr/bin/jenticctl", true},
		{"unclean path into /usr/bin", "/usr/lib/../bin/jenticctl", true},
		{"install.sh default dir", "/home/u/.jentic/bin/jenticctl", false},
		{"manual install into /usr/local/bin", "/usr/local/bin/jenticctl", false},
		{"Linuxbrew cask", "/home/linuxbrew/.linuxbrew/Caskroom/jentic/0.44.0/jenticctl", false},
		{"nested under /usr/bin", "/usr/bin/jentic/jenticctl", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := SystemPackageManaged(tt.target); got != tt.want {
				t.Errorf("SystemPackageManaged(%q) = %v, want %v", tt.target, got, tt.want)
			}
		})
	}
}

// TestSystemPackageManagedResolvesSymlinks pins that a path reached through a
// symlink is judged by where it resolves to, in both directions.
func TestSystemPackageManagedResolvesSymlinks(t *testing.T) {
	stubPackageTools(t, nil)
	root := t.TempDir()
	pkgDir := filepath.Join(root, "usr", "bin")
	otherDir := filepath.Join(root, "home", "bin")
	linkDir := filepath.Join(root, "usr", "local", "bin")
	for _, d := range []string{pkgDir, otherDir, linkDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	pkgBin := filepath.Join(pkgDir, "jenticctl")
	userBin := filepath.Join(otherDir, "jenticctl")
	for _, f := range []string{pkgBin, userBin} {
		if err := os.WriteFile(f, []byte("x"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	toPkg := filepath.Join(linkDir, "to-pkg")
	toUser := filepath.Join(pkgDir, "to-user")
	if err := os.Symlink(pkgBin, toPkg); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if err := os.Symlink(userBin, toUser); err != nil {
		t.Fatal(err)
	}
	aliasDir := filepath.Join(root, "bin")
	if err := os.Symlink(pkgDir, aliasDir); err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name   string
		target string
		want   bool
	}{
		{"binary in the package dir", pkgBin, true},
		{"symlink elsewhere pointing into the package dir", toPkg, true},
		{"symlink in the package dir pointing out of it", toUser, false},
		{"binary outside the package dir", userBin, false},
		{"reached through a merged-/usr style dir alias", filepath.Join(aliasDir, "jenticctl"), true},
		{"missing file in the package dir", filepath.Join(pkgDir, "absent"), true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := systemPackageManaged(tt.target, pkgDir); got != tt.want {
				t.Errorf("systemPackageManaged(%q) = %v, want %v", tt.target, got, tt.want)
			}
		})
	}
}

type fakeAnswer struct {
	out  string
	code int
	err  error
}

type fakeCall struct {
	bin  string
	argv []string
}

// stubPackageTools installs fake package managers: answers maps a tool name to
// its reply; a tool absent from the map is "not installed". It returns the
// recorded invocations.
func stubPackageTools(t *testing.T, answers map[string]fakeAnswer) *[]fakeCall {
	t.Helper()
	origFind, origRun := findPackageTool, runPackageTool
	t.Cleanup(func() { findPackageTool, runPackageTool = origFind, origRun })
	calls := &[]fakeCall{}
	findPackageTool = func(name string) string {
		if _, ok := answers[name]; ok {
			return "/usr/bin/" + name
		}
		return ""
	}
	runPackageTool = func(_ context.Context, bin string, argv []string) (string, int, error) {
		*calls = append(*calls, fakeCall{bin, argv})
		a := answers[filepath.Base(bin)]
		return a.out, a.code, a.err
	}
	return calls
}

func TestSystemPackageManagedCorroboratesWithPackageManager(t *testing.T) {
	const bin = "/usr/bin/jenticctl"
	dpkgOwned := fakeAnswer{out: "jenticctl: /usr/bin/jenticctl\n"}
	dpkgNone := fakeAnswer{out: "dpkg-query: no path found matching pattern /usr/bin/jenticctl\n", code: 1}
	rpmOwned := fakeAnswer{out: "jenticctl-0.44.0-1.x86_64\n"}
	rpmNone := fakeAnswer{out: "file /usr/bin/jenticctl is not owned by any package\n", code: 1}
	apkOwned := fakeAnswer{out: "/usr/bin/jenticctl is owned by jenticctl-0.44.0-r0\n"}
	apkNone := fakeAnswer{out: "ERROR: /usr/bin/jenticctl: Could not find owner package\n", code: 1}
	tests := []struct {
		name    string
		answers map[string]fakeAnswer
		want    bool
	}{
		{"dpkg owns", map[string]fakeAnswer{"dpkg": dpkgOwned}, true},
		{"rpm owns", map[string]fakeAnswer{"rpm": rpmOwned}, true},
		{"apk owns", map[string]fakeAnswer{"apk": apkOwned}, true},
		{"dpkg multi-package owner line", map[string]fakeAnswer{"dpkg": {out: "a, b:amd64: /usr/bin/jenticctl\n"}}, true},
		{"dpkg: not owned", map[string]fakeAnswer{"dpkg": dpkgNone}, false},
		{"rpm: not owned", map[string]fakeAnswer{"rpm": rpmNone}, false},
		{"apk: not owned", map[string]fakeAnswer{"apk": apkNone}, false},
		{"one tool owns, another says no", map[string]fakeAnswer{"dpkg": dpkgOwned, "rpm": rpmNone}, true},
		{"foreign rpm db says no, no other answer", map[string]fakeAnswer{"rpm": rpmNone, "dpkg": {err: errors.New("boom")}}, false},
		{"no tool installed: directory rule", nil, true},
		{"probe error: directory rule", map[string]fakeAnswer{"dpkg": {err: context.DeadlineExceeded}}, true},
		{"unrecognised failure output: directory rule", map[string]fakeAnswer{"rpm": {out: "error: cannot open Packages database\n", code: 1}}, true},
		{"dpkg exit 0 without a matching line (diversion only)", map[string]fakeAnswer{"dpkg": {out: "diversion by x from: /usr/bin/jenticctl\n"}}, true},
		{"dpkg lists a different path", map[string]fakeAnswer{"dpkg": {out: "other: /usr/bin/jenticctl-old\n"}, "rpm": rpmNone}, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			stubPackageTools(t, tt.answers)
			if got := systemPackageManaged(bin, "/usr/bin"); got != tt.want {
				t.Errorf("systemPackageManaged = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestSystemPackageManagedProbeArgvAndScope(t *testing.T) {
	calls := stubPackageTools(t, map[string]fakeAnswer{
		"dpkg": {out: "x: /usr/bin/jenticctl\n"},
	})
	// Outside the package dir: no probe at all.
	for _, p := range []string{"/usr/local/bin/jenticctl", "/home/u/.jentic/bin/jenticctl", "/usr/bin/sub/jenticctl"} {
		if systemPackageManaged(p, "/usr/bin") {
			t.Errorf("%s reported managed", p)
		}
	}
	if len(*calls) != 0 {
		t.Fatalf("probes ran for paths outside /usr/bin: %v", *calls)
	}
	// A hostile-looking name stays a single argv element: no shell involved.
	name := "/usr/bin/a b;touch x$(id)"
	systemPackageManaged(name, "/usr/bin")
	if len(*calls) == 0 {
		t.Fatal("no probe ran")
	}
	want := fakeCall{"/usr/bin/dpkg", []string{"-S", "--", name}}
	if !reflect.DeepEqual((*calls)[0], want) {
		t.Errorf("argv = %#v, want %#v", (*calls)[0], want)
	}
}

func TestSystemPackageManagedProbesResolvedPath(t *testing.T) {
	root := t.TempDir()
	pkgDir := filepath.Join(root, "usr", "bin")
	if err := os.MkdirAll(pkgDir, 0o755); err != nil {
		t.Fatal(err)
	}
	realBin := filepath.Join(pkgDir, "jenticctl")
	if err := os.WriteFile(realBin, []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "jenticctl-link")
	if err := os.Symlink(realBin, link); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	resolved, _ := filepath.EvalSymlinks(realBin)
	calls := stubPackageTools(t, map[string]fakeAnswer{"dpkg": {out: "jenticctl: " + resolved + "\n"}})
	if !systemPackageManaged(link, pkgDir) {
		t.Error("symlink into the package dir with an owning package is not managed")
	}
	if len(*calls) != 1 || (*calls)[0].argv[len((*calls)[0].argv)-1] != resolved {
		t.Errorf("probe did not receive the resolved path %q: %v", resolved, *calls)
	}
}

func TestRunPackageToolMissingBinaryAndTimeout(t *testing.T) {
	// The realBin runner: a non-existent binary is an error, not a panic, and a
	// shell metacharacter in argv is never interpreted.
	_, _, err := runPackageTool(context.Background(), "/nonexistent/tool", []string{"-S", "--", "a;b"})
	if err == nil {
		t.Error("running a missing tool returned no error")
	}
	if !strings.HasPrefix(packageToolDirs, "/usr/sbin") {
		t.Errorf("tool lookup dirs = %q", packageToolDirs)
	}
	if sleep := "/bin/sleep"; fileExists(sleep) {
		_, code, err := runPackageTool(context.Background(), sleep, []string{"10"})
		if err == nil || code != -1 {
			t.Errorf("a probe past the timeout returned code=%d err=%v, want an error", code, err)
		}
	}
}

func fileExists(p string) bool { _, err := os.Stat(p); return err == nil }
