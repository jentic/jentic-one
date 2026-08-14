// Package api assembles and runs the `jentic` command tree — the API-spec
// surface (register/identity, context/env, catalog, apis, search, inspect,
// execute) for discovering, inspecting, and executing against the Jentic API
// catalog. Data-plane commands speak to the control plane through the generated
// SDK (client/generated/control); `jentic migrate` is the sole reader of the
// legacy V1 store (see legacy_store.go).
package api

import (
	"os"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/localagentcmd"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// newAPIRootCmd assembles the jentic command tree: the API-spec surface
// (register, identity, context, catalog, apis) for discovering, inspecting,
// and executing against the Jentic API catalog.
func newAPIRootCmd(core *cmdcore.App) *cobra.Command {
	app := &app{App: core}
	root := cmdcore.NewBaseRoot(app.App, "jentic")
	root.Short = "jentic: discover, inspect, and run against the Jentic API catalog"
	root.Annotations = map[string]string{
		"tagline": "discover, inspect, and run against the Jentic API catalog",
		// The jentic tree is subject to the migrate gate: on a machine with an
		// unmigrated V1 profile store, every non-exempt command stops and
		// demands `jentic migrate` (cmdcore/gate.go). jenticctl is NOT gated —
		// it owns ~/.jentic as the install root, identity-free.
		cmdcore.GateAnnotation: "true",
	}
	root.Long = "jentic is the command-line companion for working with the Jentic API\n" +
		"catalog. Register and switch agent identities, browse and import APIs from\n" +
		"the public catalog into your local registry, inspect operations, and execute\n" +
		"against them.\n\n" +
		"New here? If you're a person setting up a local agent, run `jentic bootstrap`\n" +
		"to create one (isolated account + registration + skills). If you're an agent\n" +
		"without an identity yet, run `jentic register`. Then browse the public catalog\n" +
		"with `jentic catalog`. To install and operate jentic-one locally, use the\n" +
		"`jenticctl` CLI (e.g. `jenticctl install`). Use `jentic <command> --help` for details."

	root.AddGroup(
		&cobra.Group{ID: "identity", Title: "Identity & access"},
		&cobra.Group{ID: "apis", Title: "APIs"},
		&cobra.Group{ID: "agent", Title: "Find and run operations"},
		&cobra.Group{ID: "client", Title: "Local agent client"},
		&cobra.Group{ID: "admin", Title: "Administration"},
	)

	// Global selection/UX flags (BC-3/BC-5/BC-9). The interceptor
	// (installInterceptor) reads these via flagValue to drive the mode/theme/
	// context resolution ladder; they are persistent so they apply to every
	// subcommand. --context selects which context to resolve; --mode overrides the
	// interaction mode (closed enum, fail-closed to agent); --theme overrides the
	// human-mode palette.
	root.PersistentFlags().String("context", "", "Context to act on (overrides the active context; $JENTIC_CONTEXT)")
	root.PersistentFlags().String("mode", "", "Interaction mode: human|agent|service-account ($JENTIC_MODE)")
	root.PersistentFlags().String("theme", "", "Color theme: dark|light|no-color ($JENTIC_THEME)")

	cmdcore.AddGrouped(root, "identity", fenced(bootstrapSafe(localagentcmd.NewBootstrapCmd(app.App)))) // fenced (AGT-5): registers + waits on a HUMAN approval and writes skill files — agents run `register`
	cmdcore.AddGrouped(root, "identity", bootstrapSafe(cmdcore.NewRegisterCmd(app.App)))
	cmdcore.AddGrouped(root, "identity", newLogoutCmd(app))
	// V2 context model: the Environment × Identity × Context surface plus the
	// one-shot migrator. This IS the activation release — the V1 profile
	// command is gone. The hidden deprecation shim that briefly aliased
	// `jentic profile <verb>` onto these successors was removed in ARCH-21
	// Part B (BC — see 14_breaking_changes); `jentic profile` is now an
	// unknown command.
	cmdcore.AddGrouped(root, "identity", newContextCmd(app))
	cmdcore.AddGrouped(root, "identity", newEnvCmd(app))
	cmdcore.AddGrouped(root, "identity", newIdentityCmd(app))
	cmdcore.AddGrouped(root, "identity", bootstrapSafe(newMigrateCmd(app))) // NOT fenced (BC-1); bootstrap-safe (runs with no XDG config)
	cmdcore.AddGrouped(root, "apis", newCatalogCmd(app))
	cmdcore.AddGrouped(root, "apis", newApisCmd(app))
	cmdcore.AddGrouped(root, "apis", newEndpointsCmd(app))
	cmdcore.AddGrouped(root, "apis", newCredentialsCmd(app))
	cmdcore.AddGrouped(root, "agent", newSearchCmd(app))
	cmdcore.AddGrouped(root, "agent", newInspectCmd(app))
	cmdcore.AddGrouped(root, "agent", newExecuteCmd(app))
	cmdcore.AddGrouped(root, "agent", newAccessCmd(app))
	// Execution history + live events over the V2 SDK (Phase 5 items 3-4).
	cmdcore.AddGrouped(root, "agent", newHistoryCmd(app))
	cmdcore.AddGrouped(root, "agent", newEventsCmd(app))
	// gh-api-style authenticated passthrough to the control plane, with
	// self-description (Phase 5 item 7a). Not fenced (server-scope-gated).
	cmdcore.AddGrouped(root, "agent", newAPICmd(app))
	// The agent-client commands manage and drive the local coding agent
	// (generate its skills, launch it under isolation, tear its account down),
	// distinct from the catalog find/run operations above.
	cmdcore.AddGrouped(root, "client", localagentcmd.NewSkillCmd(app.App))
	cmdcore.AddGrouped(root, "client", fenced(localagentcmd.NewRunCmd(app.App)))
	cmdcore.AddGrouped(root, "client", fenced(newResetCmd(app)))
	// Agent-side read-only self-check (F8-4, impl/5.1 §3c). Not fenced: it never
	// mutates host state and is exactly the diagnostic an agent needs where
	// jenticctl is absent.
	cmdcore.AddGrouped(root, "client", newDoctorCmd(app))
	cmdcore.AddGrouped(root, "admin", newAdminCmd(app))
	cmdcore.AddGrouped(root, "admin", newThemeCmd(app))

	return root
}

// ExecuteAPI runs the jentic (API-spec) command tree and exits with an
// appropriate status code.
func ExecuteAPI() {
	os.Exit(cmdcore.RunRoot(newAPIRootCmd))
}

// TreeBuilder exposes the built-in `jentic` (API) command tree as a
// core.TreeBuilder so a downstream module can compose it via
// core.NewRootCmd(deps, api.TreeBuilder()). cli/pkg/clitree re-exports it so
// other modules can import it (internal/ is not importable cross-module).
func TreeBuilder() core.TreeBuilder { return cmdcore.TreeBuilder(newAPIRootCmd) }

// NewDocsRoot builds the assembled `jentic` root with a throwaway App for
// documentation generation. No filesystem or network access happens at
// construction time — commands only act when run — so a zero App is safe.
func NewDocsRoot() *cobra.Command { return newAPIRootCmd(&cmdcore.App{}) }
