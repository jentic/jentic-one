// Package core exposes the CLI's injectable dependency container and root
// builder so a downstream package can compose its own binaries without editing
// the built-in command tree. Everything here is importable (unlike internal/).
//
// Migration ordering is deliberately NOT modelled here: the Python runner
// (`python -m jentic_one.migrations.run`) owns the target set and its
// upgrade/rollback order via its DB_TARGETS registry. The CLI only invokes that
// runner (with a direction flag); it never maintains its own target list.
package core

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"

	"github.com/spf13/cobra"
)

// CommandFactory builds a cobra command from the injected container. Extra
// command groups are supplied as factories so they are constructed against the
// same container (paths, streams) the built-in tree uses.
type CommandFactory func(deps *AppContainer) *cobra.Command

// TreeBuilder builds the fully-configured root command for a binary from the
// injected container. internal/cmd supplies this so `core` never imports
// `internal/*` — which keeps the dependency edge one-directional
// (internal/cmd → pkg/core) and avoids an import cycle.
type TreeBuilder func(deps *AppContainer) *cobra.Command

// AppContainer is the injected dependency set for the CLI command tree. The
// default binaries build a plain container; a downstream package builds its own
// and adds commands via ExtraCommands.
//
// It deliberately carries NO migration-target list (see the package doc).
type AppContainer struct {
	// Out and Err are the standard output streams (overridable in tests).
	Out io.Writer
	Err io.Writer

	// ExtraCommands are extra command groups appended after the built-in tree.
	// nil for the default binaries.
	ExtraCommands []CommandFactory
}

// NewRootCmd builds a root command tree using the injected container. `build`
// assembles the built-in command set (supplied by internal/cmd); any
// ExtraCommands are appended last so they never shadow built-in commands.
func NewRootCmd(deps *AppContainer, build TreeBuilder) *cobra.Command {
	root := build(deps)
	for _, f := range deps.ExtraCommands {
		root.AddCommand(f(deps))
	}
	return root
}

// ExitCoder is an error that carries a process exit code. A wrapped child
// command's non-zero exit is surfaced as one of these so Run can mirror it
// verbatim (rather than reporting it as a generic CLI error). internal/cmd's
// exit-code error implements this; downstream errors may too.
type ExitCoder interface {
	error
	ExitCode() int
}

// Run executes a root command with a signal-cancelled context and returns the
// process exit code. It is the shared entry point for the built-in binaries and
// any downstream binary composed via NewRootCmd, so exit-code / signal semantics
// stay identical. Callers typically do: os.Exit(core.Run(root)).
func Run(root *cobra.Command) int {
	// Cancel the command context on the first SIGINT/SIGTERM so long-running
	// commands can unwind gracefully.
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	err := root.ExecuteContext(ctx)
	if err == nil {
		return 0
	}
	// A wrapped child's non-zero exit is mirrored verbatim.
	var ec ExitCoder
	if errors.As(err, &ec) {
		return ec.ExitCode()
	}
	fmt.Fprintln(os.Stderr, "error:", err)
	return 1
}
