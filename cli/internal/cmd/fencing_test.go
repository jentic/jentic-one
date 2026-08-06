package cmd

import (
	"bytes"
	"errors"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// TestFencing_BlocksFencedCommandInAgentMode proves the interceptor wiring
// end-to-end (impl/3.2 §2a): with JENTIC_MODE=agent, a fenced command (reset) is
// blocked with a FENCED_COMMAND CodedError before its RunE ever executes. This is
// the behavioral complement to the arch guard Test1C (which only checks the
// annotation is present).
func TestFencing_BlocksFencedCommandInAgentMode(t *testing.T) {
	t.Setenv("JENTIC_MODE", "agent")

	app := testApp(t)
	root := newAPIRootCmd(app)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	// No --help: help short-circuits before PersistentPreRunE, so we exercise the
	// real interceptor path. The fence returns before reset's RunE does any work.
	root.SetArgs([]string{"reset", "some-profile"})

	err := root.Execute()
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("expected a FENCED_COMMAND CodedError, got %v", err)
	}
	if coded.Code != ux.CodeFenced {
		t.Errorf("error code = %q, want %q", coded.Code, ux.CodeFenced)
	}
	if coded.ExitCode() != ux.ExitError {
		t.Errorf("fenced exit code = %d, want %d", coded.ExitCode(), ux.ExitError)
	}
}

// TestFencing_AllowsFencedCommandInHumanMode confirms the fence does NOT block a
// human: the same command in human mode is not short-circuited by the interceptor
// (it proceeds to its own logic — here --help exits cleanly).
func TestFencing_AllowsFencedCommandInHumanMode(t *testing.T) {
	t.Setenv("JENTIC_MODE", "human")

	app := testApp(t)
	root := newAPIRootCmd(app)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"reset", "--help"})

	if err := root.Execute(); err != nil {
		t.Fatalf("human mode should not be fenced, got %v", err)
	}
}
