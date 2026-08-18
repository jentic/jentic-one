package ctlcmd

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/user"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// offlineOpts points the server probe at a closed port so doctor never depends
// on a live local control plane.
func offlineOpts() *doctorOptions {
	return &doctorOptions{identityOptions: identityOptions{BaseURL: "http://127.0.0.1:1"}}
}

// A missing state directory is a hard failure, so doctor must return a non-nil
// error (the non-zero exit path) and explain what to do.
func TestDoctorMissingHomeFails(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir()) // isolate the agent-state read
	app := &app{App: &cmdcore.App{
		Paths: config.Paths{Root: filepath.Join(t.TempDir(), "nope")},
		Out:   new(bytes.Buffer),
		Err:   new(bytes.Buffer),
	}}
	err := app.doctorE(context.Background(), offlineOpts())
	if err == nil {
		t.Fatal("expected a non-nil error when ~/.jentic is missing")
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"Environment", "does not exist", "failed"} {
		if !strings.Contains(got, want) {
			t.Errorf("doctor output missing %q\n---\n%s", want, got)
		}
	}
}

// A recorded manifest should surface the install mode/db line.
func TestDoctorShowsInstallManifest(t *testing.T) {
	app := testApp(t)
	m := &config.Manifest{Mode: config.ModeDocker, DB: "postgres"}
	if err := m.Save(app.Paths); err != nil {
		t.Fatalf("save manifest: %v", err)
	}
	_ = app.doctorE(context.Background(), offlineOpts())
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"Install", "mode docker", "db postgres"} {
		if !strings.Contains(got, want) {
			t.Errorf("doctor output missing %q\n---\n%s", want, got)
		}
	}
}

func TestDoctorJSON(t *testing.T) {
	app := testApp(t)
	opts := offlineOpts()
	opts.json = true
	_ = app.doctorE(context.Background(), opts)

	var report struct {
		Checks []struct {
			Section string `json:"section"`
			Name    string `json:"name"`
			Status  string `json:"status"`
		} `json:"checks"`
		Summary struct {
			Passed   int `json:"passed"`
			Warnings int `json:"warnings"`
			Failed   int `json:"failed"`
		} `json:"summary"`
	}
	if err := json.Unmarshal(app.Out.(*bytes.Buffer).Bytes(), &report); err != nil {
		t.Fatalf("doctor --json did not emit valid JSON: %v", err)
	}
	if len(report.Checks) == 0 {
		t.Fatal("doctor --json emitted no checks")
	}
	if total := report.Summary.Passed + report.Summary.Warnings + report.Summary.Failed; total != len(report.Checks) {
		t.Errorf("summary totals %d != %d checks", total, len(report.Checks))
	}
}

// On a fresh machine (no install manifest) a missing local-mode tool (uv/git)
// must be a WARNING, not a fail — otherwise `jenticctl doctor` returns a
// non-zero exit on any box that hasn't run a source install (e.g. a Windows CI
// runner without uv). This mirrors the missing-docker/daemon-down "CI-safe,
// zero-exit" policy. We force a manifest-less state and a guaranteed-absent tool
// name via a temp PATH so the assertion holds regardless of what the host has.
func TestDoctorToolingMissingIsWarnWithoutManifest(t *testing.T) {
	app := testApp(t)
	// Point PATH at an empty dir so uv/git are guaranteed absent.
	t.Setenv("PATH", t.TempDir())

	d := &doctor{app: app, ctx: context.Background()}
	d.checkTooling(&config.Manifest{}, false /* no manifest */)

	sawUV := false
	for _, c := range d.checks {
		if c.name == "uv" {
			sawUV = true
			if c.status != statusWarn {
				t.Errorf("missing uv on a fresh box should be statusWarn (zero-exit), got %v", c.status)
			}
		}
	}
	if !sawUV {
		t.Fatalf("checkTooling recorded no uv row: %+v", d.checks)
	}
	if f := d.failed(); f != 0 {
		t.Errorf("tooling on a manifest-less box should contribute 0 hard failures, got %d", f)
	}
}

