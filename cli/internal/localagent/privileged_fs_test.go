package localagent

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"testing"
)

// TestWalkFiltersSkipLinks runs the real find filter used by the recursive
// chown walk over a tree holding a plain file, a subdirectory, a
// symlink and a file with a second hard link, and checks which entries a
// walk would act on.
func TestWalkFiltersSkipLinks(t *testing.T) {
	root := t.TempDir()
	tree := filepath.Join(root, "tree")
	mustMkdir(t, filepath.Join(tree, "sub"))
	mustWrite(t, filepath.Join(tree, "plain"))
	mustWrite(t, filepath.Join(tree, "sub", "nested"))
	outside := filepath.Join(root, "outside")
	mustWrite(t, outside)
	if err := os.Link(outside, filepath.Join(tree, "hard")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(tree, "soft")); err != nil {
		t.Fatal(err)
	}

	walk := func(filter []string) []string {
		args := append([]string{tree}, filter...)
		args = append(args, "-print")
		out, err := exec.Command("find", args...).Output()
		if err != nil {
			t.Fatalf("find %v: %v", args, err)
		}
		lines := strings.Split(strings.TrimSpace(string(out)), "\n")
		rel := make([]string, 0, len(lines))
		for _, line := range lines {
			r, _ := filepath.Rel(tree, line)
			rel = append(rel, r)
		}
		slices.Sort(rel)
		return rel
	}

	if got, want := walk(chownWalkFilter), []string{".", "plain", "soft", "sub", "sub/nested"}; !slices.Equal(got, want) {
		t.Errorf("chown walk = %v, want %v (hard-linked file must be skipped; symlink is chowned with -h)", got, want)
	}
}

func TestRecursiveChownCmdShape(t *testing.T) {
	chownPath := "/bin/chown"
	if runtime.GOOS == "darwin" {
		chownPath = "/usr/sbin/chown"
	}
	cmd := recursiveChownCmd("alice", "/Users/alice", true)
	want := append(append([]string{"/usr/bin/sudo", "/usr/bin/find", "/Users/alice"}, chownWalkFilter...), "-execdir", chownPath, "-fh", "alice", "{}", "+")
	if !slices.Equal(cmd.Args, want) {
		t.Errorf("recursiveChownCmd = %v, want %v", cmd.Args, want)
	}
	assertFixedUtilityPATH(t, cmd)
	if got := recursiveChownCmd("alice", "/x", false).Args; !slices.Contains(got, "-h") || slices.Contains(got, "-fh") {
		t.Errorf("non-force chown should use -h: %v", got)
	}
}

