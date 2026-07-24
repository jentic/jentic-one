package cmd

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
)

// writeFile creates a file with the given contents under dir, failing the test
// on error.
func writeFile(t *testing.T, path, contents string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir for %s: %v", path, err)
	}
	if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
}

// TestUninstallLocalPathUnchanged verifies that without a compose file (the
// local/venv install) uninstall backs up config and removes the rest, exactly
// as before — no Docker teardown is attempted.
func TestUninstallLocalPathUnchanged(t *testing.T) {
	app := testApp(t)
	dir := app.Paths.Dir()

	writeFile(t, filepath.Join(dir, config.InstallConfigName), "mode: local\n")
	writeFile(t, filepath.Join(dir, config.ConfigName), "key: value\n")
	writeFile(t, filepath.Join(dir, "venv", "marker"), "x")
	writeFile(t, filepath.Join(dir, "app.log"), "log")

	if err := app.uninstallE(&uninstallOptions{yes: true}); err != nil {
		t.Fatalf("uninstallE: %v", err)
	}

	// Config files backed up to *-old.
	for name, backup := range preservedConfigs {
		if _, err := os.Stat(filepath.Join(dir, name)); !os.IsNotExist(err) {
			t.Errorf("expected %s to be renamed away", name)
		}
		if _, err := os.Stat(filepath.Join(dir, backup)); err != nil {
			t.Errorf("expected backup %s to exist: %v", backup, err)
		}
	}
	// Everything else removed.
	for _, name := range []string{"venv", "app.log"} {
		if _, err := os.Stat(filepath.Join(dir, name)); !os.IsNotExist(err) {
			t.Errorf("expected %s to be removed", name)
		}
	}
	if out := app.Out.(*bytes.Buffer).String(); strings.Contains(out, "Docker stack") {
		t.Errorf("local uninstall should not touch Docker, got: %q", out)
	}
}

// TestUninstallPurgeKeepDataMutuallyExclusive verifies the flag conflict is a
// clear error before anything is removed.
func TestUninstallPurgeKeepDataMutuallyExclusive(t *testing.T) {
	app := testApp(t)
	writeFile(t, filepath.Join(app.Paths.Dir(), "app.log"), "log")

	err := app.uninstallE(&uninstallOptions{yes: true, purge: true, keepData: true})
	if err == nil || !strings.Contains(err.Error(), "mutually exclusive") {
		t.Fatalf("expected mutually-exclusive error, got %v", err)
	}
	// Nothing should have been removed.
	if _, err := os.Stat(filepath.Join(app.Paths.Dir(), "app.log")); err != nil {
		t.Errorf("files should be untouched on flag conflict: %v", err)
	}
}

// TestUninstallFlagsRegistered verifies the new flags exist on the command.
func TestUninstallFlagsRegistered(t *testing.T) {
	cmd := newUninstallCmd(testApp(t))
	for _, name := range []string{"yes", "purge", "keep-data"} {
		if cmd.Flags().Lookup(name) == nil {
			t.Errorf("uninstall command missing --%s flag", name)
		}
	}
}

// TestUninstallHelpDescribesNamedVolume verifies the help text no longer claims
// to remove "data" unconditionally and explains the preserved-by-default volume.
func TestUninstallHelpDescribesNamedVolume(t *testing.T) {
	cmd := newUninstallCmd(testApp(t))
	long := cmd.Long
	for _, want := range []string{"named volume", "PRESERVES", "--purge", "--keep-data"} {
		if !strings.Contains(long, want) {
			t.Errorf("uninstall Long help missing %q; got:\n%s", want, long)
		}
	}
}

// TestUninstallDockerDetectionLenientWhenDockerAbsent verifies that on a Docker
// install (compose file present) where the docker binary is unavailable,
// uninstall warns and still completes the filesystem teardown rather than
// aborting. The test environment has no docker on PATH.
func TestUninstallDockerDetectionLenientWhenDockerAbsent(t *testing.T) {
	if _, err := exec.LookPath("docker"); err == nil {
		t.Skip("docker present on PATH; this test asserts the docker-absent fallback")
	}

	app := testApp(t)
	dir := app.Paths.Dir()
	writeFile(t, app.Paths.ComposePath(), "services: {}\n")
	writeFile(t, filepath.Join(dir, "app.log"), "log")

	if err := app.uninstallE(&uninstallOptions{yes: true}); err != nil {
		t.Fatalf("uninstallE should not abort when docker is absent: %v", err)
	}

	out := app.Out.(*bytes.Buffer).String()
	if !strings.Contains(out, "docker not found") {
		t.Errorf("expected docker-absent warning, got: %q", out)
	}
	// Filesystem teardown still ran (including removing the compose file).
	if _, err := os.Stat(filepath.Join(dir, "app.log")); !os.IsNotExist(err) {
		t.Errorf("filesystem teardown should still complete when docker is absent")
	}
	if _, err := os.Stat(app.Paths.ComposePath()); !os.IsNotExist(err) {
		t.Errorf("compose file should be removed by the filesystem teardown")
	}
}

