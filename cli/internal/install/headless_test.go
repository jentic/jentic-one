package install

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeAnswers(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "answers.yaml")
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write answers: %v", err)
	}
	return path
}

func TestLoadAnswersAppliesOnlyProvidedFields(t *testing.T) {
	path := writeAnswers(t, `
bind_host: 0.0.0.0
app_port: "18000"
database: sqlite
debug: false
apps: [registry, admin]
`)
	a, err := LoadAnswers(path)
	if err != nil {
		t.Fatalf("LoadAnswers: %v", err)
	}
	d := NewDraft()
	a.Apply(d)

	if d.ServerHost != "0.0.0.0" || d.ServerPort != "18000" {
		t.Errorf("server answers not applied: %s:%s", d.ServerHost, d.ServerPort)
	}
	if d.DBBackend != BackendSQLite {
		t.Errorf("database answer not applied: %s", d.DBBackend)
	}
	if d.Debug {
		t.Error("explicit debug: false not applied (pointer semantics broken)")
	}
	if len(d.Apps) != 2 || d.Apps[0] != "registry" || d.Apps[1] != "admin" {
		t.Errorf("apps answer not applied: %v", d.Apps)
	}
	// Unlisted fields keep the wizard defaults.
	if d.BrokerPort != DefaultBrokerPort || d.RuntimePath != RuntimeDocker || d.LogLevel != "DEBUG" {
		t.Errorf("unlisted fields must keep defaults: broker=%s runtime=%s log=%s",
			d.BrokerPort, d.RuntimePath, d.LogLevel)
	}
}

func TestLoadAnswersAppliesPGExpose(t *testing.T) {
	path := writeAnswers(t, "pg_expose_host_port: true\npg_port: \"15432\"\n")
	a, err := LoadAnswers(path)
	if err != nil {
		t.Fatalf("LoadAnswers: %v", err)
	}
	d := NewDraft()
	a.Apply(d)

	if !d.PGExposeHostPort || d.PGPort != "15432" {
		t.Errorf("pg expose answers not applied: expose=%v port=%s", d.PGExposeHostPort, d.PGPort)
	}
	// And the default without the answer stays off (#992).
	if NewDraft().PGExposeHostPort {
		t.Error("PGExposeHostPort must default to off")
	}
}

// A typoed key silently keeping a default would defeat the point of an
// unattended install, so unknown keys are an error.
func TestLoadAnswersRejectsUnknownKeys(t *testing.T) {
	path := writeAnswers(t, "bind_hots: 127.0.0.1\n")
	if _, err := LoadAnswers(path); err == nil || !strings.Contains(err.Error(), "bind_hots") {
		t.Errorf("expected an unknown-key error naming bind_hots, got %v", err)
	}
}

// ValidateDraft holds a headless draft to exactly the wizard's rules; the
// error names the answers-file key so an unattended failure is actionable.
func TestValidateDraft(t *testing.T) {
	valid := func() *Draft {
		d := NewDraft()
		d.RuntimePath = RuntimeDocker
		return d
	}
	if err := ValidateDraft(valid()); err != nil {
		t.Fatalf("wizard defaults must validate, got: %v", err)
	}

	cases := []struct {
		name    string
		mutate  func(*Draft)
		wantKey string
	}{
		{"bad runtime", func(d *Draft) { d.RuntimePath = "vm" }, "runtime"},
		{"bad backend", func(d *Draft) { d.DBBackend = "mysql" }, "database"},
		{"no apps", func(d *Draft) { d.Apps = nil }, "apps"},
		{"unknown surface", func(d *Draft) { d.Apps = []string{"broker"} }, "apps"},
		{"hostname under docker", func(d *Draft) { d.ServerHost = "myhost.internal" }, "bind_host"},
		{"bad app port", func(d *Draft) { d.ServerPort = "http" }, "app_port"},
		{"broker port collision", func(d *Draft) { d.BrokerPort = d.ServerPort }, "broker_port"},
		{"pg port invalid", func(d *Draft) { d.PGPort = "0" }, "pg_port"},
		{"pg user empty", func(d *Draft) { d.PGUser = "" }, "pg_user"},
		{"sqlite dir empty", func(d *Draft) { d.DBBackend = BackendSQLite; d.SQLiteDir = "" }, "sqlite_dir"},
		{"sso without client id", func(d *Draft) { d.SSOEnabled = true }, "sso_client_id"},
		{"bad log level", func(d *Draft) { d.LogLevel = "TRACE" }, "log_level"},
		{"bad metrics", func(d *Draft) { d.MetricsExporter = "statsd" }, "metrics"},
		{"bad tracing", func(d *Draft) { d.TracingExporter = "zipkin" }, "tracing"},
		{"log file without name", func(d *Draft) { d.LogFileName = "" }, "log_file_name"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d := valid()
			tc.mutate(d)
			err := ValidateDraft(d)
			if err == nil {
				t.Fatal("expected a validation error")
			}
			if !strings.Contains(err.Error(), tc.wantKey) {
				t.Errorf("error should name the %q key, got: %v", tc.wantKey, err)
			}
		})
	}
}

// The local (source) path accepts a hostname bind — only Docker publish
// prefixes require an IP.
func TestValidateDraftLocalAllowsHostname(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeSource
	d.ServerHost = "myhost.internal"
	if err := ValidateDraft(d); err != nil {
		t.Errorf("local path should accept a hostname bind, got: %v", err)
	}
}
