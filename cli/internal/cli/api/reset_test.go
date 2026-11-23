package api

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"
	"strconv"
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
		if len(args) == 0 || args[0] != "/usr/bin/sudo" {
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
		if !strings.HasPrefix(got, "/usr/bin/sudo -u alice-local-agent -- /bin/rm -rf -- ") {
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
		want := []string{"/usr/bin/sudo", "/bin/rm", "-rf", "--", home}
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

func TestResetCommandsUseTrustedAbsoluteExecutables(t *testing.T) {
	bin := t.TempDir()
	marker := filepath.Join(t.TempDir(), "planted-sudo-ran")
	planted := filepath.Join(bin, "sudo")
	script := "#!/bin/sh\n/usr/bin/printf planted >> \"$RESET_TEST_MARKER\"\n"
	if err := os.WriteFile(planted, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+"/usr/bin:/bin")

	plan := resetPlan{
		user:          "alice-local-agent",
		homeDir:       "/Users/Shared/alice-local-agent",
		operator:      "alice",
		accountExists: true,
		acls: []aclRemoval{
			{dir: "/Users/alice/projects/api", present: true},
			{dir: "/Users/alice", traverse: true, present: true},
		},
	}
	keepHomeCommands := buildResetSteps(plan, false)
	deleteHomeCommands := buildResetSteps(plan, true)
	commands := make([]localagent.AccountStep, 0, len(keepHomeCommands)+len(deleteHomeCommands))
	commands = append(commands, keepHomeCommands...)
	commands = append(commands, deleteHomeCommands...)
	if len(commands) < 10 {
		t.Fatalf("reset command coverage is incomplete: %d steps", len(commands))
	}
	for _, step := range commands {
		cmd := step.Cmd
		if cmd == nil {
			t.Errorf("%s has no command", step.What)
			continue
		}
		if cmd.Path != "/usr/bin/sudo" || len(cmd.Args) == 0 || cmd.Args[0] != "/usr/bin/sudo" {
			t.Errorf("%s resolves initial sudo through %q with argv %v", step.What, cmd.Path, cmd.Args)
		}
		assertResetFixedUtilityEnvironment(t, step.What, cmd)
		assertResetAbsoluteUtilities(t, step.What, cmd)
		if cmd.Path == planted {
			cmd.Env = append(os.Environ(), "RESET_TEST_MARKER="+marker)
			if err := cmd.Run(); err != nil {
				t.Fatalf("planted sudo replay for %s: %v", step.What, err)
			}
		}
	}
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Errorf("a reset builder executed the planted sudo; marker stat error = %v", err)
	}
}

func assertResetFixedUtilityEnvironment(t *testing.T, what string, cmd *exec.Cmd) {
	t.Helper()
	const fixedPATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
	var paths []string
	for _, entry := range cmd.Env {
		if value, ok := strings.CutPrefix(entry, "PATH="); ok {
			paths = append(paths, value)
		}
	}
	if len(paths) != 1 || paths[0] != fixedPATH {
		t.Errorf("%s PATH entries = %v, want exactly %q", what, paths, fixedPATH)
		return
	}
	for _, entry := range strings.Split(paths[0], string(os.PathListSeparator)) {
		if !filepath.IsAbs(entry) {
			t.Errorf("%s PATH contains relative entry %q", what, entry)
		}
	}
}

func assertResetAbsoluteUtilities(t *testing.T, what string, cmd *exec.Cmd) {
	t.Helper()
	utilityIndex := 1
	if len(cmd.Args) > 1 && cmd.Args[1] == "-u" {
		for i, arg := range cmd.Args {
			if arg == "--" {
				utilityIndex = i + 1
				break
			}
		}
	}
	if len(cmd.Args) <= utilityIndex || !filepath.IsAbs(cmd.Args[utilityIndex]) {
		t.Errorf("%s does not use an absolute privileged utility: %v", what, cmd.Args)
		return
	}
	joined := strings.Join(cmd.Args, " ")
	var required []string
	switch {
	case strings.Contains(what, "terminate all processes"):
		required = []string{"/usr/bin/pkill"}
	case strings.Contains(what, "verify no processes"):
		required = []string{"/usr/bin/pgrep"}
	case strings.Contains(what, "read/write grant") && runtime.GOOS == "darwin":
		required = []string{"/usr/bin/find", "-exec /bin/chmod"}
	case strings.Contains(what, "read/write grant"):
		required = []string{"/usr/bin/setfacl"}
	case strings.Contains(what, "traverse grant") && runtime.GOOS == "darwin":
		required = []string{" /bin/chmod -a"}
	case strings.Contains(what, "traverse grant"):
		required = []string{"/usr/bin/setfacl"}
	case strings.Contains(what, "sudoers drop-in"):
		required = []string{"/usr/bin/mktemp", "/usr/bin/grep", "/usr/sbin/visudo", "/usr/bin/install", "/bin/rm"}
	case strings.Contains(what, "delete the Unix account") && runtime.GOOS == "darwin":
		required = []string{"/usr/bin/dscl"}
	case strings.Contains(what, "delete the Unix account"):
		required = []string{"/usr/sbin/userdel"}
	}
	for _, utility := range required {
		if !strings.Contains(joined, utility) {
			t.Errorf("%s lacks absolute utility %q: %s", what, utility, joined)
		}
	}
}

func TestSurveyResetUsesTrustedAbsoluteProbes(t *testing.T) {
	me, err := user.Current()
	if err != nil {
		t.Fatal(err)
	}
	root := resolvedTempDir(t)
	grant := filepath.Join(root, "grant")
	if err := os.Mkdir(grant, 0o700); err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS == "darwin" {
		// A real ACE on a temporary directory distinguishes the trusted ls result
		// from a planted probe that reports failure and suppresses the revoke.
		output, err := exec.Command("/bin/chmod", "+a", "user:"+me.Username+" allow execute", grant).CombinedOutput()
		if err != nil {
			t.Fatalf("grant temporary ACL: %v\n%s", err, output)
		}
	}

	bin := t.TempDir()
	marker := filepath.Join(t.TempDir(), "planted-probes-ran")
	t.Setenv("WP0501_PROBE_MARKER", marker)
	probe := "ls"
	if runtime.GOOS != "darwin" {
		probe = "getfacl"
	}
	for _, name := range []string{"id", probe} {
		script := "#!/bin/sh\n/usr/bin/printf '%s\\n' \"$0\" >> \"$WP0501_PROBE_MARKER\"\nexit 1\n"
		if err := os.WriteFile(filepath.Join(bin, name), []byte(script), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+"/usr/bin:/bin")

	acct := config.AgentAccount{
		User:           me.Username,
		AccountCreated: true,
		GrantedDirs:    []string{grant},
	}
	plan := surveyReset(context.Background(), "alice", "", acct)
	if !plan.accountExists {
		t.Error("planted id suppressed the live account survey")
	}
	if len(plan.acls) != 1 {
		t.Fatalf("survey ACL count = %d, want 1", len(plan.acls))
	}
	if runtime.GOOS == "darwin" && !plan.acls[0].present {
		t.Error("planted ls suppressed a real temporary ACE")
	}
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Errorf("survey executed a planted probe: %v", err)
	}

	// The account deliberately has neither home nor config location. A truthful
	// account survey must therefore retain process and account teardown steps.
	steps := buildResetSteps(plan, false)
	for _, what := range []string{"terminate all processes", "delete the Unix account"} {
		found := false
		for _, step := range steps {
			if strings.Contains(step.What, what) {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("no-location plan omitted %q after planted id", what)
		}
	}
}

func TestSurveyResetProbeFailuresPreserveNoLocationRecord(t *testing.T) {
	for _, tc := range []struct {
		name   string
		survey func(*testing.T) (context.Context, config.AgentAccount)
	}{
		{
			name: "account probe startup",
			survey: func(t *testing.T) (context.Context, config.AgentAccount) {
				t.Helper()
				ctx, cancel := context.WithCancel(context.Background())
				cancel()
				return ctx, config.AgentAccount{User: "wp0501-no-such-agent", AccountCreated: true}
			},
		},
		{
			name: "ACL probe unknown failure",
			survey: func(t *testing.T) (context.Context, config.AgentAccount) {
				t.Helper()
				if os.Geteuid() == 0 {
					t.Skip("permission-denied fixture requires an unprivileged user")
				}
				root := resolvedTempDir(t)
				grant := filepath.Join(root, "grant")
				if err := os.Mkdir(grant, 0o700); err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(root, 0); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = os.Chmod(root, 0o700) })
				return context.Background(), config.AgentAccount{
					User: "wp0501-no-such-agent", AccountCreated: true, GrantedDirs: []string{grant},
				}
			},
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			withXDG(t)
			ctx, acct := tc.survey(t)
			out := &bytes.Buffer{}
			a := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}}
			if err := errSeedAgentState(a.Paths, acct); err != nil {
				t.Fatal(err)
			}
			plan := surveyReset(ctx, "alice", "", acct)
			steps := buildResetSteps(plan, false)
			if len(steps) == 0 {
				t.Fatal("test requires a potential mutation step")
			}
			marker := filepath.Join(t.TempDir(), "mutation-ran")
			for i := range steps {
				steps[i].Cmd = exec.Command("/bin/sh", "-c", `printf ran >> "$1"`, "sh", marker)
			}
			err := a.execResetSteps(a.Paths, plan, false, steps)
			if err == nil || !strings.Contains(err.Error(), "survey") {
				t.Errorf("unknown survey outcome error = %v, want survey refusal", err)
			}
			if _, statErr := os.Stat(marker); !os.IsNotExist(statErr) {
				t.Errorf("reset mutated after unknown survey outcome: %v", statErr)
			}
			if strings.Contains(out.String(), "Reset complete") {
				t.Errorf("unknown survey outcome emitted success:\n%s", out.String())
			}
			st, loadErr := config.LoadAgentState(a.Paths)
			if loadErr != nil {
				t.Fatal(loadErr)
			}
			if _, ok := st.AgentAccount(); !ok {
				t.Error("unknown survey outcome cleared the account record")
			}
		})
	}
}

