package cmd

import (
	"io"

	"github.com/jentic/jentic-one/cli/internal/config"
)

// App is the dependency container threaded into every command constructor. It
// holds the resolved filesystem paths and the output streams, so commands carry
// no package-global state and are constructible (and testable) in isolation.
//
// App is the internal wiring derived from the exported core.AppContainer
// (see root.go's appFromContainer): the container carries the injectable seams
// a downstream package can override, while App carries the resolved paths every
// subcommand needs.
type App struct {
	// Paths resolves every filesystem location the CLI owns.
	Paths config.Paths
	// Out and Err are the standard output streams (overridable in tests).
	Out io.Writer
	Err io.Writer
}
