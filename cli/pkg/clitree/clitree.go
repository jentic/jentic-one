// Package clitree exposes the built-in CLI command-tree builders as
// core.TreeBuilders so a downstream module (a separate overlay binary) can
// compose them via core.NewRootCmd without editing the built-in tree.
//
// The actual builders live in internal/cmd (they need the internal *App /
// path resolution). internal/ is not importable across modules, so this
// exported package is the bridge: it imports internal/cmd (allowed — same
// module) and re-exports the two builders. The dependency edge stays one-way
// (clitree → internal/cmd → pkg/core); nothing internal imports clitree.
package clitree

import (
	"github.com/jentic/jentic-one/cli/internal/cmd"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// API returns the built-in `jentic` (API-spec) command-tree builder.
// Compose it with your own container:
//
//	deps := &core.AppContainer{ExtraCommands: myFactories}
//	root := core.NewRootCmd(deps, clitree.API())
//	os.Exit(core.Run(root))
func API() core.TreeBuilder { return cmd.APITreeBuilder() }

// Ctl returns the built-in `jenticctl` (installer / lifecycle) command-tree
// builder.
func Ctl() core.TreeBuilder { return cmd.CtlTreeBuilder() }
