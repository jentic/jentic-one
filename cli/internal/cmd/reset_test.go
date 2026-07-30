package cmd

import (
	"bytes"
	"context"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/profile"
)

// TestSurveyResetPlan checks the plan is built from the account record and includes
// leaf grants plus the deduped ancestor traverse chain for grants under the
// operator's home. The on-disk ACL probes return false for these non-existent
// paths, so `present` is false — which is exactly what lets us assert the plan's
// shape without root or real ACLs.
func TestSurveyResetPlan(t *testing.T) {
	home := "/Users/alice"
	acct := config.AgentAccount{
		User:    "alice-local-agent",
		HomeDir: "/Users/Shared/alice-local-agent",
		GrantedDirs: []string{
			"/Users/alice/projects/api", // under home → contributes traverse ancestors
			"/Users/Shared/work",        // outside home → leaf only
		},
	}
	plan := surveyReset(context.Background(), "alice", home, acct)

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

// TestSurveyResetNestedLeafNotTraversed guards the overlap bug: when a dir is
// granted directly AND is an ancestor of a deeper leaf grant, it must appear ONLY
// as a leaf ACL, never also as a traverse ancestor of the deeper grant. It carries
// the rwx leaf ACE, not a bare execute ACE — so a traverse revoke on it (`chmod -a
// … allow execute`) would error "Entry not found" and abort the whole teardown.
func TestSurveyResetNestedLeafNotTraversed(t *testing.T) {
	home := "/Users/alice"
	acct := config.AgentAccount{
		User: "alice-local-agent",
		GrantedDirs: []string{
			"/Users/alice/workspace",             // leaf grant AND ancestor of the next
			"/Users/alice/workspace/github/repo", // deeper leaf grant
		},
	}
	plan := surveyReset(context.Background(), "alice", home, acct)

	for _, acl := range plan.acls {
		if acl.traverse && acl.dir == "/Users/alice/workspace" {
			t.Errorf("a dir that is itself a leaf grant must not also be a traverse ancestor: %q", acl.dir)
		}
	}
	// It must still be present as a leaf, and the genuine (non-leaf) ancestors of
	// the deeper grant — the home and the intermediate `github` — are traverses.
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
	wantTraverse := map[string]bool{"/Users/alice": true, "/Users/alice/workspace/github": true}
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
// name when the account record has no user.
func TestSurveyResetDefaultsUser(t *testing.T) {
	plan := surveyReset(context.Background(), "bob", "/Users/bob", config.AgentAccount{})
	if plan.user != "bob-local-agent" {
		t.Fatalf("plan.user = %q, want bob-local-agent", plan.user)
	}
}

// TestBuildResetStepsOrderAndHome asserts the load-bearing ordering (leaf ACLs →
// traverse ACLs → identity → home → sudoers → account) and that the home step
// honours the delete flag. Only present ACLs become steps.
func TestBuildResetStepsOrderAndHome(t *testing.T) {
	plan := resetPlan{
		user:      "alice-local-agent",
		homeDir:   "/Users/Shared/alice-local-agent",
		configDir: "/Users/Shared/alice-local-agent/.jentic",
		operator:  "alice",
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

	// Ordering: leaf-revoke → traverse-revoke → identity dir → home → sudoers → account.
	// The agent's own ~/.jentic is torn down before the home is settled and always
	// (when the home is kept) so a re-bootstrap can't resurrect a torn-down agent.
	idxLeaf := indexOfContains(whats, "read/write grant on /Users/alice/projects/api")
	idxTraverse := indexOfContains(whats, "traverse grant on /Users/alice")
	idxIdentity := indexOfContains(whats, "remove the agent's jentic identity")
	idxHome := indexOfContains(whats, "re-own the agent's home")
	idxSudoers := indexOfContains(whats, "sudoers drop-in")
	idxAccount := indexOfContains(whats, "delete the Unix account")
	if idxIdentity < 0 || !strings.Contains(joined, "/Users/Shared/alice-local-agent/.jentic") {
		t.Errorf("expected the agent identity dir to be torn down, got:\n%s", joined)
	}
	if !(idxLeaf >= 0 && idxLeaf < idxTraverse && idxTraverse < idxIdentity && idxIdentity < idxHome && idxHome < idxSudoers && idxSudoers < idxAccount) {
		t.Fatalf("steps out of order: leaf=%d traverse=%d identity=%d home=%d sudoers=%d account=%d (%v)",
			idxLeaf, idxTraverse, idxIdentity, idxHome, idxSudoers, idxAccount, whats)
	}

	// Best-effort steps: the home step (a macOS home has SIP/TCC-protected files
	// nobody can chown/remove) and both ACL revokes (their macOS `chmod -a` exits
	// non-zero on entries that don't carry the exact ACE — subtree entries for the
	// recursive leaf revoke, a drifted/re-shaped ancestor for the traverse revoke).
	// None must abort the teardown; nothing else is best-effort.
	for _, s := range steps {
		wantBestEffort := strings.Contains(s.What, "the agent's home") ||
			strings.Contains(s.What, "read/write grant on") ||
			strings.Contains(s.What, "traverse grant on")
		if s.BestEffort != wantBestEffort {
			t.Errorf("step %q BestEffort=%v, want %v", s.What, s.BestEffort, wantBestEffort)
		}
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
	// When the home is being deleted, the recursive rm already removes the agent's
	// ~/.jentic, so no separate identity-removal step is emitted.
	if strings.Contains(delJoined, "remove the agent's jentic identity") {
		t.Errorf("delete-home run must not emit a separate identity step (the home rm covers it), got:\n%s", delJoined)
	}
}

// TestBuildResetStepsSkipsMissingAccount omits the account-delete step when the
// account no longer exists (idempotent re-run after a partial teardown).
func TestBuildResetStepsSkipsMissingAccount(t *testing.T) {
	plan := resetPlan{
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

// TestResetRunsAsOperator confirms a full reset no longer refuses to run without
// EUID 0: it runs as the operator (only its individual steps are sudo-fronted).
// With a configured account it surfaces the "(requires sudo)" notice and proceeds
// to the plan; the non-interactive-without-force guard then stops it — proving it
// got past any (removed) root gate. There must be NO "must run as root" error.
func TestResetRunsAsOperator(t *testing.T) {
	out := &bytes.Buffer{}
	app := &App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}
	cfg := &config.FileConfig{}
	cfg.SetAgentAccount(config.AgentAccount{User: "alice-local-agent", AccountCreated: true, Enabled: true})
	if err := cfg.Save(app.Paths); err != nil {
		t.Fatalf("save config: %v", err)
	}

	err := app.resetE(context.Background(), &resetOptions{}, nil)
	if err != nil && strings.Contains(err.Error(), "must run as root") {
		t.Fatalf("reset must not require root, got %v", err)
	}
	// Non-interactive without --force is the stopping point, not a root check.
	if err == nil || !strings.Contains(err.Error(), "without --force") {
		t.Fatalf("expected the non-interactive --force guard, got %v", err)
	}
	if !strings.Contains(out.String(), "requires sudo") {
		t.Errorf("expected a (requires sudo) notice, got:\n%s", out.String())
	}
}

// TestResetFullWipesProfiles confirms a bare `jentic reset --force` (no account) is
// a clean slate: it removes every profile and clears default_profile, even when no
// agent account is configured.
func TestResetFullWipesProfiles(t *testing.T) {
	out := &bytes.Buffer{}
	app := &App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}
	seedProfile(t, app, "default", "agnt_default")
	seedProfile(t, app, "work", "agnt_work")
	if err := config.SetDefaultProfile(app.Paths, "work"); err != nil {
		t.Fatalf("set default: %v", err)
	}

	// No account configured + no profile arg: a valid config-only clean slate.
	err := app.resetE(context.Background(), &resetOptions{force: true}, nil)
	if err != nil {
		t.Fatalf("resetE: %v", err)
	}

	names, err := profile.List(app.Paths)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if len(names) != 0 {
		t.Errorf("expected all profiles removed, got %v", names)
	}
	cfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if cfg.DefaultProfile != "" {
		t.Errorf("default_profile should be cleared, got %q", cfg.DefaultProfile)
	}
}

// TestResetSingleProfileKeepsOthers confirms `jentic reset <profile> --force`
// removes only the named operator-owned profile and never touches the others or
// the agent account.
func TestResetSingleProfileKeepsOthers(t *testing.T) {
	app := &App{Paths: config.Paths{Root: t.TempDir()}, Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}
	seedProfile(t, app, "default", "agnt_default")
	seedProfile(t, app, "work", "agnt_work")

	if err := app.resetE(context.Background(), &resetOptions{force: true}, []string{"work"}); err != nil {
		t.Fatalf("resetE: %v", err)
	}

	names, err := profile.List(app.Paths)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if len(names) != 1 || names[0] != "default" {
		t.Errorf("a single-profile reset must keep the other profiles, got %v", names)
	}
}

// TestResetSingleProfileClearsDefault confirms removing the profile that was the
// operator's default also clears the default_profile pointer.
func TestResetSingleProfileClearsDefault(t *testing.T) {
	app := &App{Paths: config.Paths{Root: t.TempDir()}, Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}
	seedProfile(t, app, "solo", "agnt_solo")
	if err := config.SetDefaultProfile(app.Paths, "solo"); err != nil {
		t.Fatalf("set default: %v", err)
	}

	if err := app.resetE(context.Background(), &resetOptions{force: true}, []string{"solo"}); err != nil {
		t.Fatalf("resetE: %v", err)
	}

	cfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if cfg.DefaultProfile != "" {
		t.Errorf("default_profile should be cleared after removing it, got %q", cfg.DefaultProfile)
	}
}

// TestResetUnknownProfileNameErrors confirms a name that matches no profile errors,
// and that a name matching a known agent binary id points the operator at the bare
// full reset instead.
func TestResetUnknownProfileNameErrors(t *testing.T) {
	app := &App{Paths: config.Paths{Root: t.TempDir()}, Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}
	seedProfile(t, app, "default", "agnt_default")

	// A plain unknown name.
	err := app.resetE(context.Background(), &resetOptions{}, []string{"ghost"})
	if err == nil || !strings.Contains(err.Error(), "no profile") {
		t.Fatalf("expected a no-profile error, got %v", err)
	}

	// A name that is a known agent binary id → steer to the bare full reset.
	err = app.resetE(context.Background(), &resetOptions{}, []string{"claude"})
	if err == nil || !strings.Contains(err.Error(), "with no argument") {
		t.Fatalf("expected a steer-to-full-reset error, got %v", err)
	}
}

// TestResetConfigRequiresForceNonInteractive confirms a full reset is not run
// non-interactively without --force. Tests run with a non-terminal stdin, so a
// bare `jentic reset` with profiles present must hit the single whole-slate guard.
func TestResetConfigRequiresForceNonInteractive(t *testing.T) {
	app := &App{Paths: config.Paths{Root: t.TempDir()}, Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}
	seedProfile(t, app, "default", "agnt_default")

	err := app.resetE(context.Background(), &resetOptions{}, nil)
	if err == nil || !strings.Contains(err.Error(), "without --force") {
		t.Fatalf("expected a non-interactive --force guard, got %v", err)
	}
	// Profile must survive the refusal.
	names, _ := profile.List(app.Paths)
	if len(names) != 1 {
		t.Errorf("profile should survive the refusal, got %v", names)
	}
}

// TestResetNothingToDo is a friendly no-op when there is no account and no config
// to remove.
func TestResetNothingToDo(t *testing.T) {
	out := &bytes.Buffer{}
	app := &App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}
	if err := app.resetE(context.Background(), &resetOptions{force: true}, nil); err != nil {
		t.Fatalf("resetE: %v", err)
	}
	if !strings.Contains(out.String(), "Nothing to reset") {
		t.Errorf("expected a no-op note, got:\n%s", out.String())
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
