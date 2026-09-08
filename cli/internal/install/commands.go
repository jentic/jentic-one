package install

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/config"
)

// Step is a single labelled group of shell commands to run next.
type Step struct {
	Title    string
	Commands []string
}

// SetupState is a tri-state describing whether the freshly installed stack
// still needs its first admin account, resolved from a live /health probe. It
// lets the summary tell the truth instead of unconditionally asserting "no
// users exist yet" — which is false when a re-install reuses a database that
// already has an admin (e.g. uninstall left the SQLite file behind).
type SetupState int

const (
	// SetupUnknown means the installer could not determine the state (no probe,
	// or the probe failed); fall back to the generic first-run guidance.
	SetupUnknown SetupState = iota
	// SetupRequired means the database has no users yet — create the first admin.
	SetupRequired
	// SetupComplete means an admin already exists — sign in instead of creating.
	SetupComplete
)

// NextSteps returns the ordered shell steps to finish bringing the stack up for
// the chosen path. The installer runs the build/migrate/start itself; these are
// the remaining manual steps (e.g. when --skip-build or --no-start was used).
// configPath is the path the generated config was written to. setup reflects a
// live /health probe so the create-first-admin step is only shown when the DB
// actually has no users.
func (d *Draft) NextSteps(configPath string, setup SetupState) []Step {
	if d.IsDocker() {
		return d.dockerSteps(setup)
	}
	return d.sourceSteps(configPath, setup)
}

func (d *Draft) sourceSteps(configPath string, setup SetupState) []Step {
	env := "JENTIC_CONFIG_FILE=" + configPath

	// When the wizard already built the venv, drive the remaining steps with that
	// interpreter directly; otherwise fall back to the in-repo make/uv workflow.
	py := d.VenvPython
	built := py != ""
	if !built {
		py = "uv run python"
	}

	var steps []Step
	if !built {
		steps = append(steps, Step{
			Title:    "Build (sync dependencies + dev setup)",
			Commands: []string{"make install"},
		})
	}

	if !d.MigrationsDone {
		migrateTitle := "Run migrations (creates the SQLite databases)"
		if d.IsPostgres() {
			migrateTitle = fmt.Sprintf("Run migrations (PostgreSQL must be reachable at %s:%s)", d.PGHost, d.PGPort)
		}
		steps = append(steps, Step{
			Title:    migrateTitle,
			Commands: []string{fmt.Sprintf("%s %s -m jentic_one.migrations.run", env, py)},
		})
	}

	// The wizard already started the app in the background; don't tell the user
	// to start it again.
	if !d.AppStarted {
		steps = append(steps, Step{
			Title:    "Start the app",
			Commands: []string{fmt.Sprintf("%s %s -m jentic_one", env, py)},
		})
	}

	// The broker runs as its own process on its dedicated port (it cannot be
	// bundled with the combined app). `jenticctl start` launches both together.
	if !d.BrokerStarted {
		steps = append(steps, Step{
			Title: "Start the broker (separate service, port " + d.BrokerPort + ")",
			Commands: []string{
				fmt.Sprintf("%s JENTIC__APPS=broker JENTIC__SERVER__PORT=%s %s -m jentic_one", env, d.BrokerPort, py),
			},
		})
	}

	// No-credential first-run: the DB ships with zero users, so creating the
	// first admin is always a required next step (there is no seeded account) —
	// unless a live probe found an admin already exists (re-install over an
	// existing DB), in which case sign-in is the next step instead.
	steps = append(steps, firstAdminStep(setup, d.BaseURL()))
	steps = append(steps, connectAgentStep(d.BaseURL()))
	return steps
}

// firstAdminStep is the create-the-first-admin step shared by every install
// path. The no-credential model means a fresh DB has no users, so this is the
// one manual step the operator must always run before they can sign in. When a
// live probe reports an admin already exists (SetupComplete), it becomes a
// sign-in pointer instead so the summary never contradicts reality.
func firstAdminStep(setup SetupState, baseURL string) Step {
	if setup == SetupComplete {
		signInURL := config.AppURL(baseURL, "login")
		return Step{
			Title:    "Sign in (an admin account already exists on this database)",
			Commands: []string{signInURL},
		}
	}
	return Step{
		Title:    "Create the first admin (one-time; no default account exists)",
		Commands: []string{"jenticctl setup"},
	}
}

// dockerSteps returns the post-install steps for the containerized path. The
// wizard builds the image, writes the compose stack, and (unless --no-start)
// brings it up, so the only remaining step is starting the stack when it was
// not started for you.
func (d *Draft) dockerSteps(setup SetupState) []Step {
	var steps []Step
	if !d.AppStarted {
		steps = append(steps, Step{
			Title:    "Start the stack (docker compose up -d)",
			Commands: []string{"jenticctl start"},
		})
	}
	// No-credential first-run applies to the Docker path too: create the first
	// admin once the stack is up (or sign in if the DB already has one).
	steps = append(steps, firstAdminStep(setup, d.BaseURL()))
	steps = append(steps, connectAgentStep(d.BaseURL()))
	return steps
}

