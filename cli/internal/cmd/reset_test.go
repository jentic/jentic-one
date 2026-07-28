package cmd

import (
	"bytes"
	"context"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
)

func TestResetTargets(t *testing.T) {
	cfg := &config.FileConfig{LocalAgents: map[string]config.LocalAgent{
		"claude": {User: "alice-local-agent"},
		"cursor": {User: "alice-cursor-agent"},
	}}

	// Named, configured agent resolves to just that one.
	got, err := resetTargets(cfg, []string{"claude"})
	if err != nil || len(got) != 1 || got[0] != "claude" {
		t.Fatalf("resetTargets([claude]) = %v, %v", got, err)
	}

	// Named but unconfigured agent errors (nothing to reset).
	if _, err := resetTargets(cfg, []string{"nope"}); err == nil {
		t.Fatal("expected an error for an unconfigured agent")
	}

	// No argument targets every configured agent, in stable sorted order.
	all, err := resetTargets(cfg, nil)
	if err != nil {
		t.Fatalf("resetTargets(nil): %v", err)
	}
	if len(all) != 2 || all[0] != "claude" || all[1] != "cursor" {
		t.Fatalf("resetTargets(nil) = %v, want [claude cursor]", all)
	}

	// Empty config errors rather than silently doing nothing.
	if _, err := resetTargets(&config.FileConfig{}, nil); err == nil {
		t.Fatal("expected an error when no local agents are configured")
	}
}

// TestSurveyResetPlan checks the plan is built from the config entry and includes
// leaf grants plus the deduped ancestor traverse chain for grants under the
// operator's home. The on-disk ACL probes return false for these non-existent
// paths, so `present` is false — which is exactly what lets us assert the plan's
// shape without root or real ACLs.
func TestSurveyResetPlan(t *testing.T) {
	home := "/Users/alice"
	entry := config.LocalAgent{
		User:    "alice-local-agent",
		HomeDir: "/Users/Shared/alice-local-agent",
		GrantedDirs: []string{
			"/Users/alice/projects/api", // under home → contributes traverse ancestors
			"/Users/Shared/work",        // outside home → leaf only
		},
	}
	plan := surveyReset(context.Background(), "alice", home, "claude", entry)

	if plan.user != "alice-local-agent" || plan.homeDir != "/Users/Shared/alice-local-agent" {
		t.Fatalf("plan identity = %+v", plan)
	}

	var leaves, traverses []string
	for _, acl := range plan.acls {
		if acl.traverse {
			traverses = append(traverses, acl.dir)
		} else {
			leaves = append(leaves, acl.dir)
		}
	}
	if len(leaves) != 2 {
		t.Errorf("expected 2 leaf grants, got %v", leaves)
	}
	// The under-home grant contributes traverse ancestors up to the home; the
	// outside-home grant contributes none.
	wantTraverse := map[string]bool{"/Users/alice": true, "/Users/alice/projects": true}
	for _, tr := range traverses {
		if !wantTraverse[tr] {
			t.Errorf("unexpected traverse ancestor %q", tr)
		}
	}
	if len(traverses) != len(wantTraverse) {
		t.Errorf("traverse ancestors = %v, want keys of %v", traverses, wantTraverse)
	}
}

// TestSurveyResetDefaultsUser falls back to the derived <operator>-local-agent
// name when the config entry has no user recorded.
func TestSurveyResetDefaultsUser(t *testing.T) {
	plan := surveyReset(context.Background(), "bob", "/Users/bob", "claude", config.LocalAgent{})
	if plan.user != "bob-local-agent" {
		t.Fatalf("plan.user = %q, want bob-local-agent", plan.user)
	}
}

// TestBuildResetStepsOrderAndHome asserts the load-bearing ordering (leaf ACLs →
// traverse ACLs → home → sudoers → account) and that the home step honours the
// delete flag. Only present ACLs become steps.
func TestBuildResetStepsOrderAndHome(t *testing.T) {
	plan := resetPlan{
		agentID:  "claude",
		user:     "alice-local-agent",
		homeDir:  "/Users/Shared/alice-local-agent",
		operator: "alice",
		acls: []aclRemoval{
			{traverse: false, dir: "/Users/alice/projects/api", present: true},
			{traverse: true, dir: "/Users/alice", present: true},
			{traverse: true, dir: "/Users/alice/projects", present: false}, // drifted off disk → skipped
		},
		accountExists: true,
	}

	// Preserve (default): home step re-owns, never deletes.
	steps := buildResetSteps(plan, false)
	whats := make([]string, len(steps))
	for i, s := range steps {
		whats[i] = s.What
	}
	joined := strings.Join(whats, "\n")

	// A drifted (not-present) traverse ACL must not produce a step.
	if strings.Contains(joined, "/Users/alice/projects\n") || strings.Contains(joined, "traverse grant on /Users/alice/projects\n") {
		t.Errorf("drifted-off-disk ACL should be skipped, got steps:\n%s", joined)
	}

	// Ordering: leaf-revoke before traverse-revoke before home before sudoers before account.
	idxLeaf := indexOfContains(whats, "read/write grant on /Users/alice/projects/api")
	idxTraverse := indexOfContains(whats, "traverse grant on /Users/alice")
	idxHome := indexOfContains(whats, "re-own the agent's home")
	idxSudoers := indexOfContains(whats, "sudoers drop-in")
	idxAccount := indexOfContains(whats, "delete the Unix account")
	if !(idxLeaf >= 0 && idxLeaf < idxTraverse && idxTraverse < idxHome && idxHome < idxSudoers && idxSudoers < idxAccount) {
		t.Fatalf("steps out of order: leaf=%d traverse=%d home=%d sudoers=%d account=%d (%v)",
			idxLeaf, idxTraverse, idxHome, idxSudoers, idxAccount, whats)
	}

	// Delete: the home step deletes instead of re-owning.
	delSteps := buildResetSteps(plan, true)
	delJoined := ""
	for _, s := range delSteps {
		delJoined += s.What + "\n"
	}
	if !strings.Contains(delJoined, "delete the agent's home") {
		t.Errorf("delete-home run must delete the home, got:\n%s", delJoined)
	}
	if strings.Contains(delJoined, "re-own the agent's home") {
		t.Errorf("delete-home run must not also re-own the home, got:\n%s", delJoined)
	}
}

// TestBuildResetStepsSkipsMissingAccount omits the account-delete step when the
// account no longer exists (idempotent re-run after a partial teardown).
func TestBuildResetStepsSkipsMissingAccount(t *testing.T) {
	plan := resetPlan{
		agentID:       "claude",
		user:          "alice-local-agent",
		operator:      "alice",
		accountExists: false,
	}
	for _, s := range buildResetSteps(plan, false) {
		if strings.Contains(s.What, "delete the Unix account") {
			t.Errorf("must not delete a non-existent account: %q", s.What)
		}
	}
}

// TestResetRequiresRoot ensures the command refuses to run without EUID 0. Tests
// don't run as root, so resetE should return the root-required error before it
// touches any config.
func TestResetRequiresRoot(t *testing.T) {
	app := &App{Paths: config.Paths{Root: t.TempDir()}, Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}
	err := app.resetE(context.Background(), &resetOptions{}, []string{"claude"})
	if err == nil || !strings.Contains(err.Error(), "must run as root") {
		t.Fatalf("expected a root-required error, got %v", err)
	}
}

func indexOfContains(hay []string, needle string) int {
	for i, s := range hay {
		if strings.Contains(s, needle) {
			return i
		}
	}
	return -1
}
