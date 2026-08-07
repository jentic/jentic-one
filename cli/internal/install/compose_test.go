package install

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// assertValidComposeYAML fails the test if data is not parseable YAML with a
// top-level services map (a minimal sanity check on the template's whitespace).
func assertValidComposeYAML(t *testing.T, data []byte) {
	t.Helper()
	var doc map[string]any
	if err := yaml.Unmarshal(data, &doc); err != nil {
		t.Fatalf("rendered compose is not valid YAML: %v\n%s", err, data)
	}
	if _, ok := doc["services"].(map[string]any); !ok {
		t.Fatalf("rendered compose has no services map:\n%s", data)
	}
}

func composeConfigFor(dir string) ComposeConfig {
	return ComposeConfig{
		ComposePath:    filepath.Join(dir, "docker-compose.yaml"),
		ConfigHostPath: filepath.Join(dir, "jentic-one.yaml"),
		LogsHostDir:    filepath.Join(dir, "logs"),
	}
}

func TestMigrateArgsDisablesTTY(t *testing.T) {
	args := migrateArgs("/home/u/.jentic/docker-compose.yaml")
	got := strings.Join(args, " ")

	// -T must precede the service name so `docker compose run` does not try to
	// allocate a pseudo-TTY (which fails when the CLI runs it with no terminal).
	want := "compose -p " + composeProjectName + " -f /home/u/.jentic/docker-compose.yaml run --rm -T " +
		composeServiceApp + " python -m jentic_one.migrations.run"
	if got != want {
		t.Errorf("migrateArgs =\n  %q\nwant\n  %q", got, want)
	}
}

func TestRenderComposeSQLite(t *testing.T) {
	d := NewDraft()
	d.DBBackend = BackendSQLite
	d.RuntimePath = RuntimeDocker
	d.Apps = []string{"registry", "admin"}
	cfg := composeConfigFor("/home/u/.jentic")

	data, err := RenderCompose(d, cfg)
	if err != nil {
		t.Fatalf("RenderCompose: %v", err)
	}
	assertValidComposeYAML(t, data)
	out := string(data)

	for _, want := range []string{
		AppImageTag,
		"JENTIC_CONFIG_FILE: " + containerConfigPath,
		"JENTIC__APPS: registry,admin",
		// The model cache must be redirected to a writable dir (uid 999's $HOME
		// is not writable) or the ingest embedding stage dies with EACCES.
		"HF_HOME: /tmp/hf-cache",
		"SENTENCE_TRANSFORMERS_HOME: /tmp/hf-cache",
		cfg.ConfigHostPath + ":" + containerConfigPath + ":ro",
		// SQLite is backed by a named volume, not a host bind mount.
		composeDataVolume + ":" + containerDataDir,
		// The broker runs as its own service with apps=broker on its own port.
		composeServiceBroker + ":",
		"JENTIC__APPS: broker",
		"JENTIC__SERVER__PORT: \"" + DefaultBrokerPort + "\"",
		"\"127.0.0.1:" + DefaultBrokerPort + ":" + DefaultBrokerPort + "\"",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("compose (sqlite) missing %q:\n%s", want, out)
		}
	}
	// The named volume must be declared at the top level.
	if !strings.Contains(out, "volumes:\n  "+composeDataVolume+":") {
		t.Errorf("sqlite compose should declare the %s named volume:\n%s", composeDataVolume, out)
	}
	// The project name must be pinned so volume names don't drift with the
	// install directory (and the uninstall hint stays correct).
	if !strings.Contains(out, "name: "+composeProjectName+"\n") {
		t.Errorf("compose should pin the project name %q:\n%s", composeProjectName, out)
	}
	if got := DataVolumeNames(false); len(got) != 1 || got[0] != composeProjectName+"_"+composeDataVolume {
		t.Errorf("DataVolumeNames(false) = %v, want [%s_%s]", got, composeProjectName, composeDataVolume)
	}
	if got := DataVolumeNames(true); len(got) != 1 || got[0] != composeProjectName+"_"+postgresDataVolume {
		t.Errorf("DataVolumeNames(true) = %v, want [%s_%s]", got, composeProjectName, postgresDataVolume)
	}
	if strings.Contains(out, postgresImage) || strings.Contains(out, "depends_on") {
		t.Errorf("sqlite compose should not include a db service:\n%s", out)
	}
}

func TestRenderComposePostgres(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres
	d.PGPort = "55432"
	cfg := composeConfigFor("/home/u/.jentic")

	data, err := RenderCompose(d, cfg)
	if err != nil {
		t.Fatalf("RenderCompose: %v", err)
	}
	assertValidComposeYAML(t, data)
	out := string(data)

	for _, want := range []string{
		postgresImage,
		"depends_on",
		"condition: service_healthy",
		"\"127.0.0.1:55432:5432\"",
		cfg.InitSchemasPath() + ":/docker-entrypoint-initdb.d/init-schemas.sql:ro",
		"volumes:\n  db-data:",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("compose (postgres) missing %q:\n%s", want, out)
		}
	}
	// Postgres uses the managed db service, not the SQLite named volume.
	if strings.Contains(out, composeDataVolume) {
		t.Errorf("postgres compose should not reference the SQLite volume:\n%s", out)
	}
}

