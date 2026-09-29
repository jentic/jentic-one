package localagent

import (
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

func TestAgentBashArgsSkipStartupFiles(t *testing.T) {
	args := agentBashArgs("a-local-agent", "command -v claude")
	for _, want := range []string{"--noprofile", "--norc", "-c"} {
		if !slices.Contains(args, want) {
			t.Errorf("agent shell args missing %q: %v", want, args)
		}
	}
	if slices.Contains(args, "-lc") || slices.Contains(args, "-l") {
		t.Errorf("agent shell must not be a login shell (it would source agent-owned rc files): %v", args)
	}
	snippet := args[len(args)-1]
	if !strings.HasPrefix(snippet, "export PATH=") || !strings.HasSuffix(snippet, "command -v claude") {
		t.Errorf("snippet must pin PATH before running the command: %q", snippet)
	}
}

func TestCopyBinaryCmdWritesAsTheAgent(t *testing.T) {
	joined := strings.Join(CopyBinaryCmd("a-local-agent", "/home/a-local-agent", "/opt/claude", "claude").Args, " ")
	if !strings.Contains(joined, "cat '/opt/claude' | sudo -u 'a-local-agent' -H ") {
		t.Errorf("binary must be streamed into an agent-side writer: %s", joined)
	}
	for _, forbidden := range []string{"cp ", "chown", "install "} {
		if strings.Contains(joined, forbidden) {
			t.Errorf("binary copy must not run a root %q into the agent home: %s", forbidden, joined)
		}
	}
}

// TestAgentInstallScriptReplacesLinkInsteadOfWritingThrough runs the
// agent-side install script against a destination that is a symlink to an
// unrelated file: the unrelated file must be untouched and the destination
// must become a regular executable file with the streamed content.
func TestAgentInstallScriptReplacesLinkInsteadOfWritingThrough(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, ".local", "bin")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	other := filepath.Join(root, "other")
	if err := os.WriteFile(other, []byte("original"), 0o600); err != nil {
		t.Fatal(err)
	}
	dest := filepath.Join(dir, "claude")
	if err := os.Symlink(other, dest); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command("bash", "--noprofile", "--norc", "-c", agentInstallFromStdinScript(dir, "claude"))
	cmd.Stdin = strings.NewReader("new-binary")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("install script failed: %v\n%s", err, out)
	}

	if got, _ := os.ReadFile(other); string(got) != "original" {
		t.Errorf("link target was modified: %q", got)
	}
	fi, err := os.Lstat(dest)
	if err != nil {
		t.Fatal(err)
	}
	if !fi.Mode().IsRegular() {
		t.Fatalf("destination should be a regular file, got %v", fi.Mode())
	}
	if fi.Mode().Perm() != 0o755 {
		t.Errorf("destination mode = %v, want 0755", fi.Mode().Perm())
	}
	if got, _ := os.ReadFile(dest); string(got) != "new-binary" {
		t.Errorf("destination content = %q", got)
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Errorf("temp file left behind: %v", entries)
	}
}

func TestAgentInstallScriptCreatesMissingDir(t *testing.T) {
	dir := filepath.Join(t.TempDir(), ".local", "bin")
	cmd := exec.Command("bash", "--noprofile", "--norc", "-c", agentInstallFromStdinScript(dir, "codex"))
	cmd.Stdin = strings.NewReader("x")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("install script failed: %v\n%s", err, out)
	}
	if got, _ := os.ReadFile(filepath.Join(dir, "codex")); string(got) != "x" {
		t.Errorf("content = %q", got)
	}
}

// TestWalkFiltersSkipLinks runs the real find filters used by the recursive
// chown / ACL walks over a tree holding a plain file, a subdirectory, a
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
	if got, want := walk(aclWalkFilter), []string{".", "plain", "sub", "sub/nested"}; !slices.Equal(got, want) {
		t.Errorf("ACL walk = %v, want %v (hard link and symlink must be skipped)", got, want)
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

// TestConfigCopyPipelinePreservesLinks runs the real archive | extract
// pipeline CopyConfigCmd builds (without sudo) and checks that a nested
// symlink arrives as a link, not as a copy of what it points at.
func TestConfigCopyPipelinePreservesLinks(t *testing.T) {
	root := t.TempDir()
	opHome := filepath.Join(root, "op")
	agentHome := filepath.Join(root, "agent")
	src := filepath.Join(opHome, ".aws")
	mustMkdir(t, filepath.Join(src, "sso"))
	if err := os.WriteFile(filepath.Join(src, "config"), []byte("[default]"), 0o600); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(root, "outside")
	if err := os.WriteFile(outside, []byte("do-not-copy"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(src, "sso", "link")); err != nil {
		t.Fatal(err)
	}
	mustMkdir(t, agentHome)

	destParent := agentHome
	script := operatorArchiveCmdline(src) + " | bash --noprofile --norc -c " + shellQuote(agentExtractScript(destParent))
	if out, err := exec.Command("sh", "-c", script).CombinedOutput(); err != nil {
		t.Fatalf("pipeline failed: %v\n%s", err, out)
	}

	if got, _ := os.ReadFile(filepath.Join(agentHome, ".aws", "config")); string(got) != "[default]" {
		t.Errorf("config content = %q", got)
	}
	fi, err := os.Lstat(filepath.Join(agentHome, ".aws", "sso", "link"))
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode()&os.ModeSymlink == 0 {
		t.Errorf("nested symlink was dereferenced into a %v", fi.Mode())
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
