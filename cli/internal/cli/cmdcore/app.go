package cmdcore

import (
	"context"
	"io"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// App is the dependency container threaded into every command constructor. It
// holds the resolved filesystem paths and the output streams, so commands carry
// no package-global state and are constructible (and testable) in isolation.
//
// App is the internal wiring derived from the exported core.AppContainer
// (see NewApp): the container carries the injectable seams a downstream package
// can override, while App carries the resolved paths every subcommand needs.
type App struct {
	// Paths resolves every filesystem location the CLI owns.
	Paths config.Paths
	// Out and Err are the standard output streams (overridable in tests).
	Out io.Writer
	Err io.Writer
	// DetectEnv overrides the skill operator-detection probe (tests only);
	// nil means the real OS probe. Injected here rather than a package var so
	// the command tree stays constructor-built with no global state.
	DetectEnv func() (skillgen.DetectEnv, error)

	// NudgeLatestTag and NewerVersionAvailable are the update-nudge seams. The
	// ctl tree (which legitimately depends on internal/update) injects them so
	// cmdcore itself never imports the installer/lifecycle packages — keeping the
	// `jentic` (api) binary free of them. When either is nil (the api tree, and
	// tests that don't opt in) the update nudge is a no-op. NudgeLatestTag
	// resolves the latest release tag; NewerVersionAvailable reports whether
	// latest is newer than installed.
	NudgeLatestTag        func(ctx context.Context, repo, token string) (string, error)
	NewerVersionAvailable func(installed, latest string) bool
}
