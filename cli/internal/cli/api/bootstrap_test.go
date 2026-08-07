package api

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// bootstrapServer is a fake control plane: /register always succeeds, and
// /oauth/token returns a pending 400 for the first pendingPolls calls, then a
// live token pair — modelling the human approving the agent mid-wait. The
// returned counter records how many times /register was hit, so tests can
// assert registration did (or did not) happen.
func bootstrapServer(t *testing.T, pendingPolls int32) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	var polls atomic.Int32
	var registers atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/register":
			registers.Add(1)
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"client_id":"agnt_boot","status":"pending","registration_access_token":"rat_1"}`))
		case "/oauth/token":
			if polls.Add(1) <= pendingPolls {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusBadRequest)
				_, _ = w.Write([]byte(`{"error":"invalid_grant","detail":"agent pending approval"}`))
				return
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"access_token":"acc_live","refresh_token":"ref_live","token_type":"Bearer","expires_in":3600}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, &registers
}

// fastPoll shrinks the approval poll cadence to milliseconds for the duration
// of a test so the pending-path cases assert behaviour without real wall-clock
// seconds. The originals are restored on cleanup.
func fastPoll(t *testing.T) {
	t.Helper()
	oi, om, os := pollInitialDelay, pollMaxDelay, pollDelayStep
	pollInitialDelay = 2 * time.Millisecond
	pollMaxDelay = 5 * time.Millisecond
	pollDelayStep = 1 * time.Millisecond
	t.Cleanup(func() {
		pollInitialDelay, pollMaxDelay, pollDelayStep = oi, om, os
	})
}

func TestBootstrapEndToEnd(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, _ := bootstrapServer(t, 0) // approved immediately

	app := testApp(t)
	out := new(bytes.Buffer)
	app.Out = out

	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"bootstrap",
		"--profile", "demo",
		"--base-url", srv.URL,
		"--operator", "generic",
		"--scope", "user",
		"--timeout", "5s",
		"--yes",
	})
	if err := root.Execute(); err != nil {
		t.Fatalf("bootstrap: %v\nout:\n%s", err, out.String())
	}

	// Profile activated.
	cfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.DefaultProfile != "demo" {
		t.Errorf("default profile = %q, want demo", cfg.DefaultProfile)
	}

	// Tokens persisted.
	tokensPath := filepath.Join(app.Paths.Root, "profiles", "demo", "tokens.json")
	if _, statErr := os.Stat(tokensPath); statErr != nil {
		t.Errorf("expected tokens at %s: %v", tokensPath, statErr)
	}

	// Skill written into the generic user-scope target (~/AGENTS.md).
	skillPath := filepath.Join(home, "AGENTS.md")
	body, readErr := os.ReadFile(skillPath)
	if readErr != nil {
		t.Fatalf("expected skill at %s: %v", skillPath, readErr)
	}
	if !strings.Contains(string(body), "BEGIN JENTIC MANAGED SKILL: jentic") {
		t.Errorf("skill file missing named managed block:\n%s", body)
	}
	// Bootstrap installs the full shipped set by default.
	for _, name := range skillgen.BundledNames() {
		if !strings.Contains(string(body), "BEGIN JENTIC MANAGED SKILL: "+name) {
			t.Errorf("bootstrap should install the full set; missing %s block:\n%s", name, body)
		}
	}

	got := out.String()
	for _, want := range []string{"Registered", "agnt_boot", "You're ready", "demo"} {
		if !strings.Contains(got, want) {
			t.Errorf("output missing %q:\n%s", want, got)
		}
	}
	// Approved on the first mint: no approval banner or waiting message.
	if strings.Contains(got, "Approve this agent") || strings.Contains(got, "Waiting for approval") {
		t.Errorf("already-approved agent should not print an approval/wait banner:\n%s", got)
	}
}

func TestBootstrapWaitsThenApproves(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	fastPoll(t)
	srv, _ := bootstrapServer(t, 2) // two pending polls before approval

	app := testApp(t)
	out := new(bytes.Buffer)
	app.Out = out

	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"bootstrap",
		"--profile", "demo",
		"--base-url", srv.URL,
		"--skip-skill",
		"--timeout", "30s",
		"--yes",
	})
	if err := root.Execute(); err != nil {
		t.Fatalf("bootstrap: %v\nout:\n%s", err, out.String())
	}

	got := out.String()
	if !strings.Contains(got, "Approve this agent") {
		t.Errorf("expected the approval banner while pending:\n%s", got)
	}
	if !strings.Contains(got, "resume later with `jentic bootstrap`") {
		t.Errorf("waiting hint should point back at bootstrap, not register:\n%s", got)
	}
	if !strings.Contains(got, "Agent approved") {
		t.Errorf("expected approval confirmation:\n%s", got)
	}
	// --skip-skill: no AGENTS.md should be created.
	if _, statErr := os.Stat(filepath.Join(home, "AGENTS.md")); statErr == nil {
		t.Errorf("--skip-skill should not write a skill file")
	}
}

// TestBootstrapSelectionErrorBeforeRegister proves operator selection is
// validated before any irreversible side effect: a non-interactive run where
// no operators are given AND none are detected must fail without registering
// an agent or activating a profile. (With detected operators, the run
// degrades to them instead — #755.)
func TestBootstrapSelectionErrorBeforeRegister(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, registers := bootstrapServer(t, 0)

	app := testApp(t)
	stubDetect(t, app, home, t.TempDir()) // nothing detected
	app.Out = new(bytes.Buffer)

	root := newAPIRootCmd(app.App)
	root.SetOut(app.Out)
	root.SetErr(new(bytes.Buffer))
	// --yes with no --operator/--all and no TTY: cannot resolve targets.
	root.SetArgs([]string{
		"bootstrap",
		"--profile", "demo",
		"--base-url", srv.URL,
		"--timeout", "5s",
		"--yes",
	})
	err := root.Execute()
	if err == nil {
		t.Fatalf("expected an error when no operators can be resolved")
	}
	if !strings.Contains(err.Error(), "no operators") {
		t.Errorf("error = %v, want a 'no operators' selection error", err)
	}
	if !strings.Contains(err.Error(), "--skip-skill") {
		t.Errorf("bootstrap's error should name --skip-skill as the identity-only escape hatch: %v", err)
	}
	if n := registers.Load(); n != 0 {
		t.Errorf("registered %d times before the selection error; want 0 (no side effects)", n)
	}
	if _, statErr := os.Stat(filepath.Join(app.Paths.Root, "profiles", "demo", "tokens.json")); statErr == nil {
		t.Errorf("no tokens should be persisted when selection fails up front")
	}
	cfg, _ := config.Load(app.Paths)
	if cfg.DefaultProfile == "demo" {
		t.Errorf("profile must not be activated when selection fails up front")
	}
}

// TestBootstrapSkipSkillRejectsInvalidScope pins that flag validation is not
// silently skipped on paths a flag doesn't apply to: `--skip-skill` with a
// mistyped --scope must error before any registration side effect.
func TestBootstrapSkipSkillRejectsInvalidScope(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, registers := bootstrapServer(t, 0)

	app := testApp(t)
	app.Out = new(bytes.Buffer)
	root := newAPIRootCmd(app.App)
	root.SetOut(app.Out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"bootstrap",
		"--profile", "demo",
		"--base-url", srv.URL,
		"--skip-skill",
		"--scope", "everywhere",
		"--yes",
	})
	err := root.Execute()
	if err == nil || !strings.Contains(err.Error(), "invalid --scope") {
		t.Fatalf("expected an invalid --scope error, got %v", err)
	}
	if n := registers.Load(); n != 0 {
		t.Errorf("registered %d times despite an invalid flag; want 0", n)
	}
}

func TestBootstrapDryRunWritesNothing(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	// No server needed: dry-run must not hit the network.
	app := testApp(t)
	out := new(bytes.Buffer)
	app.Out = out

	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"bootstrap",
		"--profile", "demo",
		"--base-url", "http://127.0.0.1:0",
		"--operator", "generic",
		"--scope", "user",
		"--yes",
		"--dry-run",
	})
	if err := root.Execute(); err != nil {
		t.Fatalf("bootstrap dry-run: %v\nout:\n%s", err, out.String())
	}

	if _, statErr := os.Stat(filepath.Join(home, "AGENTS.md")); statErr == nil {
		t.Errorf("dry-run should not write a skill file")
	}
	if _, statErr := os.Stat(filepath.Join(app.Paths.Root, "profiles", "demo", "tokens.json")); statErr == nil {
		t.Errorf("dry-run should not register or persist tokens")
	}
	got := out.String()
	if !strings.Contains(got, "Dry run") || !strings.Contains(got, "would register") {
		t.Errorf("dry-run output unexpected:\n%s", got)
	}
}

// TestBootstrapOperatorAndAllRejected proves --operator and --all are mutually
// exclusive and rejected before any registration side effect.
func TestBootstrapOperatorAndAllRejected(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, registers := bootstrapServer(t, 0)

	app := testApp(t)
	app.Out = new(bytes.Buffer)

	root := newAPIRootCmd(app.App)
	root.SetOut(app.Out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"bootstrap",
		"--profile", "demo",
		"--base-url", srv.URL,
		"--operator", "generic",
		"--all",
		"--yes",
	})
	err := root.Execute()
	if err == nil {
		t.Fatalf("expected an error when --operator and --all are combined")
	}
	if !strings.Contains(err.Error(), "mutually exclusive") {
		t.Errorf("error = %v, want a mutual-exclusion error", err)
	}
	if n := registers.Load(); n != 0 {
		t.Errorf("registered %d times before the selection error; want 0", n)
	}
}

func TestBootstrapNoActivateLeavesDefault(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, _ := bootstrapServer(t, 0)

	app := testApp(t)
	app.Out = new(bytes.Buffer)
	// Seed an existing default so we can prove --no-activate leaves it alone.
	if err := config.SetDefaultProfile(app.Paths, "preexisting"); err != nil {
		t.Fatalf("seed default: %v", err)
	}

	root := newAPIRootCmd(app.App)
	root.SetOut(app.Out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{
		"bootstrap",
		"--profile", "demo",
		"--base-url", srv.URL,
		"--skip-skill",
		"--no-activate",
		"--timeout", "5s",
		"--yes",
	})
	if err := root.Execute(); err != nil {
		t.Fatalf("bootstrap: %v", err)
	}

	cfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.DefaultProfile != "preexisting" {
		t.Errorf("--no-activate changed default to %q, want preexisting", cfg.DefaultProfile)
	}
}
