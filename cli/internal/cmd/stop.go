package cmd

import (
	"errors"
	"fmt"
	"os"
	"syscall"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

type stopOptions struct {
	timeout time.Duration
	volumes bool
	yes     bool
}

func newStopCmd(app *App) *cobra.Command {
	opts := &stopOptions{}
	cmd := &cobra.Command{
		Use:   "stop",
		Short: "Stop the background app or Docker stack",
		Long: "stop terminates the background jentic-one process recorded in\n" +
			"~/.jentic/app.pid (started by `jenticctl install` or `jenticctl start`), or tears\n" +
			"down the Docker stack for a containerized install. It sends SIGTERM, waits\n" +
			"for a graceful exit, then escalates to SIGKILL if needed.\n\n" +
			"For a Docker install, --volumes also removes the stack's data volumes\n" +
			"(SQLite data or the managed Postgres data dir). Use it to recover from an\n" +
			"incompatible Postgres image upgrade. This destroys the database.",
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			return app.stopE(opts)
		},
	}
	cmd.Flags().DurationVar(&opts.timeout, "timeout", 10*time.Second,
		"how long to wait for graceful shutdown before SIGKILL")
	cmd.Flags().BoolVar(&opts.volumes, "volumes", false,
		"also remove the Docker stack's data volumes (destroys the database)")
	cmd.Flags().BoolVar(&opts.yes, "yes", false,
		"skip the confirmation prompt for --volumes")
	return cmd
}

func (a *App) stopE(opts *stopOptions) error {
	// A generated compose file marks a Docker install: tear the stack down with
	// docker compose instead of signalling a local process.
	composePath := a.Paths.ComposePath()
	if proc.FileExists(composePath) {
		return a.stopDocker(opts, composePath)
	}

	if opts.volumes {
		fmt.Fprintln(a.Out, theme.Warnf("--volumes has no effect for a local (non-Docker) install; ignoring."))
	}

	pidPath := a.Paths.AppPIDPath()

	appErr := a.stopProcess(pidPath, "app", opts.timeout)
	brokerErr := a.stopProcess(a.Paths.BrokerPIDPath(), "broker", opts.timeout)
	return errors.Join(appErr, brokerErr)
}

// stopProcess terminates the process recorded in pidPath (label names it for
// output), sending SIGTERM, waiting up to timeout, then escalating to SIGKILL.
// A missing or stale PID file is not an error.
func (a *App) stopProcess(pidPath, label string, timeout time.Duration) error {
	pid, err := proc.ReadPIDFile(pidPath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			fmt.Fprintln(a.Out, theme.Dimf("No %s PID file found; nothing to stop.", label))
			return nil
		}
		return err
	}

	p, err := os.FindProcess(pid)
	if err != nil {
		return fmt.Errorf("find process %d: %w", pid, err)
	}

	// Already gone? Clear the stale PID file and report.
	if !proc.Alive(p) {
		_ = os.Remove(pidPath)
		fmt.Fprintln(a.Out, theme.Dimf("%s (pid %d) is not running; cleared stale PID file.", label, pid))
		return nil
	}

	fmt.Fprintln(a.Out, theme.Infof("Stopping %s (pid %d) ...", label, pid))
	if err := p.Signal(syscall.SIGTERM); err != nil {
		return fmt.Errorf("signal process %d: %w", pid, err)
	}

	if proc.WaitForExit(p, timeout) {
		_ = os.Remove(pidPath)
		fmt.Fprintln(a.Out, theme.Successf("Stopped %s (pid %d).", label, pid))
		return nil
	}

	// Didn't exit in time: force kill.
	fmt.Fprintln(a.Out, theme.Warnf("%s did not exit within %s; sending SIGKILL.", label, timeout))
	if err := p.Kill(); err != nil {
		return fmt.Errorf("kill process %d: %w", pid, err)
	}
	_ = os.Remove(pidPath)
	fmt.Fprintln(a.Out, theme.Successf("Killed %s (pid %d).", label, pid))
	return nil
}

// stopDocker tears down the Docker stack. With --volumes it also removes the
// stack's data volumes (`down -v`), which destroys the database; it confirms
// first unless --yes is set.
func (a *App) stopDocker(opts *stopOptions, composePath string) error {
	// `docker compose down` needs a live daemon; with the daemon down it fails
	// with a raw transport error. Probe first (before the destructive --volumes
	// confirmation prompt) so a stopped daemon yields actionable recovery
	// guidance instead (#783). The probe can wait out a cold-starting daemon
	// (~30s), so announce it — otherwise the command looks hung.
	announceDaemonCheck(a.Out)
	if err := requireDockerDaemon("jenticctl stop"); err != nil {
		return err
	}
	if !opts.volumes {
		fmt.Fprintln(a.Out, theme.Infof("Stopping Docker stack ..."))
		if err := composeDown(a.Out, composePath); err != nil {
			return fmt.Errorf("docker compose down: %w", err)
		}
		fmt.Fprintln(a.Out, theme.Successf("Stopped Docker stack."))
		return nil
	}

	if !opts.yes {
		ok, err := confirmRemoveVolumes()
		if err != nil {
			return err
		}
		if !ok {
			fmt.Fprintln(a.Out, theme.Dim.Render("stop --volumes cancelled"))
			return nil
		}
	}

	fmt.Fprintln(a.Out, theme.Infof("Stopping Docker stack and removing volumes ..."))
	if err := composeDownVolumes(a.Out, composePath); err != nil {
		return fmt.Errorf("docker compose down -v: %w", err)
	}
	fmt.Fprintln(a.Out, theme.Successf("Stopped Docker stack and removed its data volumes."))
	// The database is gone, so the next `start` comes up on an empty volume.
	// `start` now creates the schema itself, but say so here too: this is the
	// moment the operator loses their data, and it is where they will look when
	// their login stops working.
	fmt.Fprintln(a.Out, theme.Dim.Render("  the database was deleted; `jenticctl start` will recreate an empty schema"))
	fmt.Fprintln(a.Out, theme.Dim.Render("  you will need to create the admin user again"))
	return nil
}

// confirmRemoveVolumes prompts before the destructive `down -v` since it
// discards the database.
func confirmRemoveVolumes() (bool, error) {
	confirm := false
	if err := install.RunConfirm(
		huh.NewConfirm().
			Title("Remove the stack's data volumes? This permanently deletes the database.").
			Affirmative("Yes, delete the data").
			Negative("Cancel").
			Value(&confirm),
	); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, nil
		}
		return false, err
	}
	return confirm, nil
}
