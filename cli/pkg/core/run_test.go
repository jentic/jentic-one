package core_test

import (
	"bytes"
	"errors"
	"fmt"
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/pkg/core"
)

// codedError implements core.ExitCoder.
type codedError struct{ code int }

func (e *codedError) Error() string { return fmt.Sprintf("boom %d", e.code) }
func (e *codedError) ExitCode() int { return e.code }

func TestRunMirrorsExitCoder(t *testing.T) {
	for _, code := range []int{2, 3, 42} {
		root := &cobra.Command{
			Use:           "x",
			SilenceUsage:  true,
			SilenceErrors: true,
			RunE:          func(*cobra.Command, []string) error { return &codedError{code: code} },
		}
		if got := core.Run(root); got != code {
			t.Fatalf("Run mirrored exit code = %d, want %d", got, code)
		}
	}
}

func TestRunMirrorsWrappedExitCoder(t *testing.T) {
	root := &cobra.Command{
		Use:           "x",
		SilenceUsage:  true,
		SilenceErrors: true,
		RunE:          func(*cobra.Command, []string) error { return fmt.Errorf("wrap: %w", &codedError{code: 7}) },
	}
	if got := core.Run(root); got != 7 {
		t.Fatalf("Run through wrapped ExitCoder = %d, want 7", got)
	}
}

func TestRunReturns0OnSuccessAnd1OnPlainError(t *testing.T) {
	ok := &cobra.Command{Use: "x", RunE: func(*cobra.Command, []string) error { return nil }}
	if got := core.Run(ok); got != 0 {
		t.Fatalf("Run(success) = %d, want 0", got)
	}
	bad := &cobra.Command{
		Use:           "x",
		SilenceUsage:  true,
		SilenceErrors: true,
		RunE:          func(*cobra.Command, []string) error { return errors.New("plain") },
	}
	if got := core.Run(bad); got != 1 {
		t.Fatalf("Run(plain error) = %d, want 1", got)
	}
}

// NewRootCmd must wire the container's Err stream so Run's error output is
// captured there (not leaked to os.Stderr).
func TestNewRootCmdWiresErrStreamForRun(t *testing.T) {
	var errBuf bytes.Buffer
	deps := &core.AppContainer{Err: &errBuf}
	build := func(*core.AppContainer) *cobra.Command {
		return &cobra.Command{
			Use:           "x",
			SilenceUsage:  true,
			SilenceErrors: true,
			RunE:          func(*cobra.Command, []string) error { return errors.New("kaboom") },
		}
	}
	root := core.NewRootCmd(deps, build)
	if got := core.Run(root); got != 1 {
		t.Fatalf("Run = %d, want 1", got)
	}
	if !bytes.Contains(errBuf.Bytes(), []byte("kaboom")) {
		t.Fatalf("error not written to injected Err stream; got %q", errBuf.String())
	}
}
