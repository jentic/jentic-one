package api

import (
	"io"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
)

// app is the api (jentic) tree's command receiver. It embeds *cmdcore.App so
// every api leaf-command method (func (a *app) …) keeps calling shared helpers
// (a.ResolveIdentity, a.PrintRevokeHint, …) verbatim — those promote from the
// embedded App — while api-local methods hang off *app itself. The api root
// builder constructs &app{core} from the shared *cmdcore.App.
type app struct {
	*cmdcore.App
}

// Tree-local aliases so relocated api code keeps referencing the shared cmdcore
// helpers by their original (unexported) spellings without per-call-site churn.
type identityOptions = cmdcore.IdentityOptions

// exitCodeError aliases the shared type so api code keeps constructing
// &exitCodeError{Code: n}. Defined in cmdcore, so its field is exported (Code).
type exitCodeError = cmdcore.ExitCodeError

var (
	dotOK        = cmdcore.DotOK
	dotWarn      = cmdcore.DotWarn
	dotDown      = cmdcore.DotDown
	valueOr      = cmdcore.ValueOr
	tokenStatus  = cmdcore.TokenStatus
	jsonOrPretty = cmdcore.JSONOrPretty
)

// Approval-poll cadence, mirrored from cmdcore so api access/refresh poll loops
// use the exact same timing (and shrink together under tests).
var (
	pollInitialDelay = cmdcore.PollInitialDelay
	pollMaxDelay     = cmdcore.PollMaxDelay
	pollDelayStep    = cmdcore.PollDelayStep
)

// writeJSON mirrors cmdcore.WriteJSON so relocated api code keeps calling it
// lower-cased with the same (writer, value) signature.
func writeJSON(w io.Writer, v any) error { return cmdcore.WriteJSON(w, v) }

// Build-time version metadata, mirrored from cmdcore so the few relocated api
// call sites (api.go's server-version 404 enrichment) keep referencing version
// verbatim. cmdcore is the ONE package the -ldflags stamp targets. commit/date
// are mirrored for symmetry with the other trees even if api doesn't read them.
var version, commit, date = cmdcore.VersionMeta()

var (
	_ = commit
	_ = date
)