func TestExecResetStepsRefusesManagedHomeSymlinks(t *testing.T) {
	for _, tc := range []struct {
		name       string
		what       string
		deleteHome bool
		leafLink   bool
	}{
		{name: "delete through nested parent", what: "delete the agent's home", deleteHome: true},
		{name: "re-own through nested parent", what: "re-own the agent's home"},
		{name: "delete symlinked home", what: "delete the agent's home", deleteHome: true, leafLink: true},
		{name: "re-own symlinked home", what: "re-own the agent's home", leafLink: true},
		{name: "identity through nested parent", what: "remove the agent's jentic identity"},
		{name: "seeded config through nested parent", what: "remove seeded agent/provider config"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			withXDG(t)
			root := resolvedTempDir(t)
			outside := resolvedTempDir(t)
			if err := os.MkdirAll(filepath.Join(outside, "agent"), 0o700); err != nil {
				t.Fatal(err)
			}
			home := filepath.Join(root, "nested", "agent")
			if tc.leafLink {
				home = filepath.Join(root, "agent")
				if err := os.Symlink(filepath.Join(outside, "agent"), home); err != nil {
					t.Fatal(err)
				}
			} else if err := os.Symlink(outside, filepath.Join(root, "nested")); err != nil {
				t.Fatal(err)
			}

			plan := resetPlan{
				user:          "alice-local-agent",
				homeDir:       home,
				operator:      "alice",
				accountExists: !tc.deleteHome,
			}
			var selected *localagent.AccountStep
			for _, step := range buildResetSteps(plan, tc.deleteHome) {
				if strings.Contains(step.What, tc.what) {
					stepCopy := step
					selected = &stepCopy
					break
				}
			}
			if selected == nil {
				t.Fatalf("no reset step matching %q", tc.what)
			}
			marker := filepath.Join(t.TempDir(), "privileged-command-ran")
			selected.Cmd = exec.Command("/bin/sh", "-c", `printf ran > "$1"`, "sh", marker)
			out := &bytes.Buffer{}
			a := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}}
			if err := errSeedAgentState(a.Paths, config.AgentAccount{User: plan.user, HomeDir: home}); err != nil {
				t.Fatal(err)
			}
			err := a.execResetSteps(a.Paths, plan, tc.deleteHome, []localagent.AccountStep{*selected})
			if err == nil || !strings.Contains(err.Error(), "safety check") {
				t.Errorf("managed-home symlink reset error = %v, want fatal safety check", err)
			}
			if _, statErr := os.Stat(marker); !os.IsNotExist(statErr) {
				t.Errorf("privileged command ran through managed-home symlink: %v", statErr)
			}
			if strings.Contains(out.String(), "Reset complete") {
				t.Errorf("managed-home symlink refusal emitted success:\n%s", out.String())
			}
			st, loadErr := config.LoadAgentState(a.Paths)
			if loadErr != nil {
				t.Fatal(loadErr)
			}
			if _, ok := st.AgentAccount(); !ok {
				t.Error("managed-home symlink refusal cleared the account record")
			}
		})
	}
}

