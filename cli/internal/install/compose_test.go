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

// privateTempDir returns a temp dir chmodded 0700, matching the ~/.jentic
// invariant WriteComposeArtifacts asserts before creating the 0777 logs dir
// (SEC-6). t.TempDir() alone yields 0755 subdirs, which the guard rejects.
func privateTempDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.Chmod(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	return dir
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

// TestRenderComposeBindHost pins the #992 contract: the wizard's bind-host
// answer prefixes every published port, because Docker publishes unqualified
// mappings on ALL interfaces (bypassing UFW), silently exposing a
// loopback-intended install to the network.
func TestRenderComposeBindHost(t *testing.T) {
	cases := []struct {
		name       string
		serverHost string
		wantPrefix string
	}{
		{"default loopback", "127.0.0.1", "127.0.0.1"},
		{"localhost normalized", "localhost", "127.0.0.1"},
		{"empty defaults to loopback", "", "127.0.0.1"},
		{"explicit all interfaces", "0.0.0.0", "0.0.0.0"},
		{"specific interface", "10.0.0.5", "10.0.0.5"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d := NewDraft()
			d.RuntimePath = RuntimeDocker
			d.DBBackend = BackendPostgres
			d.PGPassword = "test-pw"
			// Opt in so the db publish is present to assert the prefix on.
			d.PGExposeHostPort = true
			d.ServerHost = tc.serverHost
			cfg := composeConfigFor("/home/u/.jentic")

			data, err := RenderCompose(d, cfg)
			if err != nil {
				t.Fatalf("RenderCompose: %v", err)
			}
			assertValidComposeYAML(t, data)
			out := string(data)

			for _, want := range []string{
				"\"" + tc.wantPrefix + ":" + d.ServerPort + ":" + d.ServerPort + "\"",
				"\"" + tc.wantPrefix + ":" + d.BrokerPort + ":" + d.BrokerPort + "\"",
				"\"" + tc.wantPrefix + ":" + d.PGPort + ":5432\"",
			} {
				if !strings.Contains(out, want) {
					t.Errorf("compose missing %q:\n%s", want, out)
				}
			}
			// No published port may be left unqualified: an entry starting
			// with a digit (e.g. "8000:8000") binds 0.0.0.0.
			for _, line := range strings.Split(out, "\n") {
				trimmed := strings.TrimSpace(line)
				if strings.HasPrefix(trimmed, `- "`) && len(trimmed) > 3 &&
					trimmed[3] >= '0' && trimmed[3] <= '9' &&
					!strings.HasPrefix(trimmed, `- "`+tc.wantPrefix+":") {
					t.Errorf("unqualified port publish %q would bind all interfaces", trimmed)
				}
			}
		})
	}
}

// TestWriteComposeArtifactsRefusesExposedParent (SEC-6): the 0777 logs dir is
// only safe because its parent is owner-only. If the parent is group/other
// accessible, the install must fail loud rather than silently leave a
// world-writable directory reachable by other host users.
func TestWriteComposeArtifactsRefusesExposedParent(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX file modes")
	}
	dir := t.TempDir()
	if err := os.Chmod(dir, 0o755); err != nil { // simulate a non-private parent
		t.Fatal(err)
	}
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres
	d.PGPassword = "test-pw"
	cfg := composeConfigFor(dir)

	err := WriteComposeArtifacts(d, cfg)
	if err == nil {
		t.Fatal("WriteComposeArtifacts succeeded under a group/other-accessible parent; want refusal")
	}
	if !strings.Contains(err.Error(), "world-writable") || !strings.Contains(err.Error(), "chmod 700") {
		t.Errorf("error should explain the invariant and the fix, got: %v", err)
	}
	if _, statErr := os.Stat(cfg.LogsHostDir); !os.IsNotExist(statErr) {
		t.Errorf("logs dir must not be created when the parent invariant fails, stat err = %v", statErr)
	}
}

func TestWriteComposeArtifactsModes(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX file modes")
	}
	dir := privateTempDir(t)
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres
	d.PGPassword = "test-pw"
	cfg := composeConfigFor(dir)
	// Simulate a prior install having created the logs dir 0700 (the pre-#992
	// mode): WriteComposeArtifacts must heal it, not just create-if-missing.
	if err := os.MkdirAll(cfg.LogsHostDir, 0o700); err != nil {
		t.Fatal(err)
	}

	if err := WriteComposeArtifacts(d, cfg); err != nil {
		t.Fatalf("WriteComposeArtifacts: %v", err)
	}

	assertMode := func(path string, want os.FileMode) {
		t.Helper()
		fi, err := os.Stat(path)
		if err != nil {
			t.Fatalf("stat %s: %v", path, err)
		}
		if got := fi.Mode().Perm(); got != want {
			t.Errorf("%s mode = %o, want %o", path, got, want)
		}
	}
	// Read by the postgres container's uid (999), not the host user.
	// Written by the app/broker containers as uid 999.
	assertMode(cfg.LogsHostDir, 0o777)
	// Only the docker CLI (host user) reads the compose file; keep it private.
	assertMode(cfg.ComposePath, 0o600)
}

