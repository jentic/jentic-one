package cmd

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

type startOptions struct {
	config string
}

// requireDockerDaemon guards the Docker runtime commands (`start`/`stop`)
// against a stopped daemon, surfacing the installer's actionable "start Docker
// Desktop" guidance instead of a raw `docker compose` transport error. It is a
// package-level seam so tests can simulate an up/down daemon without a real
// Docker (#783, jentic-api-scorecard#224).
var requireDockerDaemon = install.RequireDockerDaemon

func newStartCmd(app *App) *cobra.Command {
	opts := &startOptions{}
	cmd := &cobra.Command{
		Use:   "start",
		Short: "Start the configured jentic-one app",
		Long: "start launches the locally-installed jentic-one app in the background.\n" +
			"It requires a completed `jenticctl install` (a generated config and a built\n" +
			"venv); if either is missing it tells you to run `jenticctl install` first.",
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			return app.startE(opts)
		},
	}
	cmd.Flags().StringVar(&opts.config, "config", "",
		"path to the generated config (default: ~/.jentic/jentic-one.yaml)")
	return cmd
}

func (a *App) startE(opts *startOptions) error {
	// A generated compose file marks a Docker install: drive the stack with
	// docker compose instead of the local background process.
	composePath := a.Paths.ComposePath()
	if proc.FileExists(composePath) {
		return a.startDocker(composePath)
	}

	pidPath := a.Paths.AppPIDPath()

	// Already running? Don't start a second instance.
	if pid, alive, _ := proc.LivePID(pidPath); alive {
		fmt.Fprintln(a.Out, theme.Infof("App is already running (pid %d). Use `jenticctl stop` first.", pid))
		return nil
	}

	configPath := opts.config
	if configPath == "" {
		configPath = a.Paths.InstallConfigPath()
	}
	if !proc.FileExists(configPath) {
		return fmt.Errorf("not configured: %s not found — run `jenticctl install` first", configPath)
	}

	venvDir := a.Paths.VenvPath()
	py := install.VenvPython(venvDir)
	if !proc.FileExists(py) {
		return fmt.Errorf("no local environment at %s — run `jenticctl install` first", venvDir)
	}

	logsDir, err := a.Paths.Ensure(a.Paths.LogsDir())
	if err != nil {
		return err
	}
	logPath := filepath.Join(logsDir, "app.log")

	fmt.Fprintln(a.Out, theme.Infof("Starting app ..."))
	pid, err := install.StartApp(py, configPath, logPath, pidPath)
	if err != nil {
		_ = os.Remove(pidPath)
		return err
	}

	fmt.Fprintln(a.Out, theme.Successf("App started (pid %d)", pid))

	if err := a.startBroker(py, configPath, logsDir); err != nil {
		return err
	}

	fmt.Fprintln(a.Out, theme.Dim.Render("  logs: jenticctl logs -f"))
	fmt.Fprintln(a.Out, theme.Dim.Render("  stop: jenticctl stop"))
	return nil
}

// startBroker launches the broker as its own background process on the port
// recorded in the install manifest (default 8100). The broker cannot be
// bundled with the combined app, so it always runs separately on the local
// path. A missing broker PID file is fine; an already-running broker is left
// alone.
func (a *App) startBroker(py, configPath, logsDir string) error {
	pidPath := a.Paths.BrokerPIDPath()
	if pid, alive, _ := proc.LivePID(pidPath); alive {
		fmt.Fprintln(a.Out, theme.Infof("Broker is already running (pid %d).", pid))
		return nil
	}

	port := brokerPortFromManifest(a)
	logPath := filepath.Join(logsDir, "broker.log")

	fmt.Fprintln(a.Out, theme.Infof("Starting broker (port %s) ...", port))
	pid, err := install.StartBroker(py, configPath, logPath, pidPath, port)
	if err != nil {
		_ = os.Remove(pidPath)
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Broker started (pid %d)", pid))
	return nil
}

// brokerPortFromManifest returns the broker port recorded at install time,
// falling back to the default when the manifest is missing or unset.
func brokerPortFromManifest(a *App) string {
	if m, _, err := config.LoadManifest(a.Paths); err == nil && m.BrokerPort != "" {
		return m.BrokerPort
	}
	return install.DefaultBrokerPort
}

// startDocker brings the generated docker-compose stack up in detached mode.
func (a *App) startDocker(composePath string) error {
	// The compose file proves a Docker install, but not that the daemon is up:
	// with Docker Desktop closed (common after a reboot) `docker compose up`
	// fails with a raw "Cannot connect to the Docker daemon" error. Probe first
	// so we surface the same actionable "start Docker Desktop" guidance the
	// installer does (#783, jentic-api-scorecard#224).
	if err := requireDockerDaemon("jenticctl start"); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Infof("Starting Docker stack ..."))
	if err := install.ComposeUp(a.Out, composePath); err != nil {
		return fmt.Errorf("docker compose up: %w", err)
	}
	fmt.Fprintln(a.Out, theme.Successf("Stack started."))
	fmt.Fprintln(a.Out, theme.Dimf("  logs: docker compose -f %s logs -f", composePath))
	fmt.Fprintln(a.Out, theme.Dim.Render("  stop: jenticctl stop"))
	return nil
}
