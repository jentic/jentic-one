package cmdcore

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/config"
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

// TestMigrateGateError_DoesNotRecommendPurgeLegacy pins #1384: an unmigrated
// V1 machine must direct the caller to `jentic migrate` without recommending
// `--purge-legacy` before the migrated identity has authenticated.
func TestMigrateGateError_DoesNotRecommendPurgeLegacy(t *testing.T) {
	legacyRoot := t.TempDir()
	if err := os.MkdirAll(filepath.Join(legacyRoot, "profiles", "default"), 0o700); err != nil {
		t.Fatal(err)
	}
	app := &App{Paths: config.Paths{Root: legacyRoot}}
	root := &cobra.Command{
		Use:         "jentic",
		Annotations: map[string]string{GateAnnotation: "true"},
	}
	leaf := &cobra.Command{Use: "apis"}
	root.AddCommand(leaf)

	gerr := migrateGateError(app, leaf)
	if gerr == nil {
		t.Fatal("expected migrateGateError to trigger on unmigrated profile directory")
	}
	if gerr.Code != ux.CodeMigrationRequired {
		t.Errorf("code = %q, want %q", gerr.Code, ux.CodeMigrationRequired)
	}
	if gerr.Actionable != "jentic migrate" {
		t.Errorf("actionable = %q, want %q", gerr.Actionable, "jentic migrate")
	}
	if strings.Contains(gerr.Msg, "purge-legacy") || strings.Contains(gerr.Actionable, "purge-legacy") {
		t.Errorf("gate error must not advertise --purge-legacy: msg=%q actionable=%q", gerr.Msg, gerr.Actionable)
	}
}