func TestWriteComposeArtifactsSQLite(t *testing.T) {
	dir := t.TempDir()
	d := NewDraft()
	d.DBBackend = BackendSQLite
	d.RuntimePath = RuntimeDocker
	cfg := composeConfigFor(dir)

	if err := WriteComposeArtifacts(d, cfg); err != nil {
		t.Fatalf("WriteComposeArtifacts: %v", err)
	}
	if _, err := os.Stat(cfg.ComposePath); err != nil {
		t.Errorf("compose file not written: %v", err)
	}
	if _, err := os.Stat(cfg.LogsHostDir); err != nil {
		t.Errorf("logs dir not created: %v", err)
	}
	// SQLite lives in a named volume (no host data dir) and needs no init SQL.
	if _, err := os.Stat(cfg.InitSchemasPath()); err == nil {
		t.Errorf("sqlite install should not write init-schemas.sql")
	}
}

func TestWriteComposeArtifactsPostgresWritesInitSQL(t *testing.T) {
	dir := t.TempDir()
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres
	cfg := composeConfigFor(dir)

	if err := WriteComposeArtifacts(d, cfg); err != nil {
		t.Fatalf("WriteComposeArtifacts: %v", err)
	}
	sql, err := os.ReadFile(cfg.InitSchemasPath())
	if err != nil {
		t.Fatalf("init-schemas.sql not written: %v", err)
	}
	// The file is bind-mounted into the postgres container and read by initdb
	// running as the container's own uid (not the host uid that wrote it), so it
	// MUST be world-readable — a 0600 file makes initdb fail and the db exit(1).
	if runtime.GOOS != "windows" {
		fi, err := os.Stat(cfg.InitSchemasPath())
		if err != nil {
			t.Fatalf("stat init-schemas.sql: %v", err)
		}
		if perm := fi.Mode().Perm(); perm&0o044 == 0 {
			t.Errorf("init-schemas.sql mode = %#o, want world/group-readable (e.g. 0644)", perm)
		}
	}
	for _, schema := range []string{"registry", "control", "admin"} {
		if !strings.Contains(string(sql), "CREATE SCHEMA IF NOT EXISTS "+schema) {
			t.Errorf("init SQL missing schema %q:\n%s", schema, sql)
		}
	}
}

// fakeVolumeDocker installs a `docker` stub on PATH that handles `docker volume
// rm <name>`: it succeeds unless <name> is in missing (which makes it print the
// daemon's real "no such volume" message and exit 1, exactly as a missing
// volume does). Every `volume rm` invocation appends its target name to a log
// file so the test can assert which volumes removal was attempted for. Returns
// the log-file path. POSIX-only (shell stub), mirroring fakeDocker.
func fakeVolumeDocker(t *testing.T, missing ...string) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("shell-stub PATH technique is POSIX-only")
	}
	dir := t.TempDir()
	log := filepath.Join(dir, "rm_log")
	var missingClauses strings.Builder
	for _, m := range missing {
		missingClauses.WriteString("    if [ \"$3\" = \"" + m + "\" ]; then\n")
		missingClauses.WriteString("      echo \"Error: No such volume: " + m + "\" 1>&2\n")
		missingClauses.WriteString("      exit 1\n")
		missingClauses.WriteString("    fi\n")
	}
	script := "#!/bin/sh\n" +
		"if [ \"$1\" = \"volume\" ] && [ \"$2\" = \"rm\" ]; then\n" +
		"  echo \"$3\" >> '" + log + "'\n" +
		missingClauses.String() +
		"  echo \"$3\"\n" +
		"  exit 0\n" +
		"fi\n" +
		"exit 0\n"
	docker := filepath.Join(dir, "docker")
	if err := os.WriteFile(docker, []byte(script), 0o755); err != nil {
		t.Fatalf("write docker stub: %v", err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	return log
}

func TestRemoveDataVolumesRemovesEachName(t *testing.T) {
	log := fakeVolumeDocker(t)

	var buf strings.Builder
	names := []string{"jentic_jentic-data", "jentic_db-data"}
	removed, err := RemoveDataVolumes(&buf, names)
	if err != nil {
		t.Fatalf("RemoveDataVolumes: %v", err)
	}
	if len(removed) != len(names) {
		t.Fatalf("removed = %v, want %v", removed, names)
	}
	for i, n := range names {
		if removed[i] != n {
			t.Errorf("removed[%d] = %q, want %q", i, removed[i], n)
		}
	}
	logged, _ := os.ReadFile(log)
	for _, n := range names {
		if !strings.Contains(string(logged), n) {
			t.Errorf("expected docker volume rm to be attempted for %q; log:\n%s", n, logged)
		}
	}
}

func TestRemoveDataVolumesSwallowsMissingVolume(t *testing.T) {
	// The SQLite volume is already gone (down -v removed it); the Postgres one
	// does not exist. Neither is an error, and only the present one is reported
	// as removed.
	fakeVolumeDocker(t, "jentic_jentic-data")

	var buf strings.Builder
	removed, err := RemoveDataVolumes(&buf, []string{"jentic_jentic-data", "jentic_db-data"})
	if err != nil {
		t.Fatalf("missing volume must be a no-op, got: %v", err)
	}
	if len(removed) != 1 || removed[0] != "jentic_db-data" {
		t.Errorf("removed = %v, want [jentic_db-data] (missing one skipped)", removed)
	}
}
