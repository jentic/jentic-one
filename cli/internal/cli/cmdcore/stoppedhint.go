package cmdcore

import (
	"errors"
	"io"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// WithStoppedInstanceHint appends the stopped-instance recovery line
// (ux.StoppedInstanceHint) to w — the command's stderr — when err is a refused
// dial to a loopback host (agentops.LoopbackConnRefused), and returns err
// unchanged. Any other error passes through untouched.
//
// The hint is a stderr diagnostic only (#849): the error_code, exit code, and
// stdout are exactly what the command produced without it. So that the hint
// follows the error rather than preceding it, a not-yet-reported
// *ux.CodedError is rendered through the context Audience first and marked
// reported, which stops the root backstop (decorateCodedErrors) rendering it a
// second time. A non-coded error keeps core.Run's own reporting, so the hint
// precedes it.
func WithStoppedInstanceHint(cmd *cobra.Command, w io.Writer, err error) error {
	if !agentops.LoopbackConnRefused(err) {
		return err
	}
	var coded *ux.CodedError
	if errors.As(err, &coded) && !coded.IsReported() {
		ux.FromContext(cmd.Context()).ReportError(coded, coded.Actionable)
	}
	ux.RenderStoppedInstanceHint(cmd.Context(), w)
	return err
}
