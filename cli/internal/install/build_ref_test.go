package install

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// These tests drive a real `git` against a local "remote" on disk. The bug they
// guard (#949) was that fetchSource hard-reset to origin's default branch and
// ignored the requested ref entirely, so a pinned `update --ref vX.Y.Z` built
// main. Only a real git round-trip proves the checkout genuinely lands on the
// requested commit — asserting on the argv we would have run cannot.

// gitRun runs a git command in dir, failing the test on error.
func gitRun(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(cmd.Environ(),
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@t", "GIT_COMMITTER_NAME=t",
		"GIT_COMMITTER_EMAIL=t@t", "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_SYSTEM=/dev/null",
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %s in %s: %v\n%s", strings.Join(args, " "), dir, err, out)
	}
	return strings.TrimSpace(string(out))
}

// makeUpstream builds a local repo with two commits on the default branch and a
// `v1.0.0` tag on the FIRST one, so "the tag" and "the default branch tip" are
// different commits. That difference is what makes the assertions meaningful.
func makeUpstream(t *testing.T) (dir, tagCommit, headCommit string) {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git not available")
	}
	dir = t.TempDir()
	gitRun(t, dir, "init", "--initial-branch=main", ".")
	writeFile(t, filepath.Join(dir, "marker.txt"), "release")
	gitRun(t, dir, "add", ".")
	gitRun(t, dir, "commit", "-m", "release commit")
	tagCommit = gitRun(t, dir, "rev-parse", "HEAD")
	gitRun(t, dir, "tag", "v1.0.0")

	writeFile(t, filepath.Join(dir, "marker.txt"), "main-moved-on")
	gitRun(t, dir, "add", ".")
	gitRun(t, dir, "commit", "-m", "later main commit")
	headCommit = gitRun(t, dir, "rev-parse", "HEAD")
	return dir, tagCommit, headCommit
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

// TestFetchSourceClonesAtPinnedRef: a fresh clone pinned to a tag must land on
// the tag's commit, not the default branch tip.
func TestFetchSourceClonesAtPinnedRef(t *testing.T) {
	upstream, tagCommit, headCommit := makeUpstream(t)
	dest := filepath.Join(t.TempDir(), "src")

	p := BuildPlan{SourceDir: dest, FromGit: true, GitURL: upstream, Ref: "v1.0.0"}
	if err := p.fetchSource(&strings.Builder{}); err != nil {
		t.Fatalf("fetchSource: %v", err)
	}

	got := gitRun(t, dest, "rev-parse", "HEAD")
	if got != tagCommit {
		t.Errorf("checkout is at %s, want the v1.0.0 commit %s", got, tagCommit)
	}
	if got == headCommit {
		t.Error("checkout landed on the default-branch tip — the pinned ref was ignored (#949)")
	}
}

// TestFetchSourceResyncsExistingCheckoutToPinnedRef is the exact update path:
// ~/.jentic/src already exists (from a previous install) and a pinned update
// must move it onto the requested ref rather than resetting to origin's default.
func TestFetchSourceResyncsExistingCheckoutToPinnedRef(t *testing.T) {
	upstream, tagCommit, headCommit := makeUpstream(t)
	dest := filepath.Join(t.TempDir(), "src")

	// First sync with no pin: lands on the default branch tip, as before.
	unpinned := BuildPlan{SourceDir: dest, FromGit: true, GitURL: upstream}
	if err := unpinned.fetchSource(&strings.Builder{}); err != nil {
		t.Fatalf("initial fetchSource: %v", err)
	}
	if got := gitRun(t, dest, "rev-parse", "HEAD"); got != headCommit {
		t.Fatalf("unpinned sync landed at %s, want default-branch tip %s", got, headCommit)
	}

	// Now re-sync the SAME checkout with a pin.
	pinned := unpinned.AtRef("v1.0.0")
	if err := pinned.fetchSource(&strings.Builder{}); err != nil {
		t.Fatalf("pinned fetchSource: %v", err)
	}
	if got := gitRun(t, dest, "rev-parse", "HEAD"); got != tagCommit {
		t.Errorf("existing checkout is at %s, want the v1.0.0 commit %s (#949)", got, tagCommit)
	}
}