// Once an install manifest records local mode, a REQUIRED tool that is missing
// is a real broken install and stays a hard fail (non-zero exit).
func TestDoctorToolingMissingIsFailWithManifest(t *testing.T) {
	app := testApp(t)
	t.Setenv("PATH", t.TempDir())

	d := &doctor{app: app, ctx: context.Background()}
	d.checkTooling(&config.Manifest{Mode: config.ModeLocal}, true /* manifest present */)

	for _, c := range d.checks {
		if c.name == "uv" && c.status != statusFail {
			t.Errorf("missing uv with a local-mode manifest should be statusFail, got %v", c.status)
		}
	}
	if d.failed() == 0 {
		t.Error("a missing required tool with a manifest should be a hard failure")
	}
}

func TestDoctorCounts(t *testing.T) {
	d := &doctor{checks: []check{
		{status: statusPass},
		{status: statusPass},
		{status: statusWarn},
		{status: statusFail},
	}}
	p, w, f := d.counts()
	if p != 2 || w != 1 || f != 1 {
		t.Fatalf("counts = (%d,%d,%d), want (2,1,1)", p, w, f)
	}
	if d.failed() != 1 {
		t.Errorf("failed() = %d, want 1", d.failed())
	}
}

func TestDotFor(t *testing.T) {
	st := theme.Themes["dark"].Styles()
	if dotFor(st, statusPass) != st.DotOK() {
		t.Error("statusPass should map to DotOK")
	}
	if dotFor(st, statusWarn) != st.DotWarn() {
		t.Error("statusWarn should map to DotWarn")
	}
	if dotFor(st, statusFail) != st.DotFail() {
		t.Error("statusFail should map to DotFail")
	}
}

// With a compose install and a stopped daemon, the deploy check records an
// explicit, actionable "docker daemon" WARNING (not a fail) rather than
// inferring it from a cryptic `docker compose ps` error (#783). Keeping it a
// warning is what lets doctor stay a zero-exit, CI-safe diagnostic. We assert on
// the specific check rather than doctor's aggregate exit, since other checks'
// severity varies by environment (e.g. CI vs a dev box).
func TestDoctorReportsDockerDaemonDown(t *testing.T) {
	orig := doctorDockerProbe
	t.Cleanup(func() { doctorDockerProbe = orig })
	doctorDockerProbe = func(context.Context) (string, bool) { return "Cannot connect to the Docker daemon", false }

	app := testApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}

	d := &doctor{app: app, ctx: context.Background()}
	d.checkDeploy("Server")

	var daemon *check
	for i := range d.checks {
		if d.checks[i].name == "docker daemon" {
			daemon = &d.checks[i]
			break
		}
	}
	if daemon == nil {
		t.Fatalf("deploy check did not record a `docker daemon` row: %+v", d.checks)
	}
	if daemon.status != statusWarn {
		t.Errorf("down daemon should be statusWarn (CI-safe, zero exit), got %v", daemon.status)
	}
	if !strings.Contains(daemon.detail, "Cannot connect to the Docker daemon") {
		t.Errorf("daemon detail = %q, want the probe's reason", daemon.detail)
	}
	for _, want := range []string{"docker desktop start", "colima start"} {
		if !strings.Contains(daemon.hint, want) {
			t.Errorf("daemon hint missing %q: %q", want, daemon.hint)
		}
	}
	// The check must return early, never falling through to real `docker compose ps`.
	for i := range d.checks {
		if d.checks[i].name == "deploy" {
			t.Errorf("deploy check should return early on a down daemon, not record a `deploy` row: %+v", d.checks[i])
		}
	}
}

