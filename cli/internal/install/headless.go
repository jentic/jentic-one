// headless.go — non-interactive install support (#992).
//
// The install wizard is a TTY-only TUI, which made unattended installs
// (CI, scripted server provisioning) impossible. `jenticctl install
// --defaults` takes the wizard's defaults as-is; `--answers <file>` overlays
// explicit answers from YAML. Both paths run the same validators the wizard
// applies field-by-field, so a headless install cannot construct a draft the
// wizard would have rejected.

package install

import (
	"errors"
	"fmt"
	"os"
	"slices"
	"strings"

	"gopkg.in/yaml.v3"
)

// Answers mirrors the wizard-editable Draft fields for the --answers file.
// Pointer fields distinguish "not provided" (keep the default) from an
// explicit zero value ("debug: false"). Field names are the wizard's own
// vocabulary, grouped as the hub sections present them.
type Answers struct {
	// Deployment: "docker" (default) or "source".
	Runtime *string `yaml:"runtime"`

	// Components.
	Apps *[]string `yaml:"apps"`

	// Database: "postgres" (default) or "sqlite".
	Database   *string `yaml:"database"`
	PGHost     *string `yaml:"pg_host"`
	PGPort     *string `yaml:"pg_port"`
	PGName     *string `yaml:"pg_name"`
	PGUser     *string `yaml:"pg_user"`
	PGPassword *string `yaml:"pg_password"`
	// PGExpose publishes the managed Postgres 5432 on the host (Docker path
	// only; off by default — see #992).
	PGExpose  *bool   `yaml:"pg_expose_host_port"`
	SQLiteDir *string `yaml:"sqlite_dir"`

	// Server.
	BindHost   *string `yaml:"bind_host"`
	AppPort    *string `yaml:"app_port"`
	BrokerPort *string `yaml:"broker_port"`

	// Auth.
	BaseURL         *string `yaml:"base_url"`
	SSOEnabled      *bool   `yaml:"sso_enabled"`
	SSOClientID     *string `yaml:"sso_client_id"`
	SSOClientSecret *string `yaml:"sso_client_secret"`

	// Runtime knobs.
	Debug    *bool   `yaml:"debug"`
	LogLevel *string `yaml:"log_level"`

	// Logging.
	LogFile     *bool   `yaml:"log_file"`
	LogFileName *string `yaml:"log_file_name"`

	// Observability.
	Metrics *string `yaml:"metrics"`
	Tracing *string `yaml:"tracing"`
}

// LoadAnswers reads and strictly decodes an answers file: unknown keys are an
// error (a typoed key silently keeping a default would defeat the point of an
// unattended install).
func LoadAnswers(path string) (*Answers, error) {
	data, err := os.ReadFile(path) //nolint:gosec // the operator's own answers file; reading it is the point.
	if err != nil {
		return nil, fmt.Errorf("read answers file: %w", err)
	}
	var a Answers
	dec := yaml.NewDecoder(strings.NewReader(string(data)))
	dec.KnownFields(true)
	if err := dec.Decode(&a); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	return &a, nil
}

// Apply overlays the provided answers onto the draft. Only fields present in
// the file are touched; everything else keeps the NewDraft default.
func (a *Answers) Apply(d *Draft) {
	setS := func(dst *string, src *string) {
		if src != nil {
			*dst = *src
		}
	}
	setB := func(dst *bool, src *bool) {
		if src != nil {
			*dst = *src
		}
	}
	setS(&d.RuntimePath, a.Runtime)
	if a.Apps != nil {
		d.Apps = *a.Apps
	}
	setS(&d.DBBackend, a.Database)
	setS(&d.PGHost, a.PGHost)
	setS(&d.PGPort, a.PGPort)
	setS(&d.PGName, a.PGName)
	setS(&d.PGUser, a.PGUser)
	setS(&d.PGPassword, a.PGPassword)
	setB(&d.PGExposeHostPort, a.PGExpose)
	setS(&d.SQLiteDir, a.SQLiteDir)
	setS(&d.ServerHost, a.BindHost)
	setS(&d.ServerPort, a.AppPort)
	setS(&d.BrokerPort, a.BrokerPort)
	setS(&d.AuthBaseURL, a.BaseURL)
	setB(&d.SSOEnabled, a.SSOEnabled)
	setS(&d.SSOClientID, a.SSOClientID)
	setS(&d.SSOClientSecret, a.SSOClientSecret)
	setB(&d.Debug, a.Debug)
	setS(&d.LogLevel, a.LogLevel)
	setB(&d.LogFileEnabled, a.LogFile)
	setS(&d.LogFileName, a.LogFileName)
	setS(&d.MetricsExporter, a.Metrics)
	setS(&d.TracingExporter, a.Tracing)
}

// ValidateDraft applies the wizard's field validators to a headless draft, so
// --defaults/--answers installs are held to exactly the interactive rules. The
// error names the offending answer-file key.
func ValidateDraft(d *Draft) error {
	check := func(field string, err error) error {
		if err != nil {
			return fmt.Errorf("%s: %w", field, err)
		}
		return nil
	}
	if d.RuntimePath != RuntimeDocker && d.RuntimePath != RuntimeSource {
		return fmt.Errorf("runtime: must be %q or %q", RuntimeDocker, RuntimeSource)
	}
	if d.DBBackend != BackendPostgres && d.DBBackend != BackendSQLite {
		return fmt.Errorf("database: must be %q or %q", BackendPostgres, BackendSQLite)
	}
	if len(d.Apps) == 0 {
		return errors.New("apps: select at least one surface")
	}
	for _, app := range d.Apps {
		if !slices.Contains(AllSurfaces, app) {
			return fmt.Errorf("apps: unknown surface %q (valid: %s)", app, strings.Join(AllSurfaces, ", "))
		}
	}
	if err := check("bind_host", bindHostValidator(d)(d.ServerHost)); err != nil {
		return err
	}
	if err := check("app_port", validatePort(d.ServerPort)); err != nil {
		return err
	}
	if err := check("broker_port", brokerPortValidator(d)(d.BrokerPort)); err != nil {
		return err
	}
	if d.IsPostgres() {
		if err := check("pg_host", notEmpty("host")(d.PGHost)); err != nil {
			return err
		}
		if err := check("pg_port", validatePort(d.PGPort)); err != nil {
			return err
		}
		if err := check("pg_name", notEmpty("name")(d.PGName)); err != nil {
			return err
		}
		if err := check("pg_user", notEmpty("user")(d.PGUser)); err != nil {
			return err
		}
	} else if err := check("sqlite_dir", notEmpty("directory")(d.SQLiteDir)); err != nil {
		return err
	}
	if d.SSOEnabled {
		if err := check("sso_client_id", notEmpty("client id")(d.SSOClientID)); err != nil {
			return err
		}
		if err := check("sso_client_secret", notEmpty("client secret")(d.SSOClientSecret)); err != nil {
			return err
		}
	}
	if !slices.Contains([]string{"DEBUG", "INFO", "WARNING", "ERROR"}, d.LogLevel) {
		return errors.New("log_level: must be one of DEBUG, INFO, WARNING, ERROR")
	}
	if !slices.Contains([]string{"none", "otlp", "prometheus"}, d.MetricsExporter) {
		return errors.New("metrics: must be one of none, otlp, prometheus")
	}
	if !slices.Contains([]string{"none", "otlp"}, d.TracingExporter) {
		return errors.New("tracing: must be one of none, otlp")
	}
	if d.LogFileEnabled && d.LogFileName == "" {
		return errors.New("log_file_name: must not be empty when log_file is enabled")
	}
	return nil
}
