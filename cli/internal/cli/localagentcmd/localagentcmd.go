// Package localagentcmd holds the local agent client commands extracted from
// cmdcore (ARCH-1): `jentic setup` (register + skill install + MCP entry) and
// `jentic skill` (the agent-runtime skill engine shared with jenticctl's
// wizard/update/uninstall).
//
// localagentcmd depends on cmdcore (the allowed arrow: a command-tree package
// uses the shared base); cmdcore never depends on it.
package localagentcmd

import "github.com/jentic/jentic-one/cli/internal/cli/cmdcore"

// New wraps a shared *cmdcore.App in the local-agent command receiver. External
// trees (e.g. jenticctl's wizard/update) that need to call an exported
// local-agent method — SetupForWizard, SkillUpdateDefault — construct a Cmd
// through this rather than reaching for an internal field.
func New(app *cmdcore.App) *Cmd { return &Cmd{App: app} }

// Cmd is the local-agent command receiver. It embeds *cmdcore.App so every
// method below keeps calling the shared helpers (Out/Err/Paths, WantsInteractive,
// …). The two binary trees construct it from the shared *cmdcore.App
// (NewSetupCmd/NewSkillCmd take *cmdcore.App and wrap it).
//
// This mirrors the ctl tree's `type app struct { *cmdcore.App }`.
type Cmd struct {
	*cmdcore.App
}
