package cmd

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/install"
)

func TestStampTelemetryDecisionKeepsReusedInstanceID(t *testing.T) {
	// The stability contract: an instance id carried over from a prior config
	// by reuseInstallSecrets must survive the consent stamp — re-consenting
	// on reinstall keeps the same telemetry identity instead of churning it.
	draft := install.NewDraft()
	draft.TelemetryInstanceID = "inst-reused-id"

	stampTelemetryDecision(draft, true)

	if !draft.TelemetryEnabled {
		t.Errorf("TelemetryEnabled = false, want true")
	}
	if draft.TelemetryInstanceID != "inst-reused-id" {
		t.Errorf("TelemetryInstanceID = %q, want the reused id", draft.TelemetryInstanceID)
	}
}

func TestStampTelemetryDecisionGeneratesFreshIDWhenEmpty(t *testing.T) {
	draft := install.NewDraft()

	stampTelemetryDecision(draft, true)

	if draft.TelemetryInstanceID == "" {
		t.Errorf("expected a fresh instance id for a first opt-in")
	}
}

func TestStampTelemetryDecisionOptOutGeneratesNoID(t *testing.T) {
	draft := install.NewDraft()

	stampTelemetryDecision(draft, false)

	if draft.TelemetryEnabled {
		t.Errorf("TelemetryEnabled = true, want false")
	}
	if draft.TelemetryInstanceID != "" {
		t.Errorf("TelemetryInstanceID = %q, want empty on opt-out", draft.TelemetryInstanceID)
	}
}

func TestReuseInstallSecretsFromLiveConfig(t *testing.T) {
	// The reinstall repro guard at the cmd layer: reuseInstallSecrets must
	// pre-seed the draft from an existing jentic-one.yaml so a subsequent
	// FillSecrets (fill-only-empty) leaves the encryption key alone.
	dir := t.TempDir()
	out := filepath.Join(dir, "jentic-one.yaml")

	src := install.NewDraft()
	if err := src.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	data, err := src.Render()
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if err := os.WriteFile(out, data, 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}

	buf := &bytes.Buffer{}
	app := &App{Out: buf, Err: &bytes.Buffer{}}
	draft := install.NewDraft()

	reuseInstallSecrets(app, draft, out)

	if draft.EncryptionKeyset == nil {
		t.Fatalf("expected encryption keyset to be reused")
	}
	if draft.AdminJWTSecret != src.AdminJWTSecret {
		t.Errorf("AdminJWTSecret not reused")
	}
	if !strings.Contains(buf.String(), "Reusing secrets") {
		t.Errorf("expected operator notice, got: %q", buf.String())
	}
}

func TestReuseInstallSecretsFallsBackToBackup(t *testing.T) {
	// After a `jenticctl uninstall` (which renames jentic-one.yaml to
	// jentic-one-old.yaml before wiping), the backup path is what makes the
	// preserved data volume readable on the next install. Verify the
	// fallback resolves it.
	dir := t.TempDir()
	out := filepath.Join(dir, "jentic-one.yaml")
	backup := filepath.Join(dir, "jentic-one-old.yaml")

	src := install.NewDraft()
	if err := src.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	data, err := src.Render()
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if err := os.WriteFile(backup, data, 0o600); err != nil {
		t.Fatalf("write backup: %v", err)
	}
	// out itself does not exist — mirroring the state after uninstall.

	app := &App{Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}
	draft := install.NewDraft()

	reuseInstallSecrets(app, draft, out)

	if draft.EncryptionKeyset == nil {
		t.Fatalf("expected encryption keyset to be reused from backup")
	}
	if draft.AdminJWTSecret != src.AdminJWTSecret {
		t.Errorf("AdminJWTSecret not reused from backup")
	}
}

func TestReuseInstallSecretsFreshBoxIsNoOp(t *testing.T) {
	// Fresh install (no config, no backup): reuse is silent and leaves the
	// draft alone so FillSecrets generates everything from scratch.
	dir := t.TempDir()
	out := filepath.Join(dir, "jentic-one.yaml")

	buf := &bytes.Buffer{}
	app := &App{Out: buf, Err: &bytes.Buffer{}}
	draft := install.NewDraft()

	reuseInstallSecrets(app, draft, out)

	if draft.EncryptionKeyset != nil || draft.AdminJWTSecret != "" {
		t.Errorf("draft mutated on a fresh box")
	}
	if strings.Contains(buf.String(), "Reusing secrets") {
		t.Errorf("did not expect reuse notice on a fresh box, got: %q", buf.String())
	}
}

