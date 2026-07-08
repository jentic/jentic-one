// Package cmd wires the Jentic CLI command tree.
package cmd

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// Build-time version metadata. These are overridden via -ldflags
// (-X github.com/jentic/jentic-one/cli/internal/cmd.version=...) by the
// installer and release builds; a plain `go build` keeps the defaults.
var (
	version = "dev"
	commit  = "none"
	date    = "unknown"
)

// newBaseRoot builds a root command with the shared wiring (banner, help
// renderer, version template, command-group ordering) for the given binary
// name. The two binary-specific builders below add their own command sets and
// branding on top.
func newBaseRoot(app *App, binary string) *cobra.Command {
	root := &cobra.Command{
		Use:           binary,
		Version:       version,
		SilenceUsage:  true,
		SilenceErrors: true,
		// Print the jentic wordmark before every command (TTY only; see banner).
		PersistentPreRun: func(cmd *cobra.Command, _ []string) {
			app.banner(cmd)
		},
	}

	root.SetHelpFunc(app.helpFunc)
	root.SetVersionTemplate(
		fmt.Sprintf("%s %s (commit %s, built %s)\n", binary, version, commit, date),
	)

	// Preserve the AddCommand order below in help output (cobra sorts
	// alphabetically by default) so the list follows the onboarding flow.
	cobra.EnableCommandSorting = false

	return root
}

// addGrouped attaches cmd to root under the given group ID.
func addGrouped(root *cobra.Command, groupID string, cmd *cobra.Command) {
	cmd.GroupID = groupID
	root.AddCommand(cmd)
}

// newCtlRootCmd assembles the jenticctl command tree: the installer / lifecycle
// surface (install, doctor, start, stop, logs, status, update, uninstall).
// Every subcommand is built via its constructor (no package-global flag state),
// so the tree can be built repeatedly and exercised in tests.
func newCtlRootCmd(app *App) *cobra.Command {
	root := newBaseRoot(app, "jenticctl")
	root.Short = "jenticctl: install and operate jentic-one locally"
	root.Annotations = map[string]string{"tagline": "install and operate jentic-one locally"}
	root.Long = "jenticctl is the installer and lifecycle companion for jentic-one. It\n" +
		"stands up a local deployment (from source via `uv`, or in Docker via\n" +
		"`docker compose`) and manages the running app: health checks, start/stop,\n" +
		"log tailing, updates, and teardown.\n\n" +
		"New here? Run `jenticctl install` to set up locally. Once installed, use the\n" +
		"`jentic` CLI to register an agent and run against the API catalog. Use\n" +
		"`jenticctl <command> --help` for details on any command."

	root.AddGroup(
		&cobra.Group{ID: "setup", Title: "Setup & lifecycle"},
	)

	addGrouped(root, "setup", newInstallCmd(app))
	addGrouped(root, "setup", newWizardCmd(app))
	addGrouped(root, "setup", newSetupCmd(app))
	addGrouped(root, "setup", newResetPasswordCmd(app))
	addGrouped(root, "setup", newDoctorCmd(app))
	addGrouped(root, "setup", newStatusCmd(app))
	addGrouped(root, "setup", newStartCmd(app))
	addGrouped(root, "setup", newStopCmd(app))
	addGrouped(root, "setup", newLogsCmd(app))
	addGrouped(root, "setup", newUpdateCmd(app))
	addGrouped(root, "setup", newUninstallCmd(app))

	return root
}

