package localagent

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
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
	got := recursiveChownCmd("alice", "/Users/alice", true).Args
	want := append(append([]string{"sudo", "find", "/Users/alice"}, chownWalkFilter...), "-execdir", "chown", "-fh", "alice", "{}", "+")
	if !slices.Equal(got, want) {
		t.Errorf("recursiveChownCmd = %v, want %v", got, want)
	}
	if got := recursiveChownCmd("alice", "/x", false).Args; !slices.Contains(got, "-h") || slices.Contains(got, "-fh") {
		t.Errorf("non-force chown should use -h: %v", got)
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
	want := append([]string{"sudo", "-u", "alice-local-agent", "--", "/bin/rm", "-rf", "--"}, paths...)
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
