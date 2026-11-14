package api

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/localagent"
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

// TestResetIdentityScrubDoesNotFollowAgentSymlink replays the reset removal
// commands without elevation and pins that an agent-home symlink cannot redirect
// deletion into the operator's config tree.
func TestResetIdentityScrubDoesNotFollowAgentSymlink(t *testing.T) {
	root := t.TempDir()
	home := filepath.Join(root, "agent-home")
	operatorConfig := filepath.Join(root, "operator", ".config")
	for _, dir := range []string{
		home,
		filepath.Join(operatorConfig, "jentic"),
		filepath.Join(operatorConfig, "gcloud"),
	} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink(operatorConfig, filepath.Join(home, ".config")); err != nil {
		t.Fatal(err)
	}

	plan := resetPlan{
		user:          "alice-local-agent",
		homeDir:       home,
		operator:      "alice",
		accountExists: true,
	}
	refused := 0
	for _, step := range buildResetSteps(plan, false) {
		if !strings.Contains(step.What, "jentic identity") &&
			!strings.Contains(step.What, "seeded agent/provider config") {
			continue
		}
		unsafe := false
		for _, path := range step.NoSymlinkPaths {
			if err := localagent.AssertNoSymlinkComponents(step.NoSymlinkHome, path); err != nil {
				refused++
				unsafe = true
				break
			}
		}
		if unsafe {
			continue
		}
		args := step.Cmd.Args
		if len(args) == 0 || args[0] != "sudo" {
			t.Fatalf("removal command is not sudo-fronted: %v", args)
		}
		args = args[1:]
		if args[0] == "-u" {
			args = args[3:]
		}
		cmd := exec.Command(args[0], args[1:]...)
		if output, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("replay %v: %v\n%s", args, err, output)
		}
	}
	if refused == 0 {
		t.Fatal("adversarial replay did not exercise a refused symlinked path")
	}

	for _, dir := range []string{
		filepath.Join(operatorConfig, "jentic"),
		filepath.Join(operatorConfig, "gcloud"),
	} {
		if _, err := os.Stat(dir); err != nil {
			t.Errorf("operator config %s must survive reset removal replay: %v", dir, err)
		}
	}
}

// TestBuildResetStepsKillsAgentFirst pins fail-closed process termination and
// quiescence verification ahead of every privileged filesystem operation.
func TestBuildResetStepsKillsAgentFirst(t *testing.T) {
	steps := buildResetSteps(resetPlan{user: "alice-local-agent", accountExists: true}, false)
	if len(steps) < 2 {
		t.Fatalf("expected termination and quiescence steps, got %v", steps)
	}
	if got := strings.Join(steps[0].Cmd.Args, " "); !strings.Contains(got, "pkill -KILL -u 'alice-local-agent'") {
		t.Errorf("first reset command = %q, want validated pkill", got)
	}
	if got := strings.Join(steps[1].Cmd.Args, " "); !strings.Contains(got, "pgrep -u 'alice-local-agent'") {
		t.Errorf("second reset command = %q, want quiescence pgrep", got)
	}
	for _, step := range steps[:2] {
		if step.BestEffort {
			t.Errorf("process safety step %q must not be best-effort", step.What)
		}
	}
}

// TestBuildResetStepsRunsScrubAsAgentWhenAccountExists keeps deletion within
// the agent's own OS permissions without an elevated subpath fallback.
func TestBuildResetStepsRunsScrubAsAgentWhenAccountExists(t *testing.T) {
	steps := buildResetSteps(resetPlan{
		user:          "alice-local-agent",
		homeDir:       "/Users/Shared/alice-local-agent",
		operator:      "alice",
		accountExists: true,
	}, false)
	var checked int
	for _, step := range steps {
		if !strings.Contains(step.What, "jentic identity") &&
			!strings.Contains(step.What, "seeded agent/provider config") {
			continue
		}
		checked++
		got := strings.Join(step.Cmd.Args, " ")
		if !strings.HasPrefix(got, "sudo -u alice-local-agent -- /bin/rm -rf -- ") {
			t.Errorf("removal must run as the agent: %s", got)
		}
		if step.BestEffort {
			t.Errorf("credential removal %q must fail closed", step.What)
		}
	}
	if checked != 2 {
		t.Fatalf("checked %d removal steps, want identity and seeded-config steps", checked)
	}
}

