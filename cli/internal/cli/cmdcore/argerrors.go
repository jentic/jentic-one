package cmdcore

import (
	"errors"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// InvocationErrorMapper is the pkg/core error mapper (AGT-20): it converts a
// cobra-native invocation error — unknown command/flag, bad positional-arg
// count, or a missing required flag — into a typed, already-reported
// *ux.CodedError so an agent gets a closed error_code + stderr envelope instead
// of the bare "error: …" text cobra hands back. RunRoot threads it into
// core.RunTree; the golden runner uses it too so the recorded contract matches
// the shipped path exactly.
//
// Why here and not in decorateCodedErrors: those errors are produced by cobra's
// own arg/flag parsing and returned straight from Execute, so they never pass
// through a command's RunE (where decorateCodedErrors wraps). The mapper is the
// one hook that sees them. It renders through an Audience itself and marks the
// error reported, so pkg/core (which must not import ux) only ever sees an
// ExitCoder.
//
// An error that is ALREADY an *ux.CodedError (e.g. our own SetFlagErrorFunc
// output, or a coded error returned by an Args validator such as exactNamedArgs)
// is passed through untouched — but if it has not yet been rendered
// (IsReported() is false), it is reported here first so its envelope reaches the
// user/agent exactly once. Args-validator errors take this path because they are
// returned from Execute without ever passing through a RunE wrapper.
func InvocationErrorMapper(root *cobra.Command, err error) error {
	if err == nil {
		return nil
	}
	var coded *ux.CodedError
	if errors.As(err, &coded) {
		// Already typed. Our SetFlagErrorFunc / register paths render at their
		// source and MarkReported; but a coded error returned straight from an
		// Args validator (e.g. exactNamedArgs) or bubbled out of Execute has not
		// been rendered yet — it never passed through a RunE wrapper. Render it
		// once here so the envelope reaches the user/agent, then pass it through.
		if !coded.IsReported() {
			reportInvocationError(root, coded)
		}
		return err
	}
	ce := &ux.CodedError{
		Code:       ux.CodeMissingArgument,
		Msg:        err.Error(),
		Actionable: "Check the command, its arguments, and flags — run with --help to see the accepted form.",
	}
	reportInvocationError(root, ce)
	return ce
}

// flagErrorFunc is installed on the root via SetFlagErrorFunc so a flag-parsing
// failure (unknown flag, missing required flag, bad flag value) surfaces as the
// same coded MISSING_ARGUMENT envelope as the other invocation errors (AGT-20),
// rather than cobra's raw "unknown flag: --x". cobra also prints usage for flag
// errors unless silenced; the roots set SilenceUsage, so only our rendered
// envelope reaches the user/agent.
func flagErrorFunc(cmd *cobra.Command, err error) error {
	ce := &ux.CodedError{
		Code:       ux.CodeMissingArgument,
		Msg:        err.Error(),
		Actionable: "Fix the flag and retry — run with --help to see the accepted flags.",
	}
	reportInvocationError(cmd, ce)
	return ce
}

// reportInvocationError renders a coded invocation error through an Audience and
// marks it reported. Cobra parse errors fire BEFORE the root PersistentPreRunE
// interceptor injects the Audience into the context, so we resolve the mode
// ladder directly ($JENTIC_MODE / --mode is the reliable signal here; there is
// no config-derived state at parse time) and construct the matching Audience.
// The Audience renders to os.Stdout/os.Stderr just as the interceptor-built one
// does, so the envelope lands on the same stream an agent already captures.
func reportInvocationError(cmd *cobra.Command, ce *ux.CodedError) {
	mode := clictx.ResolveMode(flagValue(cmd, "mode"), "")
	var aud ux.Audience
	switch mode {
	case clictx.ModeHuman:
		aud = ux.NewHumanUX(ux.Palette{}, false)
	default:
		// Agent, service-account, and any unknown/typo'd mode fail closed to the
		// machine envelope — matching the fencing interceptor's fail-closed rule.
		aud = ux.NewAgentUX(false)
	}
	aud.ReportError(ce, ce.Actionable)
}