// A source (non-Docker) install keeps its database under ~/.jentic/data, so
// wiping the directory is sufficient. Uninstall must remove data/ and back up
// the config files.
func TestUninstall_SourceWipesDataAndBacksUpConfig(t *testing.T) {
	app, out := newTestApp(t)
	dir := app.Paths.Dir()

	dataDir := app.Paths.DataDir()
	if err := os.MkdirAll(dataDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dataDir, "admin.db"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(app.Paths.InstallConfigPath(), []byte("k: v"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := app.uninstallE(&uninstallOptions{yes: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}

	if _, err := os.Stat(dataDir); !os.IsNotExist(err) {
		t.Errorf("data dir should be removed, stat err = %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, config.BackupName(config.InstallConfigName))); err != nil {
		t.Errorf("install config should be backed up: %v", err)
	}
	if strings.Contains(out.String(), "Docker stack") {
		t.Errorf("source install should not attempt Docker teardown:\n%s", out.String())
	}
}

// A Docker install keeps its database in a Docker named volume OUTSIDE
// ~/.jentic. By default (no --purge, unattended --yes) uninstall PRESERVES that
// volume: it stops the stack with a plain `docker compose down` so a reinstall
// reattaches the old data. The teardown is best-effort — without a daemon it
// fails non-fatally and the filesystem cleanup still removes the compose file.
func TestUninstall_DockerKeepsVolumeByDefault(t *testing.T) {
	if _, err := exec.LookPath("docker"); err != nil {
		// The "Docker stack" lines only print once the docker binary is found;
		// when it's absent uninstall warns and skips teardown (covered by
		// TestUninstallDockerDetectionLenientWhenDockerAbsent). Skip so local
		// contributors without docker aren't broken.
		t.Skip("docker not on PATH")
	}

	app, out := newTestApp(t)

	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := app.uninstallE(&uninstallOptions{yes: true}); err != nil {
		t.Fatalf("uninstall should be non-fatal even if docker teardown fails: %v", err)
	}

	s := out.String()
	if !strings.Contains(s, "Docker stack") {
		t.Errorf("docker install should attempt a stack teardown:\n%s", s)
	}
	// With the keep-data default, uninstall must not print the purge hint.
	if strings.Contains(s, "down -v") {
		t.Errorf("keep-data default should not attempt a volume-removing down -v:\n%s", s)
	}
	// Whether docker is present or not, the compose file must be gone afterwards.
	if _, err := os.Stat(app.Paths.ComposePath()); !os.IsNotExist(err) {
		t.Errorf("compose file should be removed, stat err = %v", err)
	}
}

// With --purge on the Docker path uninstall removes the data volume. Without a
// daemon the `down -v` fails, but it must be non-fatal and point the operator
// at the exact volume to remove by hand.
func TestUninstall_DockerPurgeAttemptsVolumeRemoval(t *testing.T) {
	if _, err := exec.LookPath("docker"); err != nil {
		// The "Docker stack" lines only print once the docker binary is found;
		// when it's absent uninstall warns and skips teardown (covered by
		// TestUninstallDockerDetectionLenientWhenDockerAbsent). Skip so local
		// contributors without docker aren't broken.
		t.Skip("docker not on PATH")
	}

	app, out := newTestApp(t)

	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := app.uninstallE(&uninstallOptions{yes: true, purge: true}); err != nil {
		t.Fatalf("uninstall should be non-fatal even if docker teardown fails: %v", err)
	}

	s := out.String()
	if !strings.Contains(s, "Docker stack") {
		t.Errorf("docker purge should attempt stack/volume removal:\n%s", s)
	}
	// Whether docker is present or not, the compose file must be gone afterwards.
	if _, err := os.Stat(app.Paths.ComposePath()); !os.IsNotExist(err) {
		t.Errorf("compose file should be removed, stat err = %v", err)
	}
	// If the teardown could not run (no daemon in CI), the hint must name the
	// project-prefixed volume so the operator can finish the cleanup.
	wantVol := install.DataVolumeNames(false)[0]
	if strings.Contains(s, "may survive") && !strings.Contains(s, wantVol) {
		t.Errorf("failure hint should name the data volume %q:\n%s", wantVol, s)
	}
}

// fakePurgeDocker installs a `docker` stub on PATH that makes the purge path
// deterministic without a real daemon: `docker compose … down -v` succeeds but
// removes nothing (the wrong-project-name case that leaves the volume behind),
// and `docker volume rm <name>` succeeds while appending <name> to a log file.
// Returns the log path so the test can assert the explicit fallback ran. This
// reproduces the reported bug: down -v exits 0 yet the volume survives, so the
// by-name removal is what actually deletes it. POSIX-only (shell stub).
func fakePurgeDocker(t *testing.T) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("shell-stub PATH technique is POSIX-only")
	}
	dir := t.TempDir()
	log := filepath.Join(dir, "rm_log")
	script := "#!/bin/sh\n" +
		"if [ \"$1\" = \"volume\" ] && [ \"$2\" = \"rm\" ]; then\n" +
		"  echo \"$3\" >> '" + log + "'\n" +
		"  echo \"$3\"\n" +
		"  exit 0\n" +
		"fi\n" +
		// Every other invocation (compose … down -v, etc.) succeeds as a no-op,
		// standing in for a `down -v` that matched no volumes.
		"exit 0\n"
	docker := filepath.Join(dir, "docker")
	if err := os.WriteFile(docker, []byte(script), 0o755); err != nil {
		t.Fatalf("write docker stub: %v", err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	return log
}

// The regression fix: when `docker compose down -v` succeeds but removes no
// volume (the volume was created under a different project name), --purge must
// still delete the known project-prefixed volume by name. With a sqlite
// manifest that volume is jentic_jentic-data, and the success message must name
// it so the operator sees the data was actually removed.
func TestUninstall_DockerPurgeRemovesVolumeByNameWhenDownVIsNoop(t *testing.T) {
	log := fakePurgeDocker(t)

	app, out := newTestApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	m := &config.Manifest{DB: "sqlite"}
	if err := m.Save(app.Paths); err != nil {
		t.Fatal(err)
	}

	if err := app.uninstallE(&uninstallOptions{yes: true, purge: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}

	wantVol := install.DataVolumeNames(false)[0] // jentic_jentic-data
	logged, _ := os.ReadFile(log)
	if !strings.Contains(string(logged), wantVol) {
		t.Errorf("expected explicit `docker volume rm %s` fallback; rm log:\n%s", wantVol, logged)
	}
	if s := out.String(); !strings.Contains(s, wantVol) {
		t.Errorf("success message should name the removed volume %q:\n%s", wantVol, s)
	}
}

// On a local (non-Docker) install --keep-data can't preserve anything: the
// database lives under ~/.jentic/data, which the filesystem wipe deletes
// unconditionally. uninstall must warn rather than silently destroy the data
// the flag names. Mirrors stop.go's "--volumes has no effect" precedent.
func TestUninstall_KeepDataWarnsOnLocalInstall(t *testing.T) {
	app, out := newTestApp(t)
	writeFile(t, filepath.Join(app.Paths.Dir(), "app.log"), "log")

	if err := app.uninstallE(&uninstallOptions{yes: true, keepData: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}

	s := out.String()
	if !strings.Contains(s, "only apply to a Docker install") {
		t.Errorf("expected a local-install footgun warning for --keep-data, got:\n%s", s)
	}
}

// An empty ~/.jentic (nothing installed) must be a clean no-op: no teardown,
// no backups, no errors.
func TestUninstall_EmptyDirIsNoOp(t *testing.T) {
	app, out := newTestApp(t)
	// newTestApp roots at an existing TempDir with no files in it.
	if err := app.uninstallE(&uninstallOptions{yes: true}); err != nil {
		t.Fatalf("uninstall of empty dir should be a no-op: %v", err)
	}
	if s := out.String(); !strings.Contains(s, "Nothing to do") {
		t.Errorf("expected a nothing-to-do message for empty dir, got:\n%s", s)
	}
}

// The manual-removal hint must name the precise volume for the recorded install
// mode, and — when the mode is unknown (no manifest, e.g. a pre-manifest
// install) — list BOTH candidates so the operator is never pointed at the wrong
// one.
func TestUninstallVolumeHint(t *testing.T) {
	t.Run("postgres manifest names the postgres volume only", func(t *testing.T) {
		app, _ := newTestApp(t)
		m := &config.Manifest{DB: "postgres"}
		if err := m.Save(app.Paths); err != nil {
			t.Fatal(err)
		}
		got := app.uninstallVolumeHint()
		want := install.DataVolumeNames(true)
		if len(got) != 1 || got[0] != want[0] {
			t.Errorf("uninstallVolumeHint() = %v, want %v", got, want)
		}
	})

	t.Run("sqlite manifest names the sqlite volume only", func(t *testing.T) {
		app, _ := newTestApp(t)
		m := &config.Manifest{DB: "sqlite"}
		if err := m.Save(app.Paths); err != nil {
			t.Fatal(err)
		}
		got := app.uninstallVolumeHint()
		want := install.DataVolumeNames(false)
		if len(got) != 1 || got[0] != want[0] {
			t.Errorf("uninstallVolumeHint() = %v, want %v", got, want)
		}
	})

	t.Run("missing manifest lists both candidate volumes", func(t *testing.T) {
		app, _ := newTestApp(t)
		got := app.uninstallVolumeHint()
		want := install.AllDataVolumeNames()
		if len(got) != len(want) {
			t.Fatalf("uninstallVolumeHint() = %v, want %v", got, want)
		}
		for i := range want {
			if got[i] != want[i] {
				t.Errorf("uninstallVolumeHint()[%d] = %q, want %q", i, got[i], want[i])
			}
		}
	})
}