// TestFetchSourceHonoursPinnedCommitSHA: a bare commit SHA cannot be used with
// `clone --branch`, so it exercises the fetch+reset fallback.
func TestFetchSourceHonoursPinnedCommitSHA(t *testing.T) {
	upstream, tagCommit, _ := makeUpstream(t)
	dest := filepath.Join(t.TempDir(), "src")

	p := BuildPlan{SourceDir: dest, FromGit: true, GitURL: upstream, Ref: tagCommit}
	if err := p.fetchSource(&strings.Builder{}); err != nil {
		t.Fatalf("fetchSource with a commit SHA: %v", err)
	}
	if got := gitRun(t, dest, "rev-parse", "HEAD"); got != tagCommit {
		t.Errorf("checkout is at %s, want the pinned commit %s", got, tagCommit)
	}
}

// TestFetchSourceUnpinnedTracksDefaultBranch: the default path is unchanged —
// no ref means "follow origin's default branch", including after a force-push.
func TestFetchSourceUnpinnedTracksDefaultBranch(t *testing.T) {
	upstream, _, headCommit := makeUpstream(t)
	dest := filepath.Join(t.TempDir(), "src")

	p := BuildPlan{SourceDir: dest, FromGit: true, GitURL: upstream}
	if err := p.fetchSource(&strings.Builder{}); err != nil {
		t.Fatalf("fetchSource: %v", err)
	}
	if got := gitRun(t, dest, "rev-parse", "HEAD"); got != headCommit {
		t.Errorf("checkout is at %s, want default-branch tip %s", got, headCommit)
	}
}

// TestFetchSourceUnknownRefFailsLoudly: a typo'd ref must be an error, not a
// silent build of whatever the checkout happened to be on.
func TestFetchSourceUnknownRefFailsLoudly(t *testing.T) {
	upstream, _, _ := makeUpstream(t)
	dest := filepath.Join(t.TempDir(), "src")

	p := BuildPlan{SourceDir: dest, FromGit: true, GitURL: upstream, Ref: "v9.9.9-does-not-exist"}
	if err := p.fetchSource(&strings.Builder{}); err == nil {
		t.Fatal("a nonexistent ref must fail rather than silently building something else")
	}
}

// TestPinnedRefIgnoredOnlyForLocalCheckout: the caller-facing guard. A pin is
// meaningful for a managed clone and impossible for the operator's own tree.
func TestPinnedRefIgnoredOnlyForLocalCheckout(t *testing.T) {
	local := BuildPlan{SourceDir: "/repo"}.AtRef("v1.0.0")
	if !local.PinnedRefIgnored() {
		t.Error("a pinned ref against a local checkout must be reported as ignored")
	}
	if (BuildPlan{SourceDir: "/repo"}).PinnedRefIgnored() {
		t.Error("no ref means nothing is ignored")
	}
	cloned := BuildPlan{SourceDir: "/clone", FromGit: true}.AtRef("v1.0.0")
	if cloned.PinnedRefIgnored() {
		t.Error("a pinned ref against a managed clone is honoured, not ignored")
	}
}

// TestRenderHeaderNamesPinnedRef: the operator must be able to see which build
// they are getting, and be told when a pin does not apply.
func TestRenderHeaderNamesPinnedRef(t *testing.T) {
	cloned := BuildPlan{SourceDir: "/clone", FromGit: true, GitURL: "https://x/y.git"}.AtRef("v1.0.0")
	if out := cloned.RenderHeader(); !strings.Contains(out, "v1.0.0") {
		t.Errorf("clone header does not name the pinned ref:\n%s", out)
	}

	localPinned := BuildPlan{SourceDir: "/repo"}.AtRef("v1.0.0")
	out := localPinned.RenderHeader()
	if !strings.Contains(out, "does not apply") {
		t.Errorf("local-checkout header must flag the ignored pin:\n%s", out)
	}
}
