package install

import (
	"errors"
	"fmt"
	"net"
	"strconv"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// Section is one configurable area on the wizard hub. Each section renders a
// detail pane (Blurb + Summary of current values) and, when selected, a small
// form of Groups bound into the Draft.
type Section struct {
	// ID is a stable identifier (used in logs/tests).
	ID string
	// Title is the row label and editor header.
	Title string
	// Blurb is a one-line description shown in the detail pane.
	Blurb string
	// Groups builds the editor form (grouped so conditional fields can hide).
	Groups func(d *Draft) []*huh.Group
	// Summary returns the "current values" lines shown in the detail pane.
	Summary func(d *Draft) []string
}

// Sections is the hub menu, top to bottom. Append a section to extend the wizard.
var Sections = []Section{
	componentsSection,
	databaseSection,
	authSection,
	serverSection,
	runtimeSection,
	loggingSection,
	observabilitySection,
}

// --- shared validators ------------------------------------------------------

func validatePort(s string) error {
	n, err := strconv.Atoi(s)
	if err != nil {
		return errors.New("must be a number")
	}
	if n < 1 || n > 65535 {
		return errors.New("must be between 1 and 65535")
	}
	return nil
}

func brokerPortValidator(d *Draft) func(string) error {
	return func(s string) error {
		if err := validatePort(s); err != nil {
			return err
		}
		if s == d.ServerPort {
			return errors.New("must differ from the app port")
		}
		if d.IsPostgres() && s == d.PGPort {
			return errors.New("must differ from the Postgres port")
		}
		return nil
	}
}

func notEmpty(field string) func(string) error {
	return func(s string) error {
		if s == "" {
			return fmt.Errorf("%s must not be empty", field)
		}
		return nil
	}
}

// bindHostValidator validates the server bind-host answer. On the Docker path
// the answer doubles as the host-IP prefix of the compose port publishes
// (see Draft.PublishHost), and Docker only accepts an IP there — so a
// hostname other than localhost is rejected up front rather than failing at
// `compose up`. The local path keeps accepting hostnames (a process bind may
// legitimately use one).
func bindHostValidator(d *Draft) func(string) error {
	return func(s string) error {
		if s == "" {
			return errors.New("host must not be empty")
		}
		if !d.IsDocker() || s == "localhost" {
			return nil
		}
		if net.ParseIP(s) == nil {
			return errors.New("must be an IP address (or localhost) — Docker publishes ports on an IP")
		}
		return nil
	}
}

func yesNo(b bool) string {
	if b {
		return "yes"
	}
	return "no"
}

// --- deployment (page 1, shown before the hub) ------------------------------

// deployGroups builds the first page: how to run jentic-one.
func deployGroups(d *Draft) []*huh.Group {
	return []*huh.Group{
		huh.NewGroup(
			huh.NewSelect[string]().
				Title("How do you want to run jentic-one?").
				Description("Docker isolates the app and its data from your host (recommended).").
				Options(
					huh.NewOption("Run in Docker", RuntimeDocker),
					huh.NewOption("Run locally", RuntimeSource),
				).
				Value(&d.RuntimePath),
		),
	}
}

// deployDetail returns the right-side panel text shown on the deployment page.
// It updates live as the highlighted option changes (huh binds the select value
// on every cursor move), giving Docker a brief overview and Local a caution.
func deployDetail(runtime string) string {
	switch runtime {
	case RuntimeDocker:
		return headingStyle.Render("Run in Docker") + "\n" +
			mutedStyle.Render(
				"Containerized stack via docker compose. The app, its database,\n"+
					"and your secrets run in isolated containers, kept separate from\n"+
					"your host and from any agent you run.\n\n"+
					"The recommended way to run jentic-one.")
	case RuntimeSource:
		return headingStyle.Render("Run locally") + "\n" +
			warnStyle.Render("Not recommended.") + " " +
			mutedStyle.Render(
				"Runs from source (uv) directly on your\n"+
					"host. The agent and your secrets end up in the same place,\n"+
					"with no container boundary between them — use with caution.")
	}
	return ""
}

// --- components -------------------------------------------------------------

var componentsSection = Section{
	ID:    "components",
	Title: "Components",
	Blurb: "Which surfaces run in the combined app (the broker always runs as its own service).",
	Groups: func(d *Draft) []*huh.Group {
		opts := make([]huh.Option[string], 0, len(AllSurfaces))
		for _, s := range AllSurfaces {
			opts = append(opts, huh.NewOption(s, s))
		}
		return []*huh.Group{
			huh.NewGroup(
				huh.NewMultiSelect[string]().
					Title("Which surfaces should be enabled?").
					Description("Maps to the `apps` list. Space to toggle, enter to confirm.").
					Options(opts...).
					Value(&d.Apps).
					Validate(func(sel []string) error {
						if len(sel) == 0 {
							return errors.New("select at least one surface")
						}
						return nil
					}),
			),
		}
	},
	Summary: func(d *Draft) []string {
		return []string{
			theme.Field("surfaces", strings.Join(d.Apps, ", ")),
			theme.Field("broker", "separate service"),
		}
	},
}

// --- database ---------------------------------------------------------------

var databaseSection = Section{
	ID:    "database",
	Title: "Database",
	Blurb: "Postgres (recommended) runs as a managed container and handles concurrent writers; SQLite is single-file with no Docker, best for single-user/dev.",
	Groups: func(d *Draft) []*huh.Group {
		return []*huh.Group{
			huh.NewGroup(
				huh.NewSelect[string]().
					Title("Database backend").
					Options(
						huh.NewOption("PostgreSQL (recommended)", BackendPostgres),
						huh.NewOption("SQLite (single-file, no Docker — single-user/dev)", BackendSQLite),
					).
					Value(&d.DBBackend),
			),
			huh.NewGroup(
				Input().Title("Postgres host").Value(&d.PGHost).Validate(notEmpty("host")),
				Input().Title("Postgres port").Value(&d.PGPort).Validate(validatePort),
				Input().Title("Database name").Value(&d.PGName).Validate(notEmpty("name")),
				Input().Title("Superuser / owner role").
					Description("Used as the base credential; per-surface schemas are isolated by schema_name.").
					Value(&d.PGUser).Validate(notEmpty("user")),
				Input().Title("Password").EchoMode(huh.EchoModePassword).Value(&d.PGPassword),
			).WithHideFunc(func() bool { return !d.IsPostgres() }),
			huh.NewGroup(
				Input().
					Title("SQLite data directory").
					Description("Per-surface *.db files live here (created on first migrate).").
					Value(&d.SQLiteDir).Validate(notEmpty("directory")),
			).WithHideFunc(func() bool { return d.IsPostgres() }),
		}
	},
	Summary: func(d *Draft) []string {
		if d.IsPostgres() {
			return []string{
				theme.Field("backend", "postgres"),
				theme.Field("host", d.PGHost+":"+d.PGPort),
				theme.Field("name", d.PGName),
				theme.Field("user", d.PGUser),
			}
		}
		return []string{
			theme.Field("backend", "sqlite"),
			theme.Field("dir", d.SQLiteDir),
		}
	},
}

// --- auth -------------------------------------------------------------------

var authSection = Section{
	ID:    "auth",
	Title: "Auth",
	Blurb: "Canonical base URL and optional Google SSO (external OIDC provider).",
	Groups: func(d *Draft) []*huh.Group {
		return []*huh.Group{
			huh.NewGroup(
				Input().
					Title("Canonical base URL").
					Description("Leave blank to derive from the server binding.").
					Placeholder(d.BaseURL()).
					Value(&d.AuthBaseURL),
				huh.NewConfirm().
					Title("Enable Google SSO?").
					Description("Adds an external OIDC provider and a generated ID-signing key.").
					Value(&d.SSOEnabled),
			),
			huh.NewGroup(
				Input().Title("Google client ID").
					Value(&d.SSOClientID).Validate(notEmpty("client id")),
				Input().Title("Google client secret").EchoMode(huh.EchoModePassword).
					Value(&d.SSOClientSecret).Validate(notEmpty("client secret")),
			).WithHideFunc(func() bool { return !d.SSOEnabled }),
		}
	},
	Summary: func(d *Draft) []string {
		lines := []string{
			theme.Field("base_url", d.CanonicalBaseURL()),
			theme.Field("google_sso", yesNo(d.SSOEnabled)),
		}
		if d.SSOEnabled && d.SSOClientID != "" {
			lines = append(lines, theme.Field("client_id", d.SSOClientID))
		}
		return lines
	},
}

// --- server -----------------------------------------------------------------

var serverSection = Section{
	ID:    "server",
	Title: "Server",
	Blurb: "Host and port the app binds to (the broker runs on its own port). Under Docker this host also selects the interface ports are published on.",
	Groups: func(d *Draft) []*huh.Group {
		return []*huh.Group{
			huh.NewGroup(
				Input().Title("Bind host").Value(&d.ServerHost).Validate(bindHostValidator(d)),
				Input().Title("App port").Value(&d.ServerPort).Validate(validatePort),
				Input().
					Title("Broker port").
					Description("The broker runs as its own service and needs a port distinct from the app.").
					Value(&d.BrokerPort).
					Validate(brokerPortValidator(d)),
			),
		}
	},
	Summary: func(d *Draft) []string {
		return []string{
			theme.Field("bind", d.ServerHost+":"+d.ServerPort),
			theme.Field("broker", d.ServerHost+":"+d.BrokerPort),
		}
	},
}

// --- runtime ----------------------------------------------------------------

var runtimeSection = Section{
	ID:    "runtime",
	Title: "Runtime",
	Blurb: "Debug mode and log verbosity.",
	Groups: func(d *Draft) []*huh.Group {
		return []*huh.Group{
			huh.NewGroup(
				huh.NewConfirm().Title("Enable debug mode?").Value(&d.Debug),
				huh.NewSelect[string]().
					Title("Log level").
					Options(
						huh.NewOption("DEBUG", "DEBUG"),
						huh.NewOption("INFO", "INFO"),
						huh.NewOption("WARNING", "WARNING"),
						huh.NewOption("ERROR", "ERROR"),
					).
					Value(&d.LogLevel),
			),
		}
	},
	Summary: func(d *Draft) []string {
		return []string{
			theme.Field("debug", yesNo(d.Debug)),
			theme.Field("log_level", d.LogLevel),
		}
	},
}

// --- logging ----------------------------------------------------------------

var loggingSection = Section{
	ID:    "logging",
	Title: "Logging",
	Blurb: "Mirror app logs to a rotating JSON file under ~/.jentic/logs (view with `jenticctl logs`).",
	Groups: func(d *Draft) []*huh.Group {
		return []*huh.Group{
			huh.NewGroup(
				huh.NewConfirm().
					Title("Write logs to a file?").
					Description("Adds a rotating JSON-lines sink in addition to stdout. `jenticctl logs` tails it.").
					Value(&d.LogFileEnabled),
			),
			huh.NewGroup(
				Input().
					Title("Log file name").
					Description("Stored under ~/.jentic/logs. One JSON object per line.").
					Value(&d.LogFileName).
					Validate(notEmpty("file name")),
			).WithHideFunc(func() bool { return !d.LogFileEnabled }),
		}
	},
	Summary: func(d *Draft) []string {
		if !d.LogFileEnabled {
			return []string{theme.Field("file", "no")}
		}
		name := d.LogFileName
		if name == "" {
			name = "app.jsonl"
		}
		return []string{
			theme.Field("file", "yes"),
			theme.Field("name", name),
		}
	},
}

// --- observability ----------------------------------------------------------

var observabilitySection = Section{
	ID:    "observability",
	Title: "Observability",
	Blurb: "Where metrics and traces are exported (none = no collector).",
	Groups: func(d *Draft) []*huh.Group {
		return []*huh.Group{
			huh.NewGroup(
				huh.NewSelect[string]().
					Title("Metrics exporter").
					Options(
						huh.NewOption("none (no collector)", "none"),
						huh.NewOption("otlp", "otlp"),
						huh.NewOption("prometheus", "prometheus"),
					).
					Value(&d.MetricsExporter),
				huh.NewSelect[string]().
					Title("Tracing exporter").
					Options(
						huh.NewOption("none (no collector)", "none"),
						huh.NewOption("otlp", "otlp"),
					).
					Value(&d.TracingExporter),
			),
		}
	},
	Summary: func(d *Draft) []string {
		return []string{
			theme.Field("metrics", d.MetricsExporter),
			theme.Field("tracing", d.TracingExporter),
		}
	},
}
