package install

import (
	"strings"
	"testing"
)

func TestSectionsRegistryWellFormed(t *testing.T) {
	if len(Sections) == 0 {
		t.Fatal("Sections registry is empty")
	}
	ids := map[string]bool{}
	d := NewDraft()
	for _, s := range Sections {
		if s.ID == "" || s.Title == "" {
			t.Errorf("section has empty ID/Title: %+v", s)
		}
		if ids[s.ID] {
			t.Errorf("duplicate section ID %q", s.ID)
		}
		ids[s.ID] = true
		if s.Groups == nil || s.Summary == nil {
			t.Errorf("section %q missing Groups/Summary func", s.ID)
			continue
		}
		// Build the form groups and summary lines; must not panic.
		if groups := s.Groups(d); len(groups) == 0 {
			t.Errorf("section %q produced no form groups", s.ID)
		}
		_ = s.Summary(d)
	}
}

func TestDatabaseSummarySwitchesBackend(t *testing.T) {
	d := NewDraft()
	d.DBBackend = BackendSQLite
	d.SQLiteDir = "/data"
	sqlite := strings.Join(databaseSection.Summary(d), "\n")
	if !strings.Contains(sqlite, "sqlite") || !strings.Contains(sqlite, "/data") {
		t.Errorf("sqlite summary = %q", sqlite)
	}

	d.DBBackend = BackendPostgres
	d.PGHost = "db"
	d.PGPort = "5432"

	// Local path: the user's own Postgres — the summary shows where it is.
	d.RuntimePath = RuntimeSource
	pg := strings.Join(databaseSection.Summary(d), "\n")
	if !strings.Contains(pg, "postgres") || !strings.Contains(pg, "db:5432") {
		t.Errorf("postgres (local) summary = %q", pg)
	}

	// Docker path: the managed container has no user-facing host; the summary
	// shows the exposure decision instead (#992).
	d.RuntimePath = RuntimeDocker
	pg = strings.Join(databaseSection.Summary(d), "\n")
	if !strings.Contains(pg, "managed container") || !strings.Contains(pg, "no (compose network only)") {
		t.Errorf("postgres (docker, unexposed) summary = %q", pg)
	}
	d.PGExposeHostPort = true
	pg = strings.Join(databaseSection.Summary(d), "\n")
	if !strings.Contains(pg, "yes (host port 5432)") {
		t.Errorf("postgres (docker, exposed) summary = %q", pg)
	}
}

func TestAuthSummaryShowsSSO(t *testing.T) {
	d := NewDraft()
	off := strings.Join(authSection.Summary(d), "\n")
	if !strings.Contains(off, "no") {
		t.Errorf("expected google_sso: no, got %q", off)
	}

	d.SSOEnabled = true
	d.SSOClientID = "client-1"
	on := strings.Join(authSection.Summary(d), "\n")
	if !strings.Contains(on, "yes") || !strings.Contains(on, "client-1") {
		t.Errorf("expected SSO enabled with client id, got %q", on)
	}
}

func TestLoggingSummaryReflectsFileSink(t *testing.T) {
	d := NewDraft()
	d.LogFileEnabled = false
	off := strings.Join(loggingSection.Summary(d), "\n")
	if !strings.Contains(off, "no") {
		t.Errorf("disabled sink summary = %q", off)
	}

	d.LogFileEnabled = true
	d.LogFileName = "custom.jsonl"
	on := strings.Join(loggingSection.Summary(d), "\n")
	if !strings.Contains(on, "yes") || !strings.Contains(on, "custom.jsonl") {
		t.Errorf("enabled sink summary = %q", on)
	}
}

func TestValidatePort(t *testing.T) {
	for _, ok := range []string{"1", "8000", "65535"} {
		if err := validatePort(ok); err != nil {
			t.Errorf("validatePort(%q) unexpected error: %v", ok, err)
		}
	}
	for _, bad := range []string{"0", "-1", "65536", "abc", ""} {
		if err := validatePort(bad); err == nil {
			t.Errorf("validatePort(%q) should fail", bad)
		}
	}
}

func TestNotEmpty(t *testing.T) {
	v := notEmpty("host")
	if err := v(""); err == nil {
		t.Errorf("empty value should fail validation")
	}
	if err := v("x"); err != nil {
		t.Errorf("non-empty value should pass: %v", err)
	}
}

func TestBrokerPortValidation(t *testing.T) {
	d := NewDraft()
	d.DBBackend = BackendPostgres
	d.PGPort = "5432"
	d.ServerPort = "8000"

	validate := brokerPortValidator(d)

	if err := validate("9000"); err != nil {
		t.Errorf("valid port should pass: %v", err)
	}
	if err := validate("8000"); err == nil {
		t.Error("broker port == app port should fail")
	}
	if err := validate("5432"); err == nil {
		t.Error("broker port == PG port should fail")
	}

	// When SQLite is used, PG port collision is not checked.
	d.DBBackend = BackendSQLite
	validate = brokerPortValidator(d)
	if err := validate("5432"); err != nil {
		t.Errorf("PG port check should not apply for SQLite: %v", err)
	}
}