// TestResetRefusesSymlinkedIdentityDir checks that a refused component is a
// fatal safety gate, preserves the account record, and leaves operator config intact.
func TestResetRefusesSymlinkedIdentityDir(t *testing.T) {
	withXDG(t)
	root := t.TempDir()
	home := filepath.Join(root, "agent-home")
	operatorConfig := filepath.Join(root, "operator", ".config")
	for _, dir := range []string{home, filepath.Join(operatorConfig, "jentic"), filepath.Join(operatorConfig, "gcloud")} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink(operatorConfig, filepath.Join(home, ".config")); err != nil {
		t.Fatal(err)
	}

	plan := surveyReset(context.Background(), "alice", "", config.AgentAccount{
		User:    "alice-local-agent",
		HomeDir: home,
	})
	out := &bytes.Buffer{}
	a := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}}
	if err := errSeedAgentState(a.Paths, config.AgentAccount{User: plan.user, HomeDir: home}); err != nil {
		t.Fatal(err)
	}
	a.printResetPlan(plan)
	if got := out.String(); !strings.Contains(got, "refused: symlinked path component") {
		t.Errorf("reset plan must name the refused path, got:\n%s", got)
	}

	err := a.execAccountReset(a.Paths, plan, false)
	if err == nil || !strings.Contains(err.Error(), "refused removal path") {
		t.Fatalf("reset must abort on the refused component, got %v", err)
	}
	if got := out.String(); strings.Contains(got, "Reset complete") || strings.Contains(got, "were cleared") {
		t.Errorf("incomplete reset emitted credential-clearing success output:\n%s", got)
	}
	st, loadErr := config.LoadAgentState(a.Paths)
	if loadErr != nil {
		t.Fatal(loadErr)
	}
	if _, ok := st.AgentAccount(); !ok {
		t.Error("refused reset must preserve the account record for repair and retry")
	}
	for _, dir := range []string{filepath.Join(operatorConfig, "jentic"), filepath.Join(operatorConfig, "gcloud")} {
		if _, statErr := os.Stat(dir); statErr != nil {
			t.Errorf("operator config %s must survive refused reset: %v", dir, statErr)
		}
	}
}