func TestReuseInstallSecretsMalformedFileWarnsAndFallsThrough(t *testing.T) {
	// A half-written prior config must not block reinstall: the caller
	// warns and continues to fresh secrets. Whether the backup at the
	// candidate list's second slot succeeds is orthogonal.
	dir := t.TempDir()
	out := filepath.Join(dir, "jentic-one.yaml")
	if err := os.WriteFile(out, []byte(":\n\tnot yaml\n"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}

	buf := &bytes.Buffer{}
	app := &App{Out: buf, Err: &bytes.Buffer{}}
	draft := install.NewDraft()

	reuseInstallSecrets(app, draft, out)

	if draft.EncryptionKeyset != nil {
		t.Errorf("draft mutated on malformed input")
	}
	if !strings.Contains(buf.String(), "could not read prior config") {
		t.Errorf("expected warning, got: %q", buf.String())
	}
}

// installCmdStubDocker installs a `docker` stub on PATH that logs every
// invocation and succeeds, so migrationFailure's teardown path can run without
// a daemon. POSIX-only.
func installCmdStubDocker(t *testing.T) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("shell-stub PATH technique is POSIX-only")
	}
	dir := t.TempDir()
	log := filepath.Join(dir, "log")
	script := "#!/bin/sh\necho \"$@\" >> '" + log + "'\nexit 0\n"
	if err := os.WriteFile(filepath.Join(dir, "docker"), []byte(script), 0o755); err != nil {
		t.Fatalf("write docker stub: %v", err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	return log
}

func TestMigrationFailureFreshVolumeIsReset(t *testing.T) {
	// A first migration failing used to leave a half-initialized volume that
	// poisoned every retry (#992 item 3). When this run created the volume,
	// it is discarded automatically and the error says to just re-run.
	log := installCmdStubDocker(t)
	buf := &bytes.Buffer{}
	app := &App{Out: buf, Err: &bytes.Buffer{}}

	err := app.migrationFailure("/home/u/.jentic/docker-compose.yaml",
		[]string{"jentic_db-data"}, true, errors.New("boom"))
	if err == nil || !strings.Contains(err.Error(), "migrations failed: boom") {
		t.Fatalf("err = %v, want the original cause preserved", err)
	}
	if !strings.Contains(err.Error(), "re-run `jenticctl install`") {
		t.Errorf("error should say a clean re-run works, got: %v", err)
	}
	logged, _ := os.ReadFile(log)
	if !strings.Contains(string(logged), "down -v") ||
		!strings.Contains(string(logged), "volume rm jentic_db-data") {
		t.Errorf("expected teardown + explicit volume rm, docker log:\n%s", logged)
	}
}

func TestMigrationFailurePreexistingVolumeIsNeverDestroyed(t *testing.T) {
	// A volume that predates this install may hold a real database: recovery
	// must never be automatic — the operator gets the literal command and a
	// backup warning, and docker is not touched at all.
	log := installCmdStubDocker(t)
	buf := &bytes.Buffer{}
	app := &App{Out: buf, Err: &bytes.Buffer{}}

	err := app.migrationFailure("/home/u/.jentic/docker-compose.yaml",
		[]string{"jentic_db-data"}, false, errors.New("boom"))
	if err == nil || !strings.Contains(err.Error(), "migrations failed: boom") {
		t.Fatalf("err = %v, want the original cause preserved", err)
	}
	out := buf.String()
	if !strings.Contains(out, "back it up") {
		t.Errorf("expected a backup warning, got:\n%s", out)
	}
	if !strings.Contains(out, install.ManualResetCommand("/home/u/.jentic/docker-compose.yaml")) {
		t.Errorf("expected the literal manual reset command, got:\n%s", out)
	}
	if logged, _ := os.ReadFile(log); len(logged) != 0 {
		t.Errorf("docker must not be invoked for a pre-existing volume, log:\n%s", logged)
	}
}