func TestExecResetStepsAllowsPhysicalManagedHome(t *testing.T) {
	for _, tc := range []struct {
		name       string
		what       string
		deleteHome bool
	}{
		{name: "delete", what: "delete the agent's home", deleteHome: true},
		{name: "re-own", what: "re-own the agent's home"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			withXDG(t)
			root := resolvedTempDir(t)
			home := filepath.Join(root, "nested", "agent")
			if err := os.MkdirAll(home, 0o700); err != nil {
				t.Fatal(err)
			}
			plan := resetPlan{user: "alice-local-agent", homeDir: home, operator: "alice", accountExists: !tc.deleteHome}
			var selected *localagent.AccountStep
			for _, step := range buildResetSteps(plan, tc.deleteHome) {
				if strings.Contains(step.What, tc.what) {
					stepCopy := step
					selected = &stepCopy
					break
				}
			}
			if selected == nil {
				t.Fatalf("no reset step matching %q", tc.what)
			}
			marker := filepath.Join(t.TempDir(), "command-ran")
			selected.Cmd = exec.Command("/bin/sh", "-c", `printf ran > "$1"`, "sh", marker)
			a := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}}
			if err := errSeedAgentState(a.Paths, config.AgentAccount{User: plan.user, HomeDir: home}); err != nil {
				t.Fatal(err)
			}
			if err := a.execResetSteps(a.Paths, plan, tc.deleteHome, []localagent.AccountStep{*selected}); err != nil {
				t.Fatalf("physical managed home was refused: %v", err)
			}
			if _, err := os.Stat(marker); err != nil {
				t.Errorf("safe command did not run: %v", err)
			}
		})
	}
}