func TestRenderComposePostgres(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres
	d.PGPassword = "test-pw"
	d.PGExposeHostPort = true
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
		"volumes:\n  db-data:",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("compose (postgres) missing %q:\n%s", want, out)
		}
	}
	// Schema bootstrap lives in the migration runner now (#992): initdb
	// scripts run once on an empty volume, so they must not be relied on.
	if strings.Contains(out, "docker-entrypoint-initdb.d") {
		t.Errorf("postgres compose should not mount an initdb script:\n%s", out)
	}
	// Postgres uses the managed db service, not the SQLite named volume.
	if strings.Contains(out, composeDataVolume) {
		t.Errorf("postgres compose should not reference the SQLite volume:\n%s", out)
	}
}

// TestRenderComposeQuotesCredentials is the SEC-4 regression: operator-seeded
// values (headless --answers) must not be able to inject YAML structure via the
// environment block, and shell metacharacters in the CMD-SHELL-bound pg_user /
// pg_name must be rejected outright.
func TestRenderComposeQuotesCredentials(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres
	// A password full of YAML metacharacters must render as an inert quoted scalar.
	d.PGPassword = `x: y` + "\n" + `evil: {a: b}#"'`
	cfg := composeConfigFor("/home/u/.jentic")

	data, err := RenderCompose(d, cfg)
	if err != nil {
		t.Fatalf("RenderCompose: %v", err)
	}
	assertValidComposeYAML(t, data)
	var doc struct {
		Services map[string]struct {
			Environment map[string]string `yaml:"environment"`
		} `yaml:"services"`
	}
	if err := yaml.Unmarshal(data, &doc); err != nil {
		t.Fatalf("parse rendered compose: %v", err)
	}
	if got := doc.Services[composeServiceDB].Environment["POSTGRES_PASSWORD"]; got != d.PGPassword {
		t.Errorf("password did not round-trip through YAML quoting: got %q want %q", got, d.PGPassword)
	}
	if _, injected := doc.Services[composeServiceDB].Environment["evil"]; injected {
		t.Error("password value injected a new YAML key into the environment block")
	}

	// Shell metacharacters in the healthcheck-bound identifiers: rejected, not rendered.
	d.PGPassword = "safe-pw"
	d.PGUser = "x; wget evil.sh"
	if _, err := RenderCompose(d, cfg); err == nil {
		t.Error("expected RenderCompose to reject a pg_user with shell metacharacters")
	}
	d.PGUser = "jentic"
	d.PGName = `x" || true`
	if _, err := RenderCompose(d, cfg); err == nil {
		t.Error("expected RenderCompose to reject a pg_name with shell metacharacters")
	}
}

// The managed Postgres is reachable over the compose network; publishing 5432
// on the host is opt-in (#992: it was published by default, guarded only by a
// guessable password).
func TestRenderComposePostgresNoHostPublishByDefault(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres
	d.PGPassword = "test-pw"

	data, err := RenderCompose(d, composeConfigFor("/home/u/.jentic"))
	if err != nil {
		t.Fatalf("RenderCompose: %v", err)
	}
	assertValidComposeYAML(t, data)
	out := string(data)

	if strings.Contains(out, ":5432\"") {
		t.Errorf("db port published without opt-in:\n%s", out)
	}
	// The db service block (up to the next service) must carry no ports key.
	dbBlock := out[strings.Index(out, "  db:"):strings.Index(out, "  app:")]
	if strings.Contains(dbBlock, "ports:") {
		t.Errorf("db service has a ports block without opt-in:\n%s", dbBlock)
	}
}

// A blank password must fail loudly at render time, not come up passwordless:
// FillSecrets generates a random credential (the guessable "postgres" default
// was #992's exposure multiplier) and skipping it is a caller bug.
func TestRenderComposePostgresRequiresPassword(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres

	if _, err := RenderCompose(d, composeConfigFor("/home/u/.jentic")); err == nil ||
		!strings.Contains(err.Error(), "postgres password is empty") {
		t.Errorf("expected empty-password error, got %v", err)
	}
}

func TestWriteComposeArtifactsSQLite(t *testing.T) {
	dir := privateTempDir(t)
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
}

// Schema bootstrap moved into the migration runner (#992): no install path
// writes an init SQL file anymore, and a stale one from an older install is
// cleaned up so nothing suggests it is still consulted.
func TestWriteComposeArtifactsRemovesLegacyInitSQL(t *testing.T) {
	dir := privateTempDir(t)
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres
	d.PGPassword = "test-pw"
	cfg := composeConfigFor(dir)
	stale := cfg.legacyInitSchemasPath()
	if err := os.WriteFile(stale, []byte("CREATE SCHEMA IF NOT EXISTS registry;"), 0o600); err != nil {
		t.Fatalf("write stale init SQL: %v", err)
	}

	if err := WriteComposeArtifacts(d, cfg); err != nil {
		t.Fatalf("WriteComposeArtifacts: %v", err)
	}
	if _, err := os.Stat(stale); err == nil {
		t.Errorf("stale init-schemas.sql should have been removed")
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
