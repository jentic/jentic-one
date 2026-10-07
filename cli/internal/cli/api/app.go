package api

import (
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
