package cmdcore

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// captureStderr redirects the process stderr for fn's duration and returns what
// was written. Needed because the Audience implementations write envelopes to
// os.Stderr directly (the machine contract's stream, not cobra's ErrOrStderr).
func captureStderr(t *testing.T, fn func()) string {
	t.Helper()
	old := os.Stderr
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stderr = w
	defer func() { os.Stderr = old }()
	fn()
	_ = w.Close()
	var buf bytes.Buffer
	_, _ = io.Copy(&buf, r)
	return buf.String()
}

// TestDecorateCodedErrorsReportsUnreported is the AGT-3 backstop regression: a
// *ux.CodedError that a command returns WITHOUT reporting must still surface as
// the machine envelope (agent mode) before core.Run maps it to an exit code —
// otherwise the process would exit silently, since ExitCoder errors are not
// printed by core.Run.
func TestDecorateCodedErrorsReportsUnreported(t *testing.T) {
	coded := &ux.CodedError{
		Code:       ux.CodeNotAuthenticated,
		Msg:        "profile \"default\" has no registered agent; run `jentic register` first",
		Actionable: "jentic register",
	}
	root := &cobra.Command{Use: "jentic", SilenceUsage: true, SilenceErrors: true}
	root.AddCommand(&cobra.Command{
		Use:  "boom",
		RunE: func(*cobra.Command, []string) error { return coded },
	})
	decorateCodedErrors(root)

	ctx := ux.WithAudience(context.Background(), ux.NewAgentUX(false))
	root.SetArgs([]string{"boom"})
	var runErr error
	stderr := captureStderr(t, func() { runErr = root.ExecuteContext(ctx) })

	if runErr == nil {
		t.Fatal("expected the coded error to propagate")
	}
	var env ux.AgentError
	if err := json.Unmarshal([]byte(stderr), &env); err != nil {
		t.Fatalf("stderr is not one JSON envelope: %v\nraw: %q", err, stderr)
	}
	if env.ErrorCode != ux.CodeNotAuthenticated {
		t.Errorf("error_code = %q, want NOT_AUTHENTICATED", env.ErrorCode)
	}
	if env.Actionable != "jentic register" {
		t.Errorf("actionable_step = %q, want the register command", env.Actionable)
	}
}

// TestDecorateCodedErrorsSkipsAlreadyReported: cutover commands that route
// through reportCoded (Audience.ReportError marks the error) must not have the
// envelope rendered a second time by the backstop.
func TestDecorateCodedErrorsSkipsAlreadyReported(t *testing.T) {
	coded := &ux.CodedError{Code: ux.CodeResolveFailed, Msg: "no such operation"}
	root := &cobra.Command{Use: "jentic", SilenceUsage: true, SilenceErrors: true}
	root.AddCommand(&cobra.Command{
		Use: "boom",
		RunE: func(c *cobra.Command, _ []string) error {
			aud := ux.FromContext(c.Context())
			aud.ReportError(coded, coded.Actionable) // command reports, then returns
			return coded
		},
	})
	decorateCodedErrors(root)

	ctx := ux.WithAudience(context.Background(), ux.NewAgentUX(false))
	root.SetArgs([]string{"boom"})
	stderr := captureStderr(t, func() { _ = root.ExecuteContext(ctx) })

	if n := strings.Count(stderr, "RESOLVE_FAILED"); n != 1 {
		t.Errorf("envelope rendered %d times, want exactly 1\nstderr: %q", n, stderr)
	}
}
