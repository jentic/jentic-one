package cmdcore

// gate.go — the migrate gate (activation release). The V2 CLI no longer reads
// the legacy ~/.jentic profile store anywhere: onboarding, the data-plane
// session, and the launch hand-off are all context-only. Instead of silently
// ignoring a machine full of V1 state (identities that "vanish"), the root
// interceptor STOPS every gated command on an unmigrated machine and names the
// one action that unblocks it: `jentic migrate`. The gate clears the moment
// migrate drops its MIGRATED marker (which it does even for an empty store),
// so it fires at most once per machine.

import (
	"os"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/config"
)

// GateAnnotation marks a ROOT command whose tree is subject to the migrate
// gate. Only the `jentic` (api) tree sets it: `jenticctl` is the operator
// lifecycle tool — its install/start/doctor surface owns ~/.jentic as the
// INSTALL root and must keep working on a machine that has never had (and will
// never have) an agent identity to migrate.
const GateAnnotation = "migrate-gate"

// migratedMarkerName mirrors the marker `jentic migrate` drops in the legacy
// root. Kept as a separate const (not exported from the api package) because
// cmdcore must not import the command packages built on top of it.
const migratedMarkerName = "MIGRATED"

// gateExemptNames are the command names allowed through on an unmigrated
// machine. Everything an operator needs to GET OUT of the gated state — and
// nothing that would act on the half-usable identity surface:
//
//   - migrate: the action the gate demands.
//   - reset:   the abandon-ship hatch — wipe local state instead of migrating.
//   - help / completion / __complete / __completeNoDesc: shell plumbing and
//     documentation must never be blocked (help is also how the gate's own
//     remediation is discovered).
var gateExemptNames = map[string]bool{
	"migrate":          true,
	"reset":            true,
	"help":             true,
	"completion":       true,
	"__complete":       true,
	"__completeNoDesc": true,
}

// migrateGateError returns the coded error the gate reports for cmd, or nil
// when the command may run: the tree is not gated, the command is exempt, or
// the machine has no unmigrated V1 store.
func migrateGateError(app *App, cmd *cobra.Command) *ux.CodedError {
	if cmd.Root().Annotations[GateAnnotation] != "true" {
		return nil
	}
	// Exemption is decided on the invoked leaf's top-level ancestor, so
	// `jentic migrate --purge-legacy` and `jentic help catalog` both pass while
	// `jentic catalog list` is still caught.
	top := cmd
	for top.Parent() != nil && top.Parent().Parent() != nil {
		top = top.Parent()
	}
	if gateExemptNames[top.Name()] || cmd == cmd.Root() {
		return nil
	}
	if !legacyNeedsMigration(app.Paths) {
		return nil
	}
	return &ux.CodedError{
		Code: ux.CodeMigrationRequired,
		Msg: "this machine still has a V1 profile store (" + app.Paths.ProfilesDir() + ") " +
			"that this CLI no longer reads",
		Actionable: "jentic migrate   (copies every profile into the V2 context model; " +
			"add --purge-legacy afterwards to remove the old tree)",
	}
}

// legacyNeedsMigration reports whether an unmigrated V1 store exists: at least
// one profile directory under the legacy root and no MIGRATED marker. A bare
// ~/.jentic holding only install state (jenticctl's domain) does not trip the
// gate, and neither does a migrated tree kept around for downgrade.
func legacyNeedsMigration(paths config.Paths) bool {
	if _, err := os.Stat(filepath.Join(paths.Dir(), migratedMarkerName)); err == nil {
		return false
	}
	entries, err := os.ReadDir(paths.ProfilesDir())
	if err != nil {
		return false // no profiles dir → nothing to migrate
	}
	for _, e := range entries {
		if e.IsDir() {
			return true
		}
	}
	return false
}
