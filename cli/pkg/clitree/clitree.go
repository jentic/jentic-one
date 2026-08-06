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

// MustBeFenced is THE canonical fence set: the command paths that MUST carry the
// `fenced` annotation so an autonomous agent cannot run them (impl/3.2 §2a). Every
// other doc (plan Phase 3 item 5, 07 §2, impl/1.3 §3, rules/01 §4, rules/03 §4)
// defers to this list; if a doc and this list disagree, this list wins. The rule:
// a command is fenced iff it (a) mutates host-level management state (contexts,
// environments, identities, local-agent lifecycle), or (b) reveals/switches to
// contexts other than the active one.
//
// Phase 2 ships the enforcing machinery against the commands that EXIST today —
// the local-agent lifecycle (`run` mutates ACLs via --grant/--revoke and is
// operator-only; `reset` wipes an account's config tree). Phase 3 adds the
// context/env/identity management surface. Deliberate carve-outs, NOT fenced:
// the read-only verbs (`context view`/`list`, `env list`, `identity list`),
// `identity register` (DCR of the agent's own identity — required by the agent
// workflow), `migrate` (BC-1 directs agents to run it), and `theme` (a local
// color preference, not a management/context switch).
//
// Paths are space-separated ("context use" -> ["context","use"]) for root.Find.
var MustBeFenced = []string{
	"run",
	"reset",
	"context create",
	"context use",
	"context delete",
	"env add",
	"env delete",
	"identity add",
	"identity delete",
}