func TestSurveyResetRefusesSymlinkedManagedHome(t *testing.T) {
	root := resolvedTempDir(t)
	outside := resolvedTempDir(t)
	if err := os.MkdirAll(filepath.Join(outside, "agent"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "nested")); err != nil {
		t.Fatal(err)
	}
	home := filepath.Join(root, "nested", "agent")
	plan := surveyReset(context.Background(), "alice", "", config.AgentAccount{
		User:    "alice-local-agent",
		HomeDir: home,
	})
	if len(plan.skipped) == 0 {
		t.Fatal("survey accepted removal paths below a symlinked managed-home parent")
	}
}

func resolvedTempDir(t *testing.T) string {
	t.Helper()
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return root
}

func TestExecResetStepsACLFailuresAreFatal(t *testing.T) {
	for _, tc := range []struct {
		name       string
		script     string
		startup    bool
		wantStderr string
	}{
		{name: "command startup", startup: true},
		{name: "status 127", script: "exit 127"},
		{name: "sudo denial", script: `printf 'sudo: a password is required\n' >&2; exit 1`, wantStderr: "password is required"},
		{name: "missing utility", script: `/definitely/missing-acl-utility; exit 127`, wantStderr: "missing-acl-utility"},
		{name: "recursive walk", script: `printf 'chmod: /grant/file: No such file or directory\n' >&2; exit 1`, wantStderr: "No such file or directory"},
		{name: "unknown exit status", script: `printf "chmod: Entry not found when attempting delete '/grant/file'\n" >&2; exit 2`, wantStderr: "Entry not found"},
		{name: "empty diagnostics", script: "exit 1"},
		{name: "unrecognized diagnostics", script: `printf 'chmod: /grant/file: Operation not permitted\n' >&2; exit 1`, wantStderr: "Operation not permitted"},
		{name: "legacy suffix diagnostic", script: `printf 'chmod: /grant/file: Entry not found\n' >&2; exit 1`, wantStderr: "Entry not found"},
		{name: "incomplete operand", script: `printf 'chmod: No ACL present\n' >&2; exit 1`, wantStderr: "No ACL present"},
		{name: "trailing diagnostic", script: `printf "chmod: No ACL present '/grant/file' trailing\n" >&2; exit 1`, wantStderr: "trailing"},
		{name: "mixed diagnostics", script: `printf "chmod: No ACL present '/grant/a'\nchmod: /grant/b: Operation not permitted\n" >&2; exit 1`, wantStderr: "Operation not permitted"},
		{name: "signal", script: "kill -TERM $$"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			withXDG(t)
			out := &bytes.Buffer{}
			errOut := &bytes.Buffer{}
			a := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: errOut}}
			if err := errSeedAgentState(a.Paths, config.AgentAccount{User: "alice-local-agent"}); err != nil {
				t.Fatal(err)
			}
			failure := exec.Command("/bin/sh", "-c", tc.script)
			if tc.startup {
				failure = exec.Command(filepath.Join(t.TempDir(), "missing-acl-command"))
			}
			marker := filepath.Join(t.TempDir(), "account-deleted")
			steps := []localagent.AccountStep{
				{What: "remove read/write grant on /grant", Cmd: failure, BestEffort: true},
				{What: "delete the Unix account alice-local-agent", Cmd: exec.Command("/bin/sh", "-c", `printf deleted > "$1"`, "sh", marker)},
			}
			plan := resetPlan{user: "alice-local-agent", accountExists: true}
			err := a.execResetSteps(a.Paths, plan, false, steps)
			if err == nil || !strings.Contains(err.Error(), "read/write grant") {
				t.Errorf("ACL failure result = %v, want fatal grant error", err)
			}
			if _, statErr := os.Stat(marker); !os.IsNotExist(statErr) {
				t.Errorf("account deletion ran after ACL failure: %v", statErr)
			}
			if strings.Contains(out.String(), "Reset complete") {
				t.Errorf("ACL failure emitted success:\n%s", out.String())
			}
			if tc.wantStderr != "" && !strings.Contains(errOut.String(), tc.wantStderr) {
				t.Errorf("ACL diagnostics were discarded: got %q, want substring %q", errOut.String(), tc.wantStderr)
			}
			st, loadErr := config.LoadAgentState(a.Paths)
			if loadErr != nil {
				t.Fatal(loadErr)
			}
			if _, ok := st.AgentAccount(); !ok {
				t.Error("ACL failure cleared the account record")
			}
		})
	}
}