// newAPIRootCmd assembles the jentic command tree: the API-spec surface
// (register, profile, logout, catalog, apis) for discovering, inspecting,
// and executing against the Jentic API catalog.
func newAPIRootCmd(app *App) *cobra.Command {
	root := newBaseRoot(app, "jentic")
	root.Short = "jentic: discover, inspect, and run against the Jentic API catalog"
	root.Annotations = map[string]string{"tagline": "discover, inspect, and run against the Jentic API catalog"}
	root.Long = "jentic is the command-line companion for working with the Jentic API\n" +
		"catalog. Register and switch agent identities, browse and import APIs from\n" +
		"the public catalog into your local registry, inspect operations, and execute\n" +
		"against them.\n\n" +
		"New here? Run `jentic register` to create an agent, then browse the catalog\n" +
		"with `jentic apis`. To install and operate jentic-one locally, use the\n" +
		"`jenticctl` CLI (e.g. `jenticctl install`). Use `jentic <command> --help` for details."

	root.AddGroup(
		&cobra.Group{ID: "identity", Title: "Identity & access"},
		&cobra.Group{ID: "apis", Title: "APIs"},
		&cobra.Group{ID: "agent", Title: "Find and run operations"},
		&cobra.Group{ID: "admin", Title: "Administration"},
	)

	addGrouped(root, "identity", newBootstrapCmd(app))
	addGrouped(root, "identity", newRegisterCmd(app))
	addGrouped(root, "identity", newProfileCmd(app))
	addGrouped(root, "identity", newLogoutCmd(app))
	addGrouped(root, "apis", newCatalogCmd(app))
	addGrouped(root, "apis", newApisCmd(app))
	addGrouped(root, "apis", newEndpointsCmd(app))
	addGrouped(root, "agent", newSearchCmd(app))
	addGrouped(root, "agent", newInspectCmd(app))
	addGrouped(root, "agent", newExecuteCmd(app))
	addGrouped(root, "agent", newAccessCmd(app))
	addGrouped(root, "agent", newSkillCmd(app))
	addGrouped(root, "admin", newAdminCmd(app))

	return root
}

// ExecuteCtl runs the jenticctl (installer / lifecycle) command tree and exits
// with an appropriate status code.
func ExecuteCtl() {
	os.Exit(runRoot(newCtlRootCmd))
}

// ExecuteAPI runs the jentic (API-spec) command tree and exits with an
// appropriate status code.
func ExecuteAPI() {
	os.Exit(runRoot(newAPIRootCmd))
}

// defaultContainer builds the default injection container (no extra commands).
// A downstream package builds its own core.AppContainer{ExtraCommands: ...} and
// calls core.NewRootCmd directly from its own main.go.
func defaultContainer() *core.AppContainer {
	return &core.AppContainer{Out: os.Stdout, Err: os.Stderr}
}

// appFromContainer derives the internal App (resolved paths + streams) from the
// injected container. Paths are resolved here — the exported core package stays
// free of the internal config package, keeping the dependency edge
// internal/cmd → pkg/core one-directional.
func appFromContainer(deps *core.AppContainer) (*App, error) {
	paths, err := config.NewPaths()
	if err != nil {
		return nil, err
	}
	return &App{Paths: paths, Out: deps.Out, Err: deps.Err}, nil
}

// runRoot builds the root command via the built-in tree builder, wires a
// signal-cancelled context, and executes it. It composes the tree through
// core.NewRootCmd so any injected ExtraCommands are appended after the built-in
// set. The real work lives here (rather than in Execute*) so that deferred
// cleanup (the signal-context cancel) always runs before the process exits.
func runRoot(build func(*App) *cobra.Command) int {
	deps := defaultContainer()

	// Adapt the internal (*App)-based tree builder to core.TreeBuilder. App
	// construction (path resolution) can fail; surface it as a build-time panic
	// captured below rather than threading an error through the cobra tree.
	var buildErr error
	tree := func(d *core.AppContainer) *cobra.Command {
		app, err := appFromContainer(d)
		if err != nil {
			buildErr = err
			return &cobra.Command{RunE: func(*cobra.Command, []string) error { return err }}
		}
		return build(app)
	}

	root := core.NewRootCmd(deps, tree)
	if buildErr != nil {
		fmt.Fprintln(os.Stderr, "error:", buildErr)
		return 1
	}

	// Cancel the command context on the first SIGINT/SIGTERM so long-running
	// commands (e.g. register) can unwind gracefully.
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	err := root.ExecuteContext(ctx)
	if err == nil {
		return 0
	}
	// A wrapped child's non-zero exit is mirrored verbatim, not reported as a
	// CLI error.
	var ec *exitCodeError
	if errors.As(err, &ec) {
		return ec.code
	}
	fmt.Fprintln(os.Stderr, "error:", err)
	return 1
}
