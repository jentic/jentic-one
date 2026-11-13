package ctlcmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/jentic/jentic-one/cli/internal/update"
)

func installLocal(ctx context.Context, a *app, draft *install.Draft, configPath string, freshVenv bool, explicitRef string) error {
	venvDir := a.Paths.VenvPath()
	srcDir := a.Paths.SrcPath()

	// --fresh-venv: wipe a wedged/half-populated venv from a prior failed
	// install so this build starts clean, rather than reusing a partial one
	// (P1-E). Explicit, never implicit — a normal reinstall reuses the venv.
	if freshVenv {
		if err := os.RemoveAll(venvDir); err != nil {
			return fmt.Errorf("wipe existing venv for --fresh-venv: %w", err)
		}
		fmt.Fprintln(a.Out, theme.Dim.Render("  wiped existing venv (--fresh-venv)"))
	}

	// uv drives the local build; bootstrap it when missing so onboarding does
	// not dead-end on a tool the installer can provide itself.
	install.EnsureUv(a.Out)

	// Preflight: confirm required tools are available before doing any work.
	results := install.Preflight(ctx, draft)
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderPreflight(results))
	if missing := install.Missing(results); len(missing) > 0 {
		return install.MissingError(missing)
	}

	plan := install.PlanLocalBuild(venvDir, srcDir).AtRef(a.resolveStackBuildRef(explicitRef))
	if plan.PinnedRefIgnored() {
		return refIgnoredError(plan.Ref, plan.SourceDir)
	}
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, plan.RenderHeader())

	if err := plan.Execute(a.Out); err != nil {
		// A failed build can leave a half-populated venv the next run would
		// reuse as-is. Point at the explicit clean-rebuild rather than silently
		// deleting (P1-E) — mirrors the Docker fresh-volume recovery hint.
		fmt.Fprintln(a.Out, theme.Warnf("if the venv is wedged, re-run with --fresh-venv to rebuild it clean"))
		return fmt.Errorf("build failed: %w", err)
	}
	draft.VenvPython = plan.VenvPython()
	if plan.FromGit {
		draft.StackRef = plan.Ref
	}

	// Apply migrations for real. On the SQLite path wrap them in the shared
	// snapshot/rollback net so a failed first-install migration restores the
	// pre-migration bytes instead of stranding a half-applied schema (P1-E) —
	// the same helper `update` uses. Postgres stays the non-fatal keep-install
	// path (the DB may simply not be up yet), so it does not go through the net.
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderMigrateHeader(configPath))
	if draft.IsPostgres() {
		if err := install.RunMigrations(ctx, a.Out, draft.VenvPython, configPath); err != nil {
			fmt.Fprintln(a.Out, install.RenderMigrateWarning(err))
			return nil
		}
		draft.MigrationsDone = true
		return nil
	}
	if err := update.MigrateWithRollback(
		a.Paths.DataDir(),
		true, // local install is SQLite here (Postgres handled above)
		func() error { return install.RunMigrations(ctx, a.Out, draft.VenvPython, configPath) },
		func() { fmt.Fprintln(a.Out, theme.Dim.Render("  snapshotted SQLite data for rollback")) },
		func() {
			fmt.Fprintln(a.Out, theme.Warnf("migrations failed; rolled the SQLite database back to its pre-install state"))
		},
	); err != nil {
		return err
	}
	draft.MigrationsDone = true
	return nil
}

// startAppBackground launches the freshly installed app (and the broker, on its
// own port) in the background and records the results on the draft for the
// summary. A failure to start is non-fatal: the install is otherwise complete
// and the user can start things manually from the printed next steps.
func (a *app) startAppBackground(draft *install.Draft, configPath, logsDir string) {
	pidPath := a.Paths.AppPIDPath()
	logPath := filepath.Join(logsDir, "app.log")

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, install.RenderStartHeader())
	pid, err := install.StartApp(draft.VenvPython, configPath, logPath, pidPath)
	if err != nil {
		fmt.Fprintln(a.Out, install.RenderStartWarning(err))
		return
	}
	draft.AppStarted = true
	draft.AppPID = pid
	fmt.Fprintln(a.Out, theme.Successf("  App started (pid %d)", pid))

	// The broker runs as its own process on its dedicated port.
	brokerPID, err := install.StartBroker(
		draft.VenvPython, configPath,
		filepath.Join(logsDir, "broker.log"), a.Paths.BrokerPIDPath(), draft.BrokerPort,
	)
	if err != nil {
		fmt.Fprintln(a.Out, install.RenderStartWarning(err))
		return
	}
	draft.BrokerStarted = true
	draft.BrokerPID = brokerPID
	fmt.Fprintln(a.Out, theme.Successf("  Broker started (pid %d, port %s)", brokerPID, draft.BrokerPort))
}