// TestBuildResetStepsOrderAndHome asserts the load-bearing ordering (processes →
// leaf ACLs → traverse ACLs → identity → home → sudoers → account) and that the home step
// honours the delete flag. Only present ACLs become steps.
func TestBuildResetStepsOrderAndHome(t *testing.T) {
	plan := resetPlan{
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

	// Ordering: terminate processes → leaf-revoke → traverse-revoke → identity dirs → home → sudoers → account.
	// The agent's own identity dirs (exported XDG store + legacy ~/.jentic) are
	// torn down before the home is settled and always (when the home is kept) so
	// credential material never outlives the account and a re-setup can't
	// resurrect a torn-down agent.
	idxKill := indexOfContains(whats, "terminate all processes")
	idxQuiesce := indexOfContains(whats, "verify no processes remain")
	idxLeaf := indexOfContains(whats, "read/write grant on /Users/alice/projects/api")
	idxTraverse := indexOfContains(whats, "traverse grant on /Users/alice")
	idxIdentity := indexOfContains(whats, "remove the agent's jentic identity")
	idxHome := indexOfContains(whats, "re-own the agent's home")
	idxSudoers := indexOfContains(whats, "sudoers drop-in")
	idxAccount := indexOfContains(whats, "delete the Unix account")
	if idxIdentity < 0 {
		t.Errorf("expected the agent identity dirs to be torn down, got:\n%s", joined)
	} else {
		// The step's command must name every identity dir: the exported XDG
		// config/state trees (the reset-scrub gap) and the legacy ~/.jentic.
		idArgs := strings.Join(steps[idxIdentity].Cmd.Args, " ")
		for _, dir := range []string{
			"/Users/Shared/alice-local-agent/.config/jentic",
			"/Users/Shared/alice-local-agent/.local/state/jentic",
			"/Users/Shared/alice-local-agent/.jentic",
		} {
			if !strings.Contains(idArgs, dir) {
				t.Errorf("identity step must remove %s, got args: %s", dir, idArgs)
			}
		}
	}
	if idxKill != 0 || idxQuiesce != 1 || idxLeaf < 0 || idxLeaf >= idxTraverse || idxTraverse >= idxIdentity || idxIdentity >= idxHome || idxHome >= idxSudoers || idxSudoers >= idxAccount {
		t.Fatalf("steps out of order: kill=%d quiesce=%d leaf=%d traverse=%d identity=%d home=%d sudoers=%d account=%d (%v)",
			idxKill, idxQuiesce, idxLeaf, idxTraverse, idxIdentity, idxHome, idxSudoers, idxAccount, whats)
	}
	if idxHome > 0 && !strings.Contains(steps[idxHome-1].What, "verify no processes remain") {
		t.Errorf("quiescence must be rechecked immediately before re-owning: %v", whats)
	}

	// Only per-entry ACL revocation failures are benign. Process safety,
	// credential cleanup, and home disposition all abort on failure.
	for _, s := range steps {
		wantBestEffort := strings.Contains(s.What, "read/write grant on") ||
			strings.Contains(s.What, "traverse grant on")
		if s.BestEffort != wantBestEffort {
			t.Errorf("step %q BestEffort=%v, want %v", s.What, s.BestEffort, wantBestEffort)
		}
	}

	// The seeded-config scrub runs on the keep path, after the identity removal and
	// before the home is settled (re-owned), so a live seeded key is purged from
	// the kept tree.
	idxScrub := indexOfContains(whats, "seeded agent/provider config")
	if idxScrub < 0 || idxScrub <= idxIdentity || idxScrub >= idxHome {
		t.Errorf("seeded-config scrub must run after identity and before home settle: scrub=%d identity=%d home=%d (%v)",
			idxScrub, idxIdentity, idxHome, whats)
	}

	// Delete: the home step deletes instead of re-owning.
	delSteps := buildResetSteps(plan, true)
	var delBuilder strings.Builder
	for _, s := range delSteps {
		delBuilder.WriteString(s.What)
		delBuilder.WriteString("\n")
	}
	delJoined := delBuilder.String()
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
	// Likewise the seeded-config scrub is only for a KEPT home; the delete rm covers it.
	if strings.Contains(delJoined, "seeded agent/provider config") {
		t.Errorf("delete-home run must not emit a seeded-config scrub (the home rm covers it), got:\n%s", delJoined)
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

// TestBuildResetStepsOrphanKeepHomeHasNoMutations refuses an orphaned kept
// home because no run-as identity or process-quiescence proof is available.
func TestBuildResetStepsOrphanKeepHomeHasNoMutations(t *testing.T) {
	steps := buildResetSteps(resetPlan{
		user:     "alice-local-agent",
		homeDir:  "/Users/Shared/alice-local-agent",
		operator: "alice",
	}, false)
	if len(steps) != 0 {
		t.Errorf("orphaned keep-home reset must not schedule mutations: %v", steps)
	}
}

// TestBuildResetStepsOrphanDeleteHomeUsesOnlyWholeHomeRM permits only physical
// whole-home deletion for an orphaned home, never root subpath removal or re-owning.
func TestBuildResetStepsOrphanDeleteHomeUsesOnlyWholeHomeRM(t *testing.T) {
	home := "/Users/Shared/alice-local-agent"
	steps := buildResetSteps(resetPlan{user: "alice-local-agent", homeDir: home, operator: "alice"}, true)
	var homeSteps int
	for _, step := range steps {
		what := step.What
		if strings.Contains(what, "jentic identity") || strings.Contains(what, "seeded agent/provider") ||
			strings.Contains(what, "re-own") || strings.Contains(what, "processes") {
			t.Errorf("orphan delete-home scheduled forbidden step %q", what)
		}
		if !strings.Contains(what, "delete the agent's home") {
			continue
		}
		homeSteps++
		want := []string{"sudo", "/bin/rm", "-rf", "--", home}
		if strings.Join(step.Cmd.Args, "\x00") != strings.Join(want, "\x00") {
			t.Errorf("orphan home deletion = %v, want %v", step.Cmd.Args, want)
		}
		if step.BestEffort {
			t.Error("whole-home deletion must fail closed")
		}
	}
	if homeSteps != 1 {
		t.Errorf("whole-home deletion steps = %d, want 1", homeSteps)
	}
}

func TestExecAccountResetOrphanKeepHomeRefusesBeforeMutation(t *testing.T) {
	withXDG(t)
	out := &bytes.Buffer{}
	a := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}}
	acct := config.AgentAccount{User: "alice-local-agent", HomeDir: "/Users/Shared/alice-local-agent"}
	if err := errSeedAgentState(a.Paths, acct); err != nil {
		t.Fatal(err)
	}
	plan := resetPlan{user: acct.User, homeDir: acct.HomeDir, operator: "alice", accountExists: false}
	err := a.execAccountReset(a.Paths, plan, false)
	if err == nil || !strings.Contains(err.Error(), "orphaned agent home") {
		t.Fatalf("orphaned keep-home reset error = %v", err)
	}
	if strings.Contains(out.String(), "•") || strings.Contains(out.String(), "Reset complete") {
		t.Errorf("orphaned keep-home reset mutated or reported success:\n%s", out.String())
	}
	st, loadErr := config.LoadAgentState(a.Paths)
	if loadErr != nil {
		t.Fatal(loadErr)
	}
	if _, ok := st.AgentAccount(); !ok {
		t.Error("orphaned keep-home refusal must preserve the account record")
	}
}

func TestExecResetStepsImmediateSafetyCheckFailureIsAlwaysFatal(t *testing.T) {
	withXDG(t)
	home := filepath.Join(t.TempDir(), "home")
	outside := filepath.Join(t.TempDir(), "outside")
	if err := os.MkdirAll(home, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(outside, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(home, ".config")); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(t.TempDir(), "command-ran")
	out := &bytes.Buffer{}
	a := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}}
	if err := errSeedAgentState(a.Paths, config.AgentAccount{User: "alice-local-agent", HomeDir: home}); err != nil {
		t.Fatal(err)
	}
	steps := []localagent.AccountStep{{
		What:           "remove seeded agent/provider config from the kept home",
		Cmd:            exec.Command("sh", "-c", `printf ran > "$1"`, "sh", marker),
		BestEffort:     true,
		NoSymlinkHome:  home,
		NoSymlinkPaths: []string{filepath.Join(home, ".config", "gcloud")},
	}}
	err := a.execResetSteps(a.Paths, resetPlan{user: "alice-local-agent", homeDir: home}, false, steps)
	if err == nil || !strings.Contains(err.Error(), "safety check") {
		t.Fatalf("immediate component-check error = %v, want fatal safety-check error", err)
	}
	if _, statErr := os.Stat(marker); !os.IsNotExist(statErr) {
		t.Errorf("command ran despite failed pre-command safety check: %v", statErr)
	}
	if strings.Contains(out.String(), "Reset complete") || strings.Contains(out.String(), "were cleared") {
		t.Errorf("failed safety check emitted success output:\n%s", out.String())
	}
	st, loadErr := config.LoadAgentState(a.Paths)
	if loadErr != nil {
		t.Fatal(loadErr)
	}
	if _, ok := st.AgentAccount(); !ok {
		t.Error("failed safety check must preserve the account record")
	}
}

