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

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// setupServer is a fake control plane: /register always succeeds, and
// /oauth/token returns a pending 400 for the first pendingPolls calls, then a
// live token pair — modelling the human approving the agent mid-wait. The
// returned counter records how many times /register was hit, so tests can
// assert registration did (or did not) happen.
func setupServer(t *testing.T, pendingPolls int32) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	var polls atomic.Int32
	var registers atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/register":
			registers.Add(1)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusCreated) // matches the real backend (POST /register, status_code=201)
			_, _ = w.Write([]byte(`{"client_id":"agnt_boot","status":"pending","registration_access_token":"rat_1"}`))
		case "/oauth/token":
			if polls.Add(1) <= pendingPolls {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusBadRequest)
				// RFC 7807 problem-details — the REAL backend error shape
				// (auth/web/errors.py), which pending-classification must parse.
				_, _ = w.Write([]byte(`{"type":"invalid_grant","status":400,"detail":"agent pending approval","instance":"/oauth/token"}`))
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

// runSetup executes the setup command through the full jentic tree,
// returning the combined output.
func runSetup(t *testing.T, app *app, args ...string) (string, error) {
	t.Helper()
	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(out)
	root.SetArgs(append([]string{"setup"}, args...))
	err := root.Execute()
	return out.String(), err
}

