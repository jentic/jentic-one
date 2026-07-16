// Package cmd wires the Jentic CLI command tree.
package cmd

import (
	"fmt"
	"os"

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
	return &core.AppContainer{In: os.Stdin, Out: os.Stdout, Err: os.Stderr}
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

// treeBuilder adapts an internal (*App)-based command-tree builder to a
// core.TreeBuilder (which operates on the exported *core.AppContainer). Path
// resolution can fail; surface it as a command that FAILS CLOSED rather than
// threading an error out-of-band. This is the single definition shared by the
// built-in binaries (runRoot) and the exported downstream builders (pkg/clitree).
//
// The failure root uses PersistentPreRunE so the error also fires for any
// commands appended via AppContainer.ExtraCommands (which are attached to this
// root by core.NewRootCmd) — otherwise a downstream's extra command would run
// against an unresolved container and could silently succeed. SilenceUsage/
// SilenceErrors mirror the real roots so the error prints once, without usage.
func treeBuilder(build func(*App) *cobra.Command) core.TreeBuilder {
	return func(d *core.AppContainer) *cobra.Command {
		app, err := appFromContainer(d)
		if err != nil {
			return &cobra.Command{
				SilenceUsage:      true,
				SilenceErrors:     true,
				RunE:              func(*cobra.Command, []string) error { return err },
				PersistentPreRunE: func(*cobra.Command, []string) error { return err },
			}
		}
		return build(app)
	}
}

// APITreeBuilder exposes the built-in `jentic` (API) command tree as a
// core.TreeBuilder so a downstream module can compose it via
// core.NewRootCmd(deps, APITreeBuilder()). It lives in internal/cmd (which may
// see *App); cli/pkg/clitree re-exports it so other modules can import it
// (internal/ is not importable cross-module).
func APITreeBuilder() core.TreeBuilder { return treeBuilder(newAPIRootCmd) }

// CtlTreeBuilder exposes the built-in `jenticctl` (installer/lifecycle) command
// tree as a core.TreeBuilder. See APITreeBuilder.
func CtlTreeBuilder() core.TreeBuilder { return treeBuilder(newCtlRootCmd) }

// runRoot builds the root command via the built-in tree builder and runs it
// through core.Run (shared signal-context + exit-code semantics).
func runRoot(build func(*App) *cobra.Command) int {
	deps := defaultContainer()
	root := core.NewRootCmd(deps, treeBuilder(build))
	return core.Run(root)
}