func TestExecResetStepsFailureStopsAccountDeletionAndPreservesRecord(t *testing.T) {
	for _, tc := range []struct {
		name       string
		what       string
		deleteHome bool
	}{
		{name: "process termination", what: "terminate all processes for agent account"},
		{name: "quiescence verification", what: "verify no processes remain for agent account"},
		{name: "identity removal", what: "remove the agent's jentic identity"},
		{name: "seeded config scrub", what: "remove seeded agent/provider config"},
		{name: "home re-own", what: "re-own the agent's home"},
		{name: "home deletion", what: "delete the agent's home", deleteHome: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			withXDG(t)
			out := &bytes.Buffer{}
			a := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}}
			home := "/Users/Shared/alice-local-agent"
			if err := errSeedAgentState(a.Paths, config.AgentAccount{User: "alice-local-agent", HomeDir: home}); err != nil {
				t.Fatal(err)
			}
			marker := filepath.Join(t.TempDir(), "account-deleted")
			steps := []localagent.AccountStep{
				{What: tc.what, Cmd: exec.Command("sh", "-c", "exit 24")},
				{What: "delete the Unix account alice-local-agent", Cmd: exec.Command("sh", "-c", `printf deleted > "$1"`, "sh", marker)},
			}
			plan := resetPlan{user: "alice-local-agent", homeDir: home, operator: "alice", accountExists: true}
			err := a.execResetSteps(a.Paths, plan, tc.deleteHome, steps)
			if err == nil || !strings.Contains(err.Error(), tc.what) {
				t.Fatalf("failed step error = %v, want context for %q", err, tc.what)
			}
			if _, statErr := os.Stat(marker); !os.IsNotExist(statErr) {
				t.Errorf("account deletion ran after %s failed: %v", tc.name, statErr)
			}
			if strings.Contains(out.String(), "Reset complete") || strings.Contains(out.String(), "were cleared") {
				t.Errorf("incomplete cleanup emitted success output:\n%s", out.String())
			}
			st, loadErr := config.LoadAgentState(a.Paths)
			if loadErr != nil {
				t.Fatal(loadErr)
			}
			if _, ok := st.AgentAccount(); !ok {
				t.Errorf("%s failure cleared the account record", tc.name)
			}
		})
	}
}

