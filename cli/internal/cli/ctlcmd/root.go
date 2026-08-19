package ctlcmd

import (
	"os"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/update"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// wire injects the ctl-only seams onto the shared *cmdcore.App before the base
// root installs the interceptor. The update nudge lives in cmdcore but must not
// import internal/update (that would taint the `jentic` binary); the ctl tree,
// which legitimately depends on it, supplies the probe + comparator here.
func wire(core *cmdcore.App) *app {
	core.NudgeLatestTag = update.LatestReleaseTag
	core.NewerVersionAvailable = update.NewerAvailable
	core.NudgeCommand = "jenticctl update"
	return &app{App: core}
}

// newCtlRootCmd assembles the jenticctl command tree: the installer / lifecycle
// surface (install, doctor, start, stop, logs, status, update, uninstall).
// Every subcommand is built via its constructor (no package-global flag state),
// so the tree can be built repeatedly and exercised in tests.
func newCtlRootCmd(core *cmdcore.App) *cobra.Command {
	app := wire(core)
	root := cmdcore.NewBaseRoot(app.App, "jenticctl")
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

	cmdcore.AddGrouped(root, "setup", newInstallCmd(app))
	cmdcore.AddGrouped(root, "setup", newWizardCmd(app))
	cmdcore.AddGrouped(root, "setup", newSetupCmd(app))
	cmdcore.AddGrouped(root, "setup", newResetPasswordCmd(app))
	cmdcore.AddGrouped(root, "setup", newDoctorCmd(app))
	cmdcore.AddGrouped(root, "setup", newStatusCmd(app))
	cmdcore.AddGrouped(root, "setup", newStartCmd(app))
	cmdcore.AddGrouped(root, "setup", newStopCmd(app))
	cmdcore.AddGrouped(root, "setup", newLogsCmd(app))
	cmdcore.AddGrouped(root, "setup", newUpdateCmd(app))
	cmdcore.AddGrouped(root, "setup", newUninstallCmd(app))

	return root
}

// ExecuteCtl runs the jenticctl (installer / lifecycle) command tree and exits
// with an appropriate status code.
func ExecuteCtl() {
	os.Exit(cmdcore.RunRoot(newCtlRootCmd))
}

// TreeBuilder exposes the built-in `jenticctl` (installer/lifecycle) command
// tree as a core.TreeBuilder so a downstream module can compose it via
// core.NewRootCmd(deps, ctlcmd.TreeBuilder()). cli/pkg/clitree re-exports it so
// other modules can import it (internal/ is not importable cross-module).
func TreeBuilder() core.TreeBuilder { return cmdcore.TreeBuilder(newCtlRootCmd) }

// NewDocsRoot builds the assembled `jenticctl` root with a throwaway App for
// documentation generation. No filesystem or network access happens at
// construction time — commands only act when run — so a zero App is safe.
func NewDocsRoot() *cobra.Command { return newCtlRootCmd(&cmdcore.App{}) }
