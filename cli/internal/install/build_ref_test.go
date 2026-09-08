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
	pinned := unpinned.AtRef("v1.0.0", true)
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

// TestPinnedThenUnpinnedSyncStillWorks is the regression for the wedge found
// reviewing #949: `clone --branch <tag>` implies --single-branch, which rewrites
// remote.origin.fetch to only that tag and leaves no origin/<default-branch>.
// A later UNPINNED build could then never reset, so pinning once permanently
// broke the managed checkout until it was deleted by hand.
//
// Every earlier test used a fresh clone, which is exactly why they all passed
// while this sequence — pin to roll back, then resume normal updates — did not.
func TestPinnedThenUnpinnedSyncStillWorks(t *testing.T) {
	upstream, tagCommit, headCommit := makeUpstream(t)
	dest := filepath.Join(t.TempDir(), "src")

	pinned := BuildPlan{SourceDir: dest, FromGit: true, GitURL: upstream, Ref: "v1.0.0"}
	if err := pinned.fetchSource(&strings.Builder{}); err != nil {
		t.Fatalf("pinned fetchSource: %v", err)
	}
	if got := gitRun(t, dest, "rev-parse", "HEAD"); got != tagCommit {
		t.Fatalf("pinned checkout at %s, want %s", got, tagCommit)
	}

	// The operator resumes normal updates. This must not be wedged.
	unpinned := BuildPlan{SourceDir: dest, FromGit: true, GitURL: upstream}
	if err := unpinned.fetchSource(&strings.Builder{}); err != nil {
		t.Fatalf("unpinned sync after a pinned build must work, got: %v", err)
	}
	if got := gitRun(t, dest, "rev-parse", "HEAD"); got != headCommit {
		t.Errorf("unpinned sync landed at %s, want default-branch tip %s", got, headCommit)
	}
}

// TestSyncSurvivesAMovedUpstreamTag: since git 2.20 a fetch refuses to move an
// existing tag ("would clobber existing tag") and exits non-zero. A re-pointed
// tag — a re-cut release, or the history rewrite this fetch+reset design exists
// to survive — would otherwise break every later install/update against the
// managed checkout until it was deleted by hand.
func TestSyncSurvivesAMovedUpstreamTag(t *testing.T) {
	upstream, _, _ := makeUpstream(t)
	dest := filepath.Join(t.TempDir(), "src")

	plan := BuildPlan{SourceDir: dest, FromGit: true, GitURL: upstream, Ref: "v1.0.0"}
	if err := plan.fetchSource(&strings.Builder{}); err != nil {
		t.Fatalf("initial pinned fetchSource: %v", err)
	}

	// Upstream re-points the tag at a different commit.
	writeFile(t, filepath.Join(upstream, "marker.txt"), "re-cut release")
	gitRun(t, upstream, "add", ".")
	gitRun(t, upstream, "commit", "-m", "re-cut")
	gitRun(t, upstream, "tag", "-f", "v1.0.0")
	moved := gitRun(t, upstream, "rev-parse", "v1.0.0^{commit}")

	if err := plan.fetchSource(&strings.Builder{}); err != nil {
		t.Fatalf("a moved upstream tag must not break the sync, got: %v", err)
	}
	if got := gitRun(t, dest, "rev-parse", "HEAD"); got != moved {
		t.Errorf("checkout at %s, want the tag's new commit %s", got, moved)
	}
}

// TestUnpinnedFreshCloneIsNotSingleBranch pins the property underlying the wedge
// directly: a fresh clone must keep a general refspec, so any later sync can
// resolve origin's default branch.
func TestUnpinnedFreshCloneIsNotSingleBranch(t *testing.T) {
	upstream, _, _ := makeUpstream(t)
	dest := filepath.Join(t.TempDir(), "src")

	plan := BuildPlan{SourceDir: dest, FromGit: true, GitURL: upstream, Ref: "v1.0.0"}
	if err := plan.fetchSource(&strings.Builder{}); err != nil {
		t.Fatalf("fetchSource: %v", err)
	}

	refspec := gitRun(t, dest, "config", "remote.origin.fetch")
	if !strings.Contains(refspec, "refs/heads/") {
		t.Errorf("refspec %q does not cover branches; a later unpinned sync cannot resolve "+
			"origin/<default-branch>", refspec)
	}
	if !gitRevParseSucceeds(dest, "origin/main") {
		t.Error("origin/main must be resolvable after a pinned clone, or unpinned syncs wedge")
	}
}

// TestPinnedRefIgnoredOnlyForLocalCheckout: the caller-facing guard. A pin is
// meaningful for a managed clone and impossible for the operator's own tree.
func TestPinnedRefIgnoredOnlyForLocalCheckout(t *testing.T) {
	local := BuildPlan{SourceDir: "/repo"}.AtRef("v1.0.0", true)
	if !local.PinnedRefIgnored() {
		t.Error("a pinned ref against a local checkout must be reported as ignored")
	}
	if (BuildPlan{SourceDir: "/repo"}).PinnedRefIgnored() {
		t.Error("no ref means nothing is ignored")
	}
	cloned := BuildPlan{SourceDir: "/clone", FromGit: true}.AtRef("v1.0.0", true)
	if cloned.PinnedRefIgnored() {
		t.Error("a pinned ref against a managed clone is honoured, not ignored")
	}
	// A ref the CLI resolved itself (the latest release tag on a plain `update`)
	// is not an operator pin, so claiming it was "ignored" would read as though
	// their input had been discarded.
	resolved := BuildPlan{SourceDir: "/repo"}.AtRef("v1.0.0", false)
	if resolved.PinnedRefIgnored() {
		t.Error("a self-resolved ref must not be reported as an ignored --ref")
	}
}

// TestRenderHeaderNamesPinnedRef: the operator must be able to see which build
// they are getting, and be told when a pin does not apply.
func TestRenderHeaderNamesPinnedRef(t *testing.T) {
	cloned := BuildPlan{SourceDir: "/clone", FromGit: true, GitURL: "https://x/y.git"}.AtRef("v1.0.0", true)
	if out := cloned.RenderHeader(); !strings.Contains(out, "v1.0.0") {
		t.Errorf("clone header does not name the pinned ref:\n%s", out)
	}

	localPinned := BuildPlan{SourceDir: "/repo"}.AtRef("v1.0.0", true)
	out := localPinned.RenderHeader()
	if !strings.Contains(out, "does not apply") {
		t.Errorf("local-checkout header must flag the ignored pin:\n%s", out)
	}

	// A plain `jenticctl update` resolves the latest release tag itself and passes
	// it through. The operator never typed --ref, so the header must not claim
	// their flag was ignored.
	localResolved := BuildPlan{SourceDir: "/repo"}.AtRef("v1.0.0", false)
	if out := localResolved.RenderHeader(); strings.Contains(out, "does not apply") {
		t.Errorf("unpinned update must not warn about a --ref the user never passed:\n%s", out)
	}
}