// connectAgentStep tells the operator how to attach an agent to the install they
// just stood up. It uses d.BaseURL() (always 127.0.0.1, never localhost) because
// the token-exchange audience is an exact-string match against the backend's
// canonical_base_url — `--url http://localhost:...` would fail with invalid_grant.
// register also seeds the environment's broker_url for a loopback install, so
// `jentic execute` works without any extra broker flags.
func connectAgentStep(baseURL string) Step {
	return Step{
		Title: "Connect an agent (use 127.0.0.1, not localhost)",
		Commands: []string{
			"jentic register --url " + baseURL,
		},
	}
}

// FirstRunNote is the post-install reminder for the no-credential first-run
// model: the database ships with zero users, so the operator must create the
// first admin before they can sign in. There is no default password to rotate.
const FirstRunNote = "First run: no users exist yet. Create the first admin with `jenticctl setup` " +
	"(or open /app/setup in the UI). There is no default password."

// AdminExistsNote replaces FirstRunNote when a live probe finds the database
// already has an admin (e.g. a re-install over a database that uninstall left
// behind). It steers the operator to sign in rather than create an account.
const AdminExistsNote = "This database already has an admin account — sign in with your existing " +
	"credentials. To start completely fresh, uninstall and remove the data directory before reinstalling."

// setupNote returns the onboarding note matching the live setup state.
func setupNote(setup SetupState) string {
	if setup == SetupComplete {
		return AdminExistsNote
	}
	return FirstRunNote
}

// RenderSummary returns the styled post-install summary: where the config, data,
// and logs live, the next-step commands for the chosen path, and onboarding
// notes. dataDir and logsDir may be empty if they could not be resolved. setup
// reflects a live /health probe so the first-admin guidance matches reality
// instead of always claiming "no users exist yet".
func RenderSummary(d *Draft, configPath, dataDir, logsDir string, setup SetupState) string {
	var b strings.Builder

	b.WriteString(successStyle.Render("Configuration written to " + configPath))
	b.WriteString("\n\n")

	b.WriteString(headingStyle.Render("Locations (~/.jentic)"))
	b.WriteString("\n")
	b.WriteString("  config: " + commandStyle.Render(configPath) + "\n")
	switch {
	case d.IsDocker() && !d.IsPostgres():
		// SQLite lives in a Docker named volume, not on the host. Show the
		// fully-qualified (project-prefixed) name so it matches `docker volume ls`.
		b.WriteString("  data:   " + commandStyle.Render("docker volume "+DataVolumeNames(false)[0]) + "\n")
	case !d.IsPostgres() && dataDir != "":
		b.WriteString("  data:   " + commandStyle.Render(dataDir) + "\n")
	}
	if logsDir != "" {
		b.WriteString("  logs:   " + commandStyle.Render(logsDir) + "\n")
	}
	b.WriteString("\n")

	if d.AppStarted {
		if d.IsDocker() {
			b.WriteString(headingStyle.Render("Stack (running in Docker)"))
			b.WriteString("\n")
			if d.ComposePath != "" {
				b.WriteString("  compose: " + commandStyle.Render(d.ComposePath) + "\n")
				b.WriteString("  logs:    " + commandStyle.Render("docker compose -p "+composeProjectName+" -f "+d.ComposePath+" logs -f") + "\n")
			}
			b.WriteString("  url:     " + commandStyle.Render(d.BaseURL()) + "\n")
			b.WriteString("  broker:  " + commandStyle.Render(d.BrokerURL()) + "\n")
			b.WriteString("  stop:    " + commandStyle.Render("jenticctl stop") + "\n")
		} else {
			b.WriteString(headingStyle.Render("App (running in background)"))
			b.WriteString("\n")
			b.WriteString("  pid:    " + commandStyle.Render(strconv.Itoa(d.AppPID)) + "\n")
			b.WriteString("  url:    " + commandStyle.Render(d.BaseURL()) + "\n")
			if d.BrokerStarted {
				b.WriteString("  broker: " + commandStyle.Render(fmt.Sprintf("%s (pid %d)", d.BrokerURL(), d.BrokerPID)) + "\n")
			}
			b.WriteString("  logs:   " + commandStyle.Render("jenticctl logs -f") + "\n")
			b.WriteString("  stop:   " + commandStyle.Render("jenticctl stop") + "\n")
		}
		b.WriteString("\n")
	}

	b.WriteString(headingStyle.Render("Next steps"))
	b.WriteString("\n")

	for i, step := range d.NextSteps(configPath, setup) {
		b.WriteString(stepStyle.Render("  " + strconv.Itoa(i+1) + ". " + step.Title))
		b.WriteString("\n")
		for _, cmd := range step.Commands {
			b.WriteString("     " + commandStyle.Render(cmd))
			b.WriteString("\n")
		}
	}

	b.WriteString("\n")
	b.WriteString(mutedStyle.Render(setupNote(setup)))
	b.WriteString("\n")

	return b.String()
}
