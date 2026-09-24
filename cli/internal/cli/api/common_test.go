package api

import (
	"errors"
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// TestExactNamedArgs pins UX-22: a wrong positional-arg count returns a coded
// MISSING_ARGUMENT that names the expected argument and the usage line, instead
// of cobra's bare "accepts 1 arg(s), received 0". An agent gets the closed
// error_code; a human gets the actionable usage hint.
func TestExactNamedArgs(t *testing.T) {
	cmd := &cobra.Command{Use: "execute"}
	cmd.SetArgs(nil)
	v := exactNamedArgs("<target>", "target")

	if err := v(cmd, []string{"one"}); err != nil {
		t.Fatalf("exact count should pass, got %v", err)
	}

	err := v(cmd, nil)
	var ce *ux.CodedError
	if !errors.As(err, &ce) {
		t.Fatalf("miscount err = %v, want *ux.CodedError", err)
	}
	if ce.Code != ux.CodeMissingArgument {
		t.Errorf("code = %q, want %q", ce.Code, ux.CodeMissingArgument)
	}
	if ce.Actionable == "" {
		t.Error("expected an actionable usage hint")
	}
}

// TestRangeNamedArgs pins UX-22 for the variadic form used by `apis rm`: 1–2
// args are accepted; 0 or 3 args return a coded MISSING_ARGUMENT.
func TestRangeNamedArgs(t *testing.T) {
	cmd := &cobra.Command{Use: "rm"}
	v := rangeNamedArgs(1, 2, "<name> [rev]", "name", "rev")

	for _, ok := range [][]string{{"a"}, {"a", "b"}} {
		if err := v(cmd, ok); err != nil {
			t.Errorf("args %v should pass, got %v", ok, err)
		}
	}
	for _, bad := range [][]string{nil, {"a", "b", "c"}} {
		err := v(cmd, bad)
		var ce *ux.CodedError
		if !errors.As(err, &ce) || ce.Code != ux.CodeMissingArgument {
			t.Errorf("args %v err = %v, want MISSING_ARGUMENT", bad, err)
		}
	}
}