func TestExecResetStepsAllowsOnlyAbsentACEACLDiagnostics(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Skip("macOS chmod absent-ACE diagnostics")
	}
	withXDG(t)
	out := &bytes.Buffer{}
	errOut := &bytes.Buffer{}
	a := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: errOut}}
	if err := errSeedAgentState(a.Paths, config.AgentAccount{User: "alice-local-agent"}); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(t.TempDir(), "cleanup-continued")
	acl := exec.Command("/bin/sh", "-c", `printf "chmod: No ACL present '/grant/a'\nchmod: Entry not found when attempting delete '/grant/b'\n" >&2; exit 1`)
	steps := []localagent.AccountStep{
		{What: "remove read/write grant on /grant", Cmd: acl, BestEffort: true},
		{What: "delete the Unix account alice-local-agent", Cmd: exec.Command("/bin/sh", "-c", `printf continued > "$1"`, "sh", marker)},
	}
	if err := a.execResetSteps(a.Paths, resetPlan{user: "alice-local-agent"}, false, steps); err != nil {
		t.Fatalf("exclusively absent-ACE diagnostics must be benign: %v", err)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Errorf("cleanup did not continue after absent-ACE diagnostics: %v", err)
	}
	if !strings.Contains(out.String(), "Reset complete") {
		t.Errorf("benign ACL outcome did not complete reset:\n%s", out.String())
	}
	for _, diagnostic := range []string{"Entry not found", "No ACL present"} {
		if !strings.Contains(errOut.String(), diagnostic) {
			t.Errorf("benign ACL diagnostic %q was discarded: %q", diagnostic, errOut.String())
		}
	}
}

