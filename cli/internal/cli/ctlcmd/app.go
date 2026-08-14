// Package ctlcmd is the jenticctl command tree — the operator/lifecycle CLI
// (install, wizard, setup, doctor, status, start/stop, logs, update, uninstall).
// Its commands are methods on a tree-local app that embeds *cmdcore.App; unlike
// the api (jentic) tree it legitimately links the operator packages
// (internal/install, internal/proc, internal/update). Named ctlcmd rather than
// ctl to avoid clashing with internal/cli/ctl (the config-driven installer
// machinery). See impl/1.1 §1a.
package ctlcmd

import (
	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
)

// app is the ctl (jenticctl) tree's command receiver. It embeds *cmdcore.App so
// every ctl leaf-command method (func (a *app) …) keeps calling shared helpers
// (a.ResolveIdentity, a.BrandHeader, …) verbatim — those promote from the
// embedded App — while ctl-local methods hang off *app itself. The ctl root
// builder constructs &app{core} from the shared *cmdcore.App.
type app struct {
	*cmdcore.App
}

// Tree-local aliases so relocated ctl code keeps referencing the shared cmdcore
// helpers by their original (unexported) spellings without per-call-site churn.
type identityOptions = cmdcore.IdentityOptions

var (
	dotOK         = cmdcore.DotOK
	dotWarn       = cmdcore.DotWarn
	dotDown       = cmdcore.DotDown
	dotFail       = cmdcore.DotFail
	valueOr       = cmdcore.ValueOr
	tokenStatus   = cmdcore.TokenStatus
	identityLabel = cmdcore.IdentityLabel
)

// exitCodeError aliases the shared type so ctl code (install.go) keeps
// constructing &exitCodeError{Code: n}. The field is exported (Code) since the
// type is defined in cmdcore.
type exitCodeError = cmdcore.ExitCodeError

// Build-time version metadata, mirrored from cmdcore so the many relocated ctl
// call sites keep referencing version/commit/date verbatim. cmdcore is the ONE
// package the -ldflags stamp targets. version/commit are read by status/update;
// date is kept for symmetry (and future use) and sunk so it isn't flagged.
var version, commit, date = cmdcore.VersionMeta()

var _ = date

// wantsInteractive mirrors cmdcore.WantsInteractive so relocated ctl code keeps
// calling it lower-cased.
func wantsInteractive(cmd *cobra.Command, yes bool, fieldFlags ...string) bool {
	return cmdcore.WantsInteractive(cmd, yes, fieldFlags...)
}
