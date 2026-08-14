package api

import (
	"io"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
)

// app is the api (jentic) tree's command receiver. It embeds *cmdcore.App so
// every api leaf-command method (func (a *app) …) keeps calling shared helpers
// (a.WriteJSON, a.WantsInteractive, …) verbatim — those promote from the
// embedded App — while api-local methods hang off *app itself. The api root
// builder constructs &app{core} from the shared *cmdcore.App.
type app struct {
	*cmdcore.App
}

// Tree-local aliases so relocated api code keeps referencing the shared cmdcore
// helpers by their original (unexported) spellings without per-call-site churn.

var (
	dotOK        = cmdcore.DotOK
	dotWarn      = cmdcore.DotWarn
	dotDown      = cmdcore.DotDown
	valueOr      = cmdcore.ValueOr
	jsonOrPretty = cmdcore.JSONOrPretty
)

// writeJSON mirrors cmdcore.WriteJSON so relocated api code keeps calling it
// lower-cased with the same (writer, value) signature.
func writeJSON(w io.Writer, v any) error { return cmdcore.WriteJSON(w, v) }

// writeList mirrors cmdcore.WriteList: the canonical versioned list envelope
// {schema_version, data, has_more, next_cursor[, meta]} every list command emits.
func writeList(w io.Writer, data any, nextCursor string, meta map[string]any) error {
	return cmdcore.WriteList(w, data, nextCursor, meta)
}

// Build-time version metadata, mirrored from cmdcore for symmetry with the ctl
// tree. cmdcore is the ONE package the -ldflags stamp targets; the api tree
// reads the connected SERVER's version (api.go's 404 enrichment probes it live)
// rather than its own build stamp, so these are unread here — sunk below so the
// mirror stays a single obvious source of truth without tripping the unused check.
var version, commit, date = cmdcore.VersionMeta()

var (
	_ = version
	_ = commit
	_ = date
)