func TestExecResetStepsSudoersFailuresPreserveState(t *testing.T) {
	for _, tc := range []struct {
		name           string
		utility        string
		status         int
		validatePasses bool
	}{
		{name: "mktemp", utility: "/usr/bin/mktemp", status: 42},
		{name: "inspection", utility: "/usr/bin/grep", status: 2},
		{name: "visudo", utility: "/usr/sbin/visudo", status: 42},
		{name: "install", utility: "/usr/bin/install", status: 42, validatePasses: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			withXDG(t)
			root := resolvedTempDir(t)
			target := filepath.Join(root, "jentic-agent")
			original := "alice-local-agent ALL=(alice-local-agent) NOPASSWD: /bin/launch\n" +
				"alice-local-agent-2 ALL=(alice-local-agent-2) NOPASSWD: /bin/sibling\n"
			if err := os.WriteFile(target, []byte(original), 0o440); err != nil {
				t.Fatal(err)
			}

			bin := filepath.Join(root, "bin")
			if err := os.Mkdir(bin, 0o700); err != nil {
				t.Fatal(err)
			}
			fake := writeResetFakeUtility(t, bin, tc.name, tc.status)
			built := localagent.RemoveAgentSudoersCmd("alice-local-agent")
			if len(built.Args) != 4 || built.Args[1] != "/bin/sh" || built.Args[2] != "-c" {
				t.Fatalf("unexpected sudoers command shape: %v", built.Args)
			}
			script := built.Args[3]
			for old, replacement := range map[string]string{
				"/etc/sudoers.d/jentic-agent": target,
				tc.utility:                    fake,
			} {
				if !strings.Contains(script, old) {
					t.Fatalf("sudoers script lacks replacement target %q: %s", old, script)
				}
				script = strings.ReplaceAll(script, old, replacement)
			}
			if tc.validatePasses {
				script = strings.ReplaceAll(script, "/usr/sbin/visudo", writeResetFakeUtility(t, bin, "visudo-ok", 0))
			}
			if strings.Contains(script, "/etc/sudoers") {
				t.Fatalf("replay still names a real sudoers path: %s", script)
			}

			out := &bytes.Buffer{}
			a := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}}
			acct := config.AgentAccount{User: "alice-local-agent", AccountCreated: true}
			if err := errSeedAgentState(a.Paths, acct); err != nil {
				t.Fatal(err)
			}
			accountMarker := filepath.Join(t.TempDir(), "account-deleted")
			steps := []localagent.AccountStep{
				{What: "remove the passwordless-launch sudoers drop-in", Cmd: exec.Command("/bin/sh", "-c", script)},
				{What: "delete the Unix account alice-local-agent", Cmd: exec.Command("/bin/sh", "-c", `printf deleted > "$1"`, "sh", accountMarker)},
			}
			plan := resetPlan{user: acct.User, accountExists: true}
			if err := a.execResetSteps(a.Paths, plan, false, steps); err == nil {
				t.Errorf("%s failure returned success", tc.name)
			}
			contents, err := os.ReadFile(target)
			if err != nil {
				t.Fatalf("read preserved sudoers fixture: %v", err)
			}
			if string(contents) != original {
				t.Errorf("%s failure changed target or sibling rules:\n%s", tc.name, contents)
			}
			if _, statErr := os.Stat(accountMarker); !os.IsNotExist(statErr) {
				t.Errorf("account deletion ran after %s failure: %v", tc.name, statErr)
			}
			if strings.Contains(out.String(), "Reset complete") {
				t.Errorf("%s failure emitted success:\n%s", tc.name, out.String())
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

func writeResetFakeUtility(t *testing.T, dir, name string, status int) string {
	t.Helper()
	path := filepath.Join(dir, name)
	script := "#!/bin/sh\nexit " + strconv.Itoa(status) + "\n"
	if err := os.WriteFile(path, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestExecResetStepsRefusesUnresolvedLegacyConfigDir(t *testing.T) {
	withXDG(t)
	legacyConfig := filepath.Join(t.TempDir(), "legacy-agent-config")
	if err := os.MkdirAll(legacyConfig, 0o700); err != nil {
		t.Fatal(err)
	}
	sentinel := filepath.Join(legacyConfig, "credentials")
	if err := os.WriteFile(sentinel, []byte("retain"), 0o600); err != nil {
		t.Fatal(err)
	}
	acct := config.AgentAccount{
		User:           "wp0501-no-such-agent",
		AccountCreated: true,
		ConfigDir:      legacyConfig,
	}
	out := &bytes.Buffer{}
	a := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: &bytes.Buffer{}}}
	if err := errSeedAgentState(a.Paths, acct); err != nil {
		t.Fatal(err)
	}
	plan := surveyReset(context.Background(), "alice", "", acct)
	steps := buildResetSteps(plan, false)
	if len(steps) == 0 {
		t.Fatal("test requires a potential mutation step")
	}
	marker := filepath.Join(t.TempDir(), "mutation-ran")
	for i := range steps {
		steps[i].Cmd = exec.Command("/bin/sh", "-c", `printf ran >> "$1"`, "sh", marker)
	}
	err := a.execResetSteps(a.Paths, plan, false, steps)
	if err == nil || !strings.Contains(err.Error(), legacyConfig) {
		t.Errorf("unresolved legacy ConfigDir reset error = %v, want location in refusal", err)
	}
	if _, statErr := os.Stat(marker); !os.IsNotExist(statErr) {
		t.Errorf("reset mutated with unresolved legacy ConfigDir: %v", statErr)
	}
	if strings.Contains(out.String(), "Reset complete") {
		t.Errorf("unresolved legacy ConfigDir emitted success:\n%s", out.String())
	}
	if _, statErr := os.Stat(sentinel); statErr != nil {
		t.Errorf("legacy ConfigDir was removed or damaged: %v", statErr)
	}
	st, loadErr := config.LoadAgentState(a.Paths)
	if loadErr != nil {
		t.Fatal(loadErr)
	}
	if _, ok := st.AgentAccount(); !ok {
		t.Error("unresolved legacy ConfigDir reset cleared the account record")
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
