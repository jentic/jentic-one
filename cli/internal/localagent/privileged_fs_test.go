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
	cmd := CopyBinaryCmd("a-local-agent", "/home/a-local-agent", "/opt/claude", "claude")
	joined := strings.Join(cmd.Args, " ")
	if !strings.Contains(joined, "sudo -u 'a-local-agent' -H ") || !strings.HasSuffix(joined, " < '/opt/claude'") {
		t.Errorf("binary must be redirected into an agent-side writer: %s", joined)
	}
	if cmd.Dir != "/" {
		t.Errorf("copy must run from / (the operator's cwd is unreadable to the agent), got %q", cmd.Dir)
	}
	for _, forbidden := range []string{"cp ", "chown", "install ", "cat '/opt/claude'"} {
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

// TestCopyBinaryScriptFailsOnUnreadableSource runs the real root-side script
// (minus the sudo hop) against a source that does not exist: it must fail and
// must not install an empty file.
func TestCopyBinaryScriptFailsOnUnreadableSource(t *testing.T) {
	home := t.TempDir()
	script := copyBinaryScript("", home, filepath.Join(home, "missing"), "claude")
	if out, err := exec.Command("sh", "-c", script).CombinedOutput(); err == nil {
		t.Fatalf("copy of a missing source must fail:\n%s", out)
	}
	if _, err := os.Lstat(filepath.Join(AgentLocalBinDir(home), "claude")); !os.IsNotExist(err) {
		t.Errorf("no destination may be created for a failed copy (lstat err = %v)", err)
	}
}

func TestCopyBinaryScriptCopiesContent(t *testing.T) {
	home := t.TempDir()
	src := filepath.Join(t.TempDir(), "claude")
	if err := os.WriteFile(src, []byte("binary"), 0o700); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command("sh", "-c", copyBinaryScript("", home, src, "claude")).CombinedOutput(); err != nil {
		t.Fatalf("copy failed: %v\n%s", err, out)
	}
	if got, _ := os.ReadFile(filepath.Join(AgentLocalBinDir(home), "claude")); string(got) != "binary" {
		t.Errorf("content = %q", got)
	}
}

// TestAgentInstallScriptDestinationDirs: a symlink to a directory at the
// destination is replaced (mv would otherwise move the binary INTO the link's
// target), and a real directory there is an error, not a silent move-into.
func TestAgentInstallScriptDestinationDirs(t *testing.T) {
	run := func(dir string) ([]byte, error) {
		cmd := exec.Command("bash", "--noprofile", "--norc", "-c", agentInstallFromStdinScript(dir, "claude"))
		cmd.Stdin = strings.NewReader("new-binary")
		return cmd.CombinedOutput()
	}

	t.Run("symlink to dir", func(t *testing.T) {
		root := t.TempDir()
		dir := filepath.Join(root, "bin")
		target := filepath.Join(root, "elsewhere")
		mustMkdir(t, dir)
		mustMkdir(t, target)
		if err := os.Symlink(target, filepath.Join(dir, "claude")); err != nil {
			t.Fatal(err)
		}
		if out, err := run(dir); err != nil {
			t.Fatalf("install failed: %v\n%s", err, out)
		}
		if fi, err := os.Lstat(filepath.Join(dir, "claude")); err != nil || !fi.Mode().IsRegular() {
			t.Fatalf("destination should be a regular file (err %v)", err)
		}
		if entries, _ := os.ReadDir(target); len(entries) != 0 {
			t.Errorf("the link's target dir must be untouched, has %v", entries)
		}
	})

	t.Run("real dir", func(t *testing.T) {
		dir := filepath.Join(t.TempDir(), "bin")
		mustMkdir(t, filepath.Join(dir, "claude"))
		if out, err := run(dir); err == nil {
			t.Fatalf("install onto a directory must fail:\n%s", out)
		}
		if entries, _ := os.ReadDir(filepath.Join(dir, "claude")); len(entries) != 0 {
			t.Errorf("nothing may be moved into the directory, found %v", entries)
		}
	})
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

	script := copyConfigScript("", agentHome, opHome, []string{src})
	if out, err := exec.Command("bash", "--noprofile", "--norc", "-c", script).CombinedOutput(); err != nil {
		t.Fatalf("pipeline failed: %v\n%s", err, out)
	}

	cfg := filepath.Join(agentHome, ".aws", "config")
	if got, _ := os.ReadFile(cfg); string(got) != "[default]" {
		t.Errorf("config content = %q", got)
	}
	// Credential-bearing files must keep their owner-only mode.
	if fi, err := os.Stat(cfg); err != nil || fi.Mode().Perm() != 0o600 {
		t.Errorf("config mode = %v (err %v), want 0600", fi.Mode().Perm(), err)
	}
	fi, err := os.Lstat(filepath.Join(agentHome, ".aws", "sso", "link"))
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode()&os.ModeSymlink == 0 {
		t.Errorf("nested symlink was dereferenced into a %v", fi.Mode())
	}
}

// TestConfigCopyScriptFailsWhenArchiveFails: a source the archiving side
// cannot read must fail the whole copy, not report success for an empty
// extract (the pipeline's status would otherwise be the extractor's).
func TestConfigCopyScriptFailsWhenArchiveFails(t *testing.T) {
	root := t.TempDir()
	opHome := filepath.Join(root, "op")
	agentHome := filepath.Join(root, "agent")
	mustMkdir(t, opHome)
	mustMkdir(t, agentHome)
	script := copyConfigScript("", agentHome, opHome, []string{filepath.Join(opHome, ".missing")})
	if out, err := exec.Command("bash", "--noprofile", "--norc", "-c", script).CombinedOutput(); err == nil {
		t.Fatalf("copy of an unreadable source must fail:\n%s", out)
	}
	cmd := CopyConfigCmd("agent", agentHome, opHome, []string{filepath.Join(opHome, ".x")})
	if cmd.Args[1] != agentLaunchShell || !strings.Contains(cmd.Args[len(cmd.Args)-1], "set -o pipefail;") {
		t.Errorf("config copy must run under bash with pipefail: %v", cmd.Args)
	}
	if cmd.Dir != "/" {
		t.Errorf("config copy must run from /, got %q", cmd.Dir)
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