func TestRecursiveChownCannotExecuteAgentPlantedChown(t *testing.T) {
	root := t.TempDir()
	tree := filepath.Join(root, "tree")
	marker := filepath.Join(root, "planted-chown-executed")
	for _, dir := range []string{root, tree, filepath.Join(tree, "sub")} {
		mustMkdir(t, dir)
		planted := filepath.Join(dir, "chown")
		script := "#!/bin/sh\n/usr/bin/touch " + shellQuote(marker) + "\n"
		if err := os.WriteFile(planted, []byte(script), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	mustWrite(t, filepath.Join(tree, "sub", "nested"))
	t.Setenv("PATH", ".")
	built := recursiveChownCmd(strconv.Itoa(os.Getuid()), tree, true)

	// Resolve find itself so this replay isolates the utility selected by -execdir.
	args := append([]string(nil), built.Args[1:]...)
	args[0] = "/usr/bin/find"
	cmd := exec.Command(args[0], args[1:]...)
	cmd.Dir = root
	cmd.Env = []string{"PATH=."}
	// GNU find refuses -execdir outright when PATH holds a relative entry, which
	// also keeps the planted utility from running; BSD find runs the command and
	// relies on the absolute utility path asserted in TestRecursiveChownCmdShape.
	if output, err := cmd.CombinedOutput(); err != nil &&
		!strings.Contains(string(output), "insecure in combination with the -execdir") {
		t.Fatalf("replay %v: %v\n%s", args, err, output)
	}
	if _, err := os.Stat(marker); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("agent-planted chown was executed; marker stat error = %v", err)
	}
}

func assertFixedUtilityPATH(t *testing.T, cmd *exec.Cmd) {
	t.Helper()
	var paths []string
	for _, entry := range cmd.Env {
		if value, ok := strings.CutPrefix(entry, "PATH="); ok {
			paths = append(paths, value)
		}
	}
	if len(paths) != 1 || paths[0] != agentSystemPATH {
		t.Errorf("command PATH entries = %v, want exactly %q (all environment: %v)", paths, agentSystemPATH, cmd.Env)
		return
	}
	for _, entry := range strings.Split(paths[0], string(os.PathListSeparator)) {
		if !filepath.IsAbs(entry) {
			t.Errorf("command PATH contains relative entry %q: %s", entry, paths[0])
		}
	}
}

func TestAssertNoSymlinkComponentsRefusesIntermediateLink(t *testing.T) {
	root := t.TempDir()
	home := filepath.Join(root, "home")
	outside := filepath.Join(root, "outside")
	mustMkdir(t, home)
	mustMkdir(t, outside)
	if err := os.Symlink(outside, filepath.Join(home, ".config")); err != nil {
		t.Fatal(err)
	}
	for _, leaf := range []string{"gcloud", "jentic"} {
		path := filepath.Join(home, ".config", leaf)
		if err := AssertNoSymlinkComponents(home, path); !errors.Is(err, ErrSymlinkedPath) {
			t.Errorf("AssertNoSymlinkComponents(%s) = %v, want ErrSymlinkedPath", path, err)
		}
	}
}

func TestAssertNoSymlinkComponentsAllowsLeafLinkAndMissing(t *testing.T) {
	root := t.TempDir()
	home := filepath.Join(root, "home")
	mustMkdir(t, home)
	outside := filepath.Join(root, "outside")
	mustWrite(t, outside)
	leaf := filepath.Join(home, "leaf")
	if err := os.Symlink(outside, leaf); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{leaf, filepath.Join(home, "missing", "child")} {
		if err := AssertNoSymlinkComponents(home, path); err != nil {
			t.Errorf("AssertNoSymlinkComponents(%s) = %v, want nil", path, err)
		}
	}
}

func TestRemoveAsAgentCmdShape(t *testing.T) {
	paths := []string{"/Users/Shared/a/.aws", "/Users/Shared/a/.codex"}
	got := RemoveAsAgentCmd("alice-local-agent", paths)
	if got == nil {
		t.Fatal("expected a removal command")
	}
	want := append([]string{"/usr/bin/sudo", "-u", "alice-local-agent", "--", "/bin/rm", "-rf", "--"}, paths...)
	if !slices.Equal(got.Args, want) {
		t.Errorf("RemoveAsAgentCmd = %v, want %v", got.Args, want)
	}
	if cmd := RemoveAsAgentCmd("Invalid User", paths); cmd != nil {
		t.Errorf("invalid agent user must be rejected, got %v", cmd.Args)
	}
	if cmd := RemoveAsAgentCmd("alice-local-agent", nil); cmd != nil {
		t.Errorf("empty removal list must return nil, got %v", cmd.Args)
	}
}

func TestRecursiveChownUsesExecdir(t *testing.T) {
	root := t.TempDir()
	tree := filepath.Join(root, "tree")
	outside := filepath.Join(root, "outside")
	mustMkdir(t, filepath.Join(tree, "sub"))
	mustMkdir(t, outside)
	mustWrite(t, filepath.Join(tree, "sub", "nested"))
	mustWrite(t, filepath.Join(outside, "escaped"))
	if err := os.Symlink(outside, filepath.Join(tree, "linked")); err != nil {
		t.Fatal(err)
	}

	args := append([]string{tree}, chownWalkFilter...)
	args = append(args, "-execdir", "echo", "{}", "+")
	out, err := exec.Command("find", args...).CombinedOutput()
	if err != nil {
		t.Fatalf("find %v: %v\n%s", args, err, out)
	}
	if strings.Contains(string(out), "escaped") || strings.Contains(string(out), outside+string(filepath.Separator)) {
		t.Errorf("execdir walk visited an entry outside the root:\n%s", out)
	}
}

func mustMkdir(t *testing.T, p string) {
	t.Helper()
	if err := os.MkdirAll(p, 0o755); err != nil {
		t.Fatal(err)
	}
}

func mustWrite(t *testing.T, p string) {
	t.Helper()
	if err := os.WriteFile(p, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
}