// TestResetRunsAsOperator confirms reset runs as the operator without EUID 0 while
// individual teardown steps are sudo-fronted. With a configured account, reset
// emits the "(requires sudo)" notice and renders the plan before the
// non-interactive-without-force guard stops execution prior to teardown, with no
// "must run as root" error.
func TestResetRunsAsOperator(t *testing.T) {
	withXDG(t)
	out := &bytes.Buffer{}
	app := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}}
	err := errSeedAgentState(app.Paths, config.AgentAccount{User: "alice-local-agent", AccountCreated: true, Enabled: true})
	if err != nil {
		t.Fatalf("seed agent state: %v", err)
	}

	err = app.resetE(context.Background(), &resetOptions{})
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

// seedIdentityState creates an XDG config tree, state tree, and a legacy
// V1 profiles dir (plus MIGRATED marker), so a full reset has identity state to
// wipe in all three places.
func seedIdentityState(t *testing.T, app *app) (configDir, stateDir, legacyProfiles string) {
	t.Helper()
	configDir, err := sdkconfig.ConfigDir()
	if err != nil {
		t.Fatal(err)
	}
	stateDir, err = sdkconfig.StateDir()
	if err != nil {
		t.Fatal(err)
	}
	for _, d := range []string{configDir, stateDir} {
		if err := os.MkdirAll(d, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(configDir, "config.yaml"), []byte("active_context: work\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	legacyProfiles = filepath.Join(app.Paths.ProfilesDir(), "work")
	if err := os.MkdirAll(legacyProfiles, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(app.Paths.Dir(), "MIGRATED"), []byte("x\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return configDir, stateDir, legacyProfiles
}

// TestResetFullWipesIdentityState confirms a bare `jentic reset --force` (no
// account) is a clean slate: it removes the config/state trees, the legacy
// profiles dir, and the MIGRATED marker.
func TestResetFullWipesIdentityState(t *testing.T) {
	withXDG(t)
	out := &bytes.Buffer{}
	app := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}}
	configDir, stateDir, legacyProfiles := seedIdentityState(t, app)

	// No account configured: a valid identity-state-only clean slate.
	if err := app.resetE(context.Background(), &resetOptions{force: true}); err != nil {
		t.Fatalf("resetE: %v", err)
	}

	for _, gone := range []string{configDir, stateDir, legacyProfiles, filepath.Join(app.Paths.Dir(), "MIGRATED")} {
		if _, err := os.Stat(gone); !os.IsNotExist(err) {
			t.Errorf("%s should be removed (err=%v)", gone, err)
		}
	}
}

// TestResetConfigRequiresForceNonInteractive confirms a full reset is not run
// non-interactively without --force. Tests run with a non-terminal stdin, so a
// bare `jentic reset` with identity state present must hit the single
// whole-slate guard — and the state must survive the refusal.
func TestResetConfigRequiresForceNonInteractive(t *testing.T) {
	withXDG(t)
	app := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}}
	configDir, _, legacyProfiles := seedIdentityState(t, app)

	err := app.resetE(context.Background(), &resetOptions{})
	if err == nil || !strings.Contains(err.Error(), "without --force") {
		t.Fatalf("expected a non-interactive --force guard, got %v", err)
	}
	for _, kept := range []string{configDir, legacyProfiles} {
		if _, serr := os.Stat(kept); serr != nil {
			t.Errorf("%s should survive the refusal: %v", kept, serr)
		}
	}
}

// TestResetNothingToDo is a friendly no-op when there is no account and no
// identity state to remove.
func TestResetNothingToDo(t *testing.T) {
	withXDG(t)
	out := &bytes.Buffer{}
	app := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}}
	if err := app.resetE(context.Background(), &resetOptions{force: true}); err != nil {
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

// errSeedAgentState records an agent account in the XDG agent state (the tests
// point XDG_CONFIG_HOME at a temp dir via withXDG).
func errSeedAgentState(paths config.Paths, acct config.AgentAccount) error {
	_, err := config.MutateAgentState(paths, func(s *config.AgentState) error {
		s.Agent = &acct
		return nil
	})
	return err
}
