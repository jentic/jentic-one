// Package clitree exposes the built-in CLI command-tree builders as
// core.TreeBuilders so a downstream module (a separate overlay binary) can
// compose them via core.NewRootCmd without editing the built-in tree.
//
// The actual builders live in internal/cli/api and internal/cli/ctlcmd (they
// need the internal *cmdcore.App / path resolution). internal/ is not
// importable across modules, so this exported package is the bridge: it imports
// those packages (allowed — same module) and re-exports the two builders. The
// dependency edge stays one-way (clitree → internal/cli/{api,ctlcmd} →
// pkg/core); nothing internal imports clitree.
package clitree

import (
	"github.com/jentic/jentic-one/cli/internal/cli/api"
	"github.com/jentic/jentic-one/cli/internal/cli/ctlcmd"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// API returns the built-in `jentic` (API-spec) command-tree builder.
// Compose it with your own container:
//
//	deps := &core.AppContainer{ExtraCommands: myFactories}
//	root := core.NewRootCmd(deps, clitree.API())
//	os.Exit(core.Run(root))
func API() core.TreeBuilder { return api.TreeBuilder() }

// Ctl returns the built-in `jenticctl` (installer / lifecycle) command-tree
// builder.
func Ctl() core.TreeBuilder { return ctlcmd.TreeBuilder() }

// MustBeFenced is THE canonical fence set: the command paths that MUST carry the
// `fenced` annotation so an autonomous agent cannot run them (impl/3.2 §2a). Every
// other doc (plan Phase 3 item 5, 07 §2, impl/1.3 §3, rules/01 §4, rules/03 §4)
// defers to this list; if a doc and this list disagree, this list wins. The rule:
// a command is fenced iff it (a) mutates host-level management state (contexts,
// environments, identities, local-agent lifecycle), or (b) reveals/switches to
// contexts other than the active one.
//
// Phase 2 ships the enforcing machinery against the commands that EXIST today —
// context/env/identity management surface. Deliberate carve-outs, NOT fenced:
// the read-only verbs (`context view`, `env list`, `identity list`),
// `identity register` (DCR of the agent's own identity — required by the agent
// workflow), `migrate` (BC-1 directs agents to run it), and `theme` (a local
// color preference, not a management/context switch). NOTE: `context list` IS
// fenced (impl/3.2 §2a): unlike `context view` (active context only) it
// enumerates the operator's OTHER identities/contexts on a shared machine, a
// disclosure an agent should not perform.
//
// Paths are space-separated ("context use" -> ["context","use"]) for root.Find.
var MustBeFenced = []string{
	"run",
	"reset",
	"context create",
	"context use",
	"context delete",
	"context list",
	"env add",
	"env delete",
	"identity add",
	"identity delete",
}
