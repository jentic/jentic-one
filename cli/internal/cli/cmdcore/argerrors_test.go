package cmdcore

import (
	"errors"
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// TestInvocationErrorMapper_WrapsRawError pins AGT-20: a cobra-native invocation
// error (unknown command / bad arg count) that reaches core.RunTree as a plain
// error must be converted into a coded MISSING_ARGUMENT so an agent gets a
// closed error_code + exit 1, not the raw "error: …" string cobra hands back.
func TestInvocationErrorMapper_WrapsRawError(t *testing.T) {
	t.Setenv("JENTIC_MODE", "agent") // agent branch renders the machine envelope
	root := &cobra.Command{Use: "jentic"}
	root.PersistentFlags().String("mode", "", "")

	raw := errors.New(`unknown command "nope" for "jentic"`)
	got := InvocationErrorMapper(root, raw)

	var ce *ux.CodedError
	if !errors.As(got, &ce) {
		t.Fatalf("mapper returned %T, want *ux.CodedError", got)
	}
	if ce.Code != ux.CodeMissingArgument {
		t.Errorf("code = %q, want %q", ce.Code, ux.CodeMissingArgument)
	}
	if ce.ExitCode() != ux.ExitError {
		t.Errorf("exit = %d, want %d", ce.ExitCode(), ux.ExitError)
	}
	if !ce.IsReported() {
		t.Error("mapper must render the error through an Audience and mark it reported, " +
			"so core.Run's residual backstop does not print it twice")
	}
	if ce.Actionable == "" {
		t.Error("invocation error must carry an actionable hint")
	}
	if ce.Msg != raw.Error() {
		t.Errorf("mapper dropped the original cobra message: got %q, want %q", ce.Msg, raw.Error())
	}
}

// TestInvocationErrorMapper_PassesThroughCoded pins that an error already typed
// as *ux.CodedError (e.g. a command's own denial, or flagErrorFunc's output) is
// returned untouched — it was rendered at its source, so re-wrapping would
// double-render and could relabel a BROKER_DENIED as MISSING_ARGUMENT.
func TestInvocationErrorMapper_PassesThroughCoded(t *testing.T) {
	root := &cobra.Command{Use: "jentic"}
	orig := &ux.CodedError{Code: ux.CodeBrokerDenied, Msg: "denied"}
	got := InvocationErrorMapper(root, orig)
	var ce *ux.CodedError
	if !errors.As(got, &ce) {
		t.Fatalf("mapper returned %T, want the original *ux.CodedError", got)
	}
	if ce != orig {
		t.Errorf("mapper replaced the coded error; it must pass through the same instance")
	}
	if ce.Code != ux.CodeBrokerDenied {
		t.Errorf("code changed to %q; a coded error must pass through unchanged", ce.Code)
	}
}

// TestInvocationErrorMapper_NilIsNil keeps the mapper transparent on success.
func TestInvocationErrorMapper_NilIsNil(t *testing.T) {
	if got := InvocationErrorMapper(&cobra.Command{Use: "jentic"}, nil); got != nil {
		t.Errorf("mapper(nil) = %v, want nil", got)
	}
}

// TestFlagErrorFunc_CodesMissingArgument pins AGT-20 for the flag-parse path: an
// unknown/bad flag surfaces as the same coded MISSING_ARGUMENT envelope as the
// other invocation errors instead of cobra's raw "unknown flag: --x".
func TestFlagErrorFunc_CodesMissingArgument(t *testing.T) {
	t.Setenv("JENTIC_MODE", "agent")
	cmd := &cobra.Command{Use: "jentic"}
	cmd.PersistentFlags().String("mode", "", "")

	got := flagErrorFunc(cmd, errors.New("unknown flag: --bogus"))
	var ce *ux.CodedError
	if !errors.As(got, &ce) {
		t.Fatalf("flagErrorFunc returned %T, want *ux.CodedError", got)
	}
	if ce.Code != ux.CodeMissingArgument {
		t.Errorf("code = %q, want %q", ce.Code, ux.CodeMissingArgument)
	}
	if !ce.IsReported() {
		t.Error("flagErrorFunc must render + mark reported so core.Run does not double-print")
	}
}
