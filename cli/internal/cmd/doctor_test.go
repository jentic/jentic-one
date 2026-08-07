package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
)

// offlineOpts points the server probe at a closed port so doctor never depends
// on a live local control plane.
func offlineOpts() *doctorOptions {
	return &doctorOptions{identityOptions: identityOptions{baseURL: "http://127.0.0.1:1"}}
}

// A missing state directory is a hard failure, so doctor must return a non-nil
// error (the non-zero exit path) and explain what to do.
func TestDoctorMissingHomeFails(t *testing.T) {
	app := &App{
		Paths: config.Paths{Root: filepath.Join(t.TempDir(), "nope")},
		Out:   new(bytes.Buffer),
		Err:   new(bytes.Buffer),
	}
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
	if dotFor(statusPass) != dotOK() {
		t.Error("statusPass should map to dotOK")
	}
	if dotFor(statusWarn) != dotWarn() {
		t.Error("statusWarn should map to dotWarn")
	}
	if dotFor(statusFail) != dotFail() {
		t.Error("statusFail should map to dotFail")
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

// exposureRow finds the `exposure` check row recorded by checkExposure.
func exposureRow(t *testing.T, d *doctor) *check {
	t.Helper()
	for i := range d.checks {
		if d.checks[i].name == "exposure" {
			return &d.checks[i]
		}
	}
	t.Fatalf("no `exposure` row recorded: %+v", d.checks)
	return nil
}

// A compose file generated before the #992 fix publishes ports unqualified,
// which Docker binds to all interfaces (bypassing UFW). Doctor must warn with
// the offending mappings and a regeneration hint — this is the only mitigation
// that reaches installs already in the field.
func TestDoctorWarnsOnUnqualifiedPublishes(t *testing.T) {
	app := testApp(t)
	legacy := "services:\n  db:\n    ports:\n      - \"5432:5432\"\n  app:\n    ports:\n      - \"8000:8000\"\n"
	if err := os.WriteFile(app.Paths.ComposePath(), []byte(legacy), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}

	d := &doctor{app: app, ctx: context.Background()}
	d.checkExposure("Server", app.Paths.ComposePath(), "")

	row := exposureRow(t, d)
	if row.status != statusWarn {
		t.Errorf("unqualified publishes should warn, got %v", row.status)
	}
	for _, want := range []string{"db 5432:5432", "app 8000:8000", "UFW"} {
		if !strings.Contains(row.detail, want) {
			t.Errorf("exposure detail missing %q: %q", want, row.detail)
		}
	}
	if !strings.Contains(row.hint, "jenticctl install") {
		t.Errorf("exposure hint should point at regeneration, got %q", row.hint)
	}
}

// Even when the compose file is clean, containers started from an older file
// can still be publishing on 0.0.0.0 — the live ps output is the truth.
func TestDoctorWarnsOnStaleAllInterfacesContainers(t *testing.T) {
	app := testApp(t)
	clean := "services:\n  app:\n    ports:\n      - \"127.0.0.1:8000:8000\"\n"
	if err := os.WriteFile(app.Paths.ComposePath(), []byte(clean), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}

	d := &doctor{app: app, ctx: context.Background()}
	d.checkExposure("Server", app.Paths.ComposePath(), "app  Up  0.0.0.0:8000->8000/tcp\n")

	row := exposureRow(t, d)
	if row.status != statusWarn {
		t.Errorf("stale all-interfaces containers should warn, got %v", row.status)
	}
	if !strings.Contains(row.hint, "jenticctl stop") {
		t.Errorf("stale-container hint should say to restart the stack, got %q", row.hint)
	}
}

func TestDoctorExposurePassesOnCleanInstall(t *testing.T) {
	app := testApp(t)
	clean := "services:\n  app:\n    ports:\n      - \"127.0.0.1:8000:8000\"\n"
	if err := os.WriteFile(app.Paths.ComposePath(), []byte(clean), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}

	d := &doctor{app: app, ctx: context.Background()}
	d.checkExposure("Server", app.Paths.ComposePath(), "app  Up  127.0.0.1:8000->8000/tcp\n")

	if row := exposureRow(t, d); row.status != statusPass {
		t.Errorf("clean install should pass, got %v (%q)", row.status, row.detail)
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
