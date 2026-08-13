// Package localagentcmd holds the local-agent runtime feature extracted from
// cmdcore (ARCH-1): everything that stands up and drives a coding agent as an
// isolated Unix user — `jentic bootstrap`, `jentic run`, agent-account
// creation/seeding, workspace grants, and exporting the active context's
// credentials into the agent's home.
//
// It is the ONLY package that performs privileged host mutation (useradd, ACL
// grants, sudo-launched processes), so isolating it keeps that blast radius in
// one reviewable place and leaves cmdcore as the shared command-infrastructure
// base both binaries embed. localagentcmd depends on cmdcore (the allowed
// arrow: a command-tree package uses the shared base); cmdcore never depends on
// it.
package localagentcmd

import "github.com/jentic/jentic-one/cli/internal/cli/cmdcore"

// New wraps a shared *cmdcore.App in the local-agent command receiver. External
// trees (e.g. jenticctl's wizard/update) that need to call an exported
// local-agent method — BootstrapForWizard, SkillUpdateDefault — construct a Cmd
// through this rather than reaching for an internal field.
func New(app *cmdcore.App) *Cmd { return &Cmd{App: app} }

// Cmd is the local-agent command receiver. It embeds *cmdcore.App so every
// method below keeps calling the shared helpers (Out/Err/Paths, WantsInteractive,
// …) exactly as it did when these files lived in cmdcore — the move is pure
// relocation, not a behavior change. The two binary trees construct it from the
// shared *cmdcore.App (NewBootstrapCmd/NewRunCmd take *cmdcore.App and wrap it).
//
// This mirrors the ctl tree's `type app struct { *cmdcore.App }`.
type Cmd struct {
	*cmdcore.App
}
