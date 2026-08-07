// Package cmdcore holds the shared base of the Jentic CLI command trees: the
// App dependency container plus every helper and method used by BOTH the
// `jentic` (api) and `jenticctl` (ctl) trees. It deliberately never imports the
// installer/lifecycle packages (internal/install, internal/proc, internal/update)
// so the `jentic` binary — which links only cmdcore + internal/cli/api — stays
// free of them.
package cmdcore

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// Build-time version metadata. These are overridden via -ldflags
// (-X github.com/jentic/jentic-one/cli/internal/cli/cmdcore.version=...) by the
// installer and release builds; a plain `go build` keeps the defaults.
var (
	version = "dev"
	commit  = "none"
	date    = "unknown"
)

// Version returns the build-time version string (the -ldflags-stamped value, or
// "dev"). The tree packages read it through this accessor since the underlying
// var is unexported and lives only here.
func Version() string { return version }

// VersionMeta returns the full build-time version metadata (version, commit,
// date). The tree packages alias these into local unexported vars so relocated
// code keeps referencing `version`/`commit`/`date` verbatim.
func VersionMeta() (v, c, d string) { return version, commit, date }

// NewBaseRoot builds a root command with the shared wiring (banner, help
// renderer, version template, command-group ordering) for the given binary
// name. The two binary-specific builders (api.newAPIRootCmd, ctlcmd.newCtlRootCmd)
// add their own command sets and branding on top.
func NewBaseRoot(app *App, binary string) *cobra.Command {
	root := &cobra.Command{
		Use:           binary,
		Version:       version,
		SilenceUsage:  true,
		SilenceErrors: true,
	}

	// Audience-aware interceptor (impl/3.2 §2): resolves mode/theme, constructs the
	// Audience, enforces fencing, and injects both into the context. It also carries
	// the shipped banner + update-nudge side effects (previously PersistentPreRun).
	installInterceptor(app, root)

	root.SetHelpFunc(app.helpFunc)
	root.SetVersionTemplate(
		fmt.Sprintf("%s %s (commit %s, built %s)\n", binary, version, commit, date),
	)

	// Preserve the AddCommand order below in help output (cobra sorts
	// alphabetically by default) so the list follows the onboarding flow.
	cobra.EnableCommandSorting = false

	// Parent PersistentPreRunE hooks (audience injection, fencing) must run even
	// when a subcommand defines its own hook; without this Cobra runs only the
	// nearest one and silently disables fencing for that subtree (impl/3.2 §2).
	cobra.EnableTraverseRunHooks = true

	return root
}

// AddGrouped attaches cmd to root under the given group ID.
func AddGrouped(root *cobra.Command, groupID string, cmd *cobra.Command) {
	cmd.GroupID = groupID
	root.AddCommand(cmd)
}

// DefaultContainer builds the default injection container (no extra commands).
// A downstream package builds its own core.AppContainer{ExtraCommands: ...} and
// calls core.NewRootCmd directly from its own main.go.
func DefaultContainer() *core.AppContainer {
	return &core.AppContainer{In: os.Stdin, Out: os.Stdout, Err: os.Stderr}
}

// NewApp derives the internal App (resolved paths + streams) from the injected
// container. Paths are resolved here — the exported core package stays free of
// the internal config package, keeping the dependency edge
// internal/cli/cmdcore → pkg/core one-directional.
func NewApp(deps *core.AppContainer) (*App, error) {
	paths, err := config.NewPaths()
	if err != nil {
		return nil, err
	}
	return &App{Paths: paths, Out: deps.Out, Err: deps.Err}, nil
}

// TreeBuilder adapts an internal (*App)-based command-tree builder to a
// core.TreeBuilder (which operates on the exported *core.AppContainer). Path
// resolution can fail; surface it as a command that FAILS CLOSED rather than
// threading an error out-of-band. This is the single definition shared by the
// built-in binaries (RunRoot) and the exported downstream builders (pkg/clitree).
//
// The failure root uses PersistentPreRunE so the error also fires for any
// commands appended via AppContainer.ExtraCommands (which are attached to this
// root by core.NewRootCmd) — otherwise a downstream's extra command would run
// against an unresolved container and could silently succeed. SilenceUsage/
// SilenceErrors mirror the real roots so the error prints once, without usage.
func TreeBuilder(build func(*App) *cobra.Command) core.TreeBuilder {
	return func(d *core.AppContainer) *cobra.Command {
		app, err := NewApp(d)
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

// RunRoot builds the root command via the given tree builder and runs it through
// core.Run (shared signal-context + exit-code semantics). The tree packages call
// this from their Execute functions.
func RunRoot(build func(*App) *cobra.Command) int {
	deps := DefaultContainer()
	root := core.NewRootCmd(deps, TreeBuilder(build))
	return core.Run(root)
}