// When the `docker` binary is missing (not merely a stopped daemon), doctor's
// deploy check reports it under a `docker` row pointing at install docs rather
// than the "start your Docker daemon" hint (#954).
func TestDoctorReportsDockerNotInstalled(t *testing.T) {
	orig := doctorDockerProbe
	t.Cleanup(func() { doctorDockerProbe = orig })
	// Mirror what the real probe returns when the binary is absent.
	notInstalled := install.DockerNotInstalledDetail()
	doctorDockerProbe = func(context.Context) (string, bool) { return notInstalled, false }

	app := testApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}

	d := &doctor{app: app, ctx: context.Background()}
	d.checkDeploy("Server")

	var row *check
	for i := range d.checks {
		if d.checks[i].name == "docker" {
			row = &d.checks[i]
			break
		}
	}
	if row == nil {
		t.Fatalf("deploy check did not record a `docker` row for a missing binary: %+v", d.checks)
	}
	if row.status != statusWarn {
		t.Errorf("missing docker should be statusWarn (CI-safe), got %v", row.status)
	}
	if !strings.Contains(row.hint, "get-docker") {
		t.Errorf("missing-docker hint should point at install docs, got %q", row.hint)
	}
	if strings.Contains(row.hint, "start your Docker daemon") {
		t.Errorf("missing-docker hint should not tell the user to start a daemon: %q", row.hint)
	}
}

func TestComposeSummary(t *testing.T) {
	out := "NAME      IMAGE     STATUS\napp       x         Up\ndb        y         Up\n"
	if got := composeSummary(out); got != "2 services" {
		t.Errorf("composeSummary = %q, want \"2 services\"", got)
	}
	if got := composeSummary(""); got != "0 services" {
		t.Errorf("composeSummary(empty) = %q, want \"0 services\"", got)
	}
}

func TestCheckStatusString(t *testing.T) {
	for s, want := range map[checkStatus]string{statusPass: "pass", statusWarn: "warn", statusFail: "fail"} {
		if got := s.String(); got != want {
			t.Errorf("checkStatus(%d).String() = %q, want %q", s, got, want)
		}
	}
}