func TestSetupEndToEnd(t *testing.T) {
	withXDG(t)
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, _ := setupServer(t, 0) // approved immediately

	app := testApp(t)
	got, err := runSetup(t, app,
		"--url", srv.URL,
		"--name", "demo",
		"--env", "qa",
		"--operator", "generic",
		"--scope", "user",
		"--timeout", "5s",
		"--yes",
	)
	if err != nil {
		t.Fatalf("setup: %v\nout:\n%s", err, got)
	}

	// The trio was created, activated, and approved with cached tokens.
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.ActiveContext != "qa-demo" {
		t.Errorf("active context = %q, want qa-demo", cfg.ActiveContext)
	}
	assertRegApproved(t, "demo", "qa")

	// Skill written into the generic user-scope target (~/AGENTS.md).
	skillPath := filepath.Join(home, "AGENTS.md")
	body, readErr := os.ReadFile(skillPath)
	if readErr != nil {
		t.Fatalf("expected skill at %s: %v", skillPath, readErr)
	}
	if !strings.Contains(string(body), "BEGIN JENTIC MANAGED SKILL: jentic") {
		t.Errorf("skill file missing named managed block:\n%s", body)
	}
	// Setup installs the full shipped set by default.
	for _, name := range skillgen.BundledNames() {
		if !strings.Contains(string(body), "BEGIN JENTIC MANAGED SKILL: "+name) {
			t.Errorf("setup should install the full set; missing %s block:\n%s", name, body)
		}
	}
	// The skill is templated with THIS install's URL, never a localhost default.
	if !strings.Contains(string(body), srv.URL) {
		t.Errorf("skill body should carry the install URL %s:\n%s", srv.URL, body)
	}

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

func TestSetupWaitsThenApproves(t *testing.T) {
	withXDG(t)
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, _ := setupServer(t, 2) // two pending polls before approval

	app := testApp(t)
	got, err := runSetup(t, app,
		"--url", srv.URL,
		"--name", "demo",
		"--env", "qa",
		"--skip-skill",
		"--timeout", "30s",
		"--yes",
	)
	if err != nil {
		t.Fatalf("setup: %v\nout:\n%s", err, got)
	}

	if !strings.Contains(got, "Approve this agent") {
		t.Errorf("expected the approval banner while pending:\n%s", got)
	}
	if !strings.Contains(got, "resume later with `jentic register`") {
		t.Errorf("waiting hint should point at the resumable front door:\n%s", got)
	}
	if !strings.Contains(got, "Agent approved") {
		t.Errorf("expected approval confirmation:\n%s", got)
	}
	// --skip-skill: no AGENTS.md should be created.
	if _, statErr := os.Stat(filepath.Join(home, "AGENTS.md")); statErr == nil {
		t.Errorf("--skip-skill should not write a skill file")
	}
}

// TestSetupBootstrapAliasStillWorks pins the compatibility contract for the
// command's pre-rename name: `jentic bootstrap` must keep executing the setup
// flow (hidden alias), while staying out of the root help output.
func TestSetupBootstrapAliasStillWorks(t *testing.T) {
	withXDG(t)
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, _ := setupServer(t, 0)

	app := testApp(t)
	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(out)
	root.SetArgs([]string{
		"bootstrap",
		"--url", srv.URL, "--name", "demo", "--env", "qa", "--skip-skill", "--timeout", "5s", "--yes",
	})
	if err := root.Execute(); err != nil {
		t.Fatalf("bootstrap alias: %v\nout:\n%s", err, out.String())
	}
	if !strings.Contains(out.String(), "You're ready") {
		t.Errorf("alias should run the full setup flow:\n%s", out.String())
	}

	// And the alias must be invisible in help.
	help := new(bytes.Buffer)
	helpRoot := newAPIRootCmd(testApp(t).App)
	helpRoot.SetOut(help)
	helpRoot.SetErr(help)
	helpRoot.SetArgs([]string{"--help"})
	if err := helpRoot.Execute(); err != nil {
		t.Fatalf("help: %v", err)
	}
	if strings.Contains(help.String(), "bootstrap") {
		t.Errorf("root help must not mention the hidden bootstrap alias:\n%s", help.String())
	}
}

// TestSetupSelectionErrorBeforeRegister proves operator selection is
// validated before any irreversible side effect: a non-interactive run where
// no operators are given AND none are detected must fail without registering
// an agent or activating a context. (With detected operators, the run
// degrades to them instead — #755.)
func TestSetupSelectionErrorBeforeRegister(t *testing.T) {
	withXDG(t)
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, registers := setupServer(t, 0)

	app := testApp(t)
	stubDetect(t, app, home, t.TempDir()) // nothing detected

	// --yes with no --operator/--all and no TTY: cannot resolve targets.
	_, err := runSetup(t, app,
		"--url", srv.URL,
		"--name", "demo",
		"--env", "qa",
		"--timeout", "5s",
		"--yes",
	)
	if err == nil {
		t.Fatalf("expected an error when no operators can be resolved")
	}
	if !strings.Contains(err.Error(), "no operators") {
		t.Errorf("error = %v, want a 'no operators' selection error", err)
	}
	if !strings.Contains(err.Error(), "--skip-skill") {
		t.Errorf("setup's error should name --skip-skill as the identity-only escape hatch: %v", err)
	}
	if n := registers.Load(); n != 0 {
		t.Errorf("registered %d times before the selection error; want 0 (no side effects)", n)
	}
	cfg, _ := sdkconfig.Load()
	if cfg != nil && cfg.ActiveContext != "" {
		t.Errorf("no context must be activated when selection fails up front, got %q", cfg.ActiveContext)
	}
}

// TestSetupSkipSkillRejectsInvalidScope pins that flag validation is not
// silently skipped on paths a flag doesn't apply to: `--skip-skill` with a
// mistyped --scope must error before any registration side effect.
func TestSetupSkipSkillRejectsInvalidScope(t *testing.T) {
	withXDG(t)
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, registers := setupServer(t, 0)

	app := testApp(t)
	_, err := runSetup(t, app,
		"--url", srv.URL,
		"--skip-skill",
		"--scope", "everywhere",
		"--yes",
	)
	if err == nil || !strings.Contains(err.Error(), "invalid --scope") {
		t.Fatalf("expected an invalid --scope error, got %v", err)
	}
	if n := registers.Load(); n != 0 {
		t.Errorf("registered %d times despite an invalid flag; want 0", n)
	}
}

func TestSetupDryRunWritesNothing(t *testing.T) {
	withXDG(t)
	home := t.TempDir()
	t.Setenv("HOME", home)
	// No server needed: dry-run must not hit the network.
	app := testApp(t)
	got, err := runSetup(t, app,
		"--url", "http://127.0.0.1:0",
		"--name", "demo",
		"--env", "qa",
		"--operator", "generic",
		"--scope", "user",
		"--yes",
		"--dry-run",
	)
	if err != nil {
		t.Fatalf("setup dry-run: %v\nout:\n%s", err, got)
	}

	if _, statErr := os.Stat(filepath.Join(home, "AGENTS.md")); statErr == nil {
		t.Errorf("dry-run should not write a skill file")
	}
	// No context/registration side effects either.
	cfg, _ := sdkconfig.Load()
	if cfg != nil && cfg.ActiveContext != "" {
		t.Errorf("dry-run must not activate a context, got %q", cfg.ActiveContext)
	}
	if !strings.Contains(got, "Dry run") || !strings.Contains(got, "would create environment/identity/context") {
		t.Errorf("dry-run output unexpected:\n%s", got)
	}
}

// TestSetupOperatorAndAllRejected proves --operator and --all are mutually
// exclusive and rejected before any registration side effect.
func TestSetupOperatorAndAllRejected(t *testing.T) {
	withXDG(t)
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, registers := setupServer(t, 0)

	app := testApp(t)
	_, err := runSetup(t, app,
		"--url", srv.URL,
		"--operator", "generic",
		"--all",
		"--yes",
	)
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
