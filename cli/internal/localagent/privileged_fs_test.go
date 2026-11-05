package localagent

import (
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
	want := append(append([]string{"sudo", "find", "/Users/alice"}, chownWalkFilter...), "-exec", "chown", "-fh", "alice", "{}", "+")
	if !slices.Equal(got, want) {
		t.Errorf("recursiveChownCmd = %v, want %v", got, want)
	}
	if got := recursiveChownCmd("alice", "/x", false).Args; !slices.Contains(got, "-h") || slices.Contains(got, "-fh") {
		t.Errorf("non-force chown should use -h: %v", got)
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