// The client-side self-check (5.1 §3c, plan.md Phase 5 item 6) must always render
// the local-agent confinement prereq breakdown — jenticctl is absent from agent
// hosts, so doctor is the only place these prereqs surface there.
func TestDoctorReportsLocalAgentPrereqs(t *testing.T) {
	app := testApp(t)
	opts := offlineOpts()
	opts.json = true
	_ = app.doctorE(context.Background(), opts)

	var report struct {
		Checks []struct {
			Section string `json:"section"`
		} `json:"checks"`
	}
	if err := json.Unmarshal(app.Out.(*bytes.Buffer).Bytes(), &report); err != nil {
		t.Fatalf("decode doctor JSON: %v", err)
	}
	found := false
	for _, c := range report.Checks {
		if c.Section == "Local agent" {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("doctor is missing the \"Local agent\" section:\n%s", app.Out.(*bytes.Buffer).String())
	}
}

// A provisioned agent account whose OS user resolves to the operator's own uid is
// the isolation-defeating case the same-uid warning exists to catch. We seed the
// account with the CURRENT user (guaranteed same uid) and assert a warning row.
func TestDoctorWarnsSameUIDAgentAccount(t *testing.T) {
	me, err := user.Current()
	if err != nil {
		t.Skipf("cannot resolve current user: %v", err)
	}

	app := testApp(t)
	if _, err := config.MutateAgentState(app.Paths, func(s *config.AgentState) error {
		s.SetAgentAccount(config.AgentAccount{User: me.Username, AccountCreated: true, Enabled: true})
		return nil
	}); err != nil {
		t.Fatalf("seed agent state: %v", err)
	}

	opts := offlineOpts()
	opts.json = true
	_ = app.doctorE(context.Background(), opts)

	var report struct {
		Checks []struct {
			Section string `json:"section"`
			Name    string `json:"name"`
			Status  string `json:"status"`
			Detail  string `json:"detail"`
		} `json:"checks"`
	}
	if err := json.Unmarshal(app.Out.(*bytes.Buffer).Bytes(), &report); err != nil {
		t.Fatalf("decode doctor JSON: %v", err)
	}
	warned := false
	for _, c := range report.Checks {
		if c.Section == "Local agent" && c.Name == "account uid" {
			warned = c.Status == "warn"
		}
	}
	if !warned {
		t.Errorf("expected a same-uid warning for agent account %q:\n%s", me.Username, app.Out.(*bytes.Buffer).String())
	}
}

// A well-formed installed config must produce a passing Configuration check
// naming the DB backend (impl/6.4 "Configuration Validity", Phase 6).
func TestDoctorConfigValidity_PassesForValidConfig(t *testing.T) {
	app := testApp(t)
	cfg := "databases:\n  control:\n    host: localhost\n    port: 5432\n"
	if err := os.WriteFile(app.Paths.InstallConfigPath(), []byte(cfg), 0o600); err != nil {
		t.Fatalf("write install config: %v", err)
	}
	opts := offlineOpts()
	opts.json = true
	_ = app.doctorE(context.Background(), opts)

	c := findCheck(t, app, "Configuration", "databases.control")
	if c.Status != "pass" {
		t.Errorf("expected a passing Configuration check, got %q (%s)", c.Status, c.Detail)
	}
	if !strings.Contains(c.Detail, "postgres") {
		t.Errorf("expected the DB backend in the detail, got %q", c.Detail)
	}
}

// An installed config with no DB backend configured is the "install half-written"
// case; doctor must fail it (non-zero exit) with a remediation hint.
func TestDoctorConfigValidity_FailsWhenControlDBUnset(t *testing.T) {
	app := testApp(t)
	// databases.control present but neither host nor path set.
	cfg := "databases:\n  control:\n    port: 5432\n"
	if err := os.WriteFile(app.Paths.InstallConfigPath(), []byte(cfg), 0o600); err != nil {
		t.Fatalf("write install config: %v", err)
	}
	opts := offlineOpts()
	opts.json = true
	err := app.doctorE(context.Background(), opts)
	if err == nil {
		t.Error("expected a non-zero exit when databases.control has no backend")
	}
	c := findCheck(t, app, "Configuration", "databases.control")
	if c.Status != "fail" {
		t.Errorf("expected a failing Configuration check, got %q", c.Status)
	}
}

// A fresh machine with no installed config must not emit a Configuration row at
// all (the gap is covered by the Install/Server checks; a fresh box shouldn't
// fail here).
func TestDoctorConfigValidity_SkippedWhenNotInstalled(t *testing.T) {
	app := testApp(t)
	opts := offlineOpts()
	opts.json = true
	_ = app.doctorE(context.Background(), opts)

	var report struct {
		Checks []struct {
			Section string `json:"section"`
		} `json:"checks"`
	}
	if err := json.Unmarshal(app.Out.(*bytes.Buffer).Bytes(), &report); err != nil {
		t.Fatalf("decode doctor JSON: %v", err)
	}
	for _, c := range report.Checks {
		if c.Section == "Configuration" {
			t.Errorf("Configuration check should be skipped with no installed config; got a %q row", c.Section)
		}
	}
}

// findCheck decodes the doctor JSON report and returns the check with the given
// section+name, failing the test if it is absent.
func findCheck(t *testing.T, app *app, section, name string) struct {
	Section string `json:"section"`
	Name    string `json:"name"`
	Status  string `json:"status"`
	Detail  string `json:"detail"`
} {
	t.Helper()
	var report struct {
		Checks []struct {
			Section string `json:"section"`
			Name    string `json:"name"`
			Status  string `json:"status"`
			Detail  string `json:"detail"`
		} `json:"checks"`
	}
	if err := json.Unmarshal(app.Out.(*bytes.Buffer).Bytes(), &report); err != nil {
		t.Fatalf("decode doctor JSON: %v", err)
	}
	for _, c := range report.Checks {
		if c.Section == section && c.Name == name {
			return c
		}
	}
	t.Fatalf("check %q/%q not found in doctor report:\n%s", section, name, app.Out.(*bytes.Buffer).String())
	return report.Checks[0] // unreachable
}
