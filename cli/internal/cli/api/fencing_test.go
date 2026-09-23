package api

import (
	"bytes"
	"errors"
	"strings"
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
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	// No --help: help short-circuits before PersistentPreRunE, so we exercise the
	// real interceptor path. The fence returns before reset's RunE does any work.
	root.SetArgs([]string{"reset"})

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
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"reset", "--help"})

	if err := root.Execute(); err != nil {
		t.Fatalf("human mode should not be fenced, got %v", err)
	}
}

// TestFencing_ContextListIsFencedInAgentMode guards F8-2: `context list`
// enumerates the operator's OTHER identities/contexts on a shared machine, so an
// agent must be blocked from running it (impl/3.2 §2a). `context view` (active
// context only) stays a read-only carve-out.
func TestFencing_ContextListIsFencedInAgentMode(t *testing.T) {
	t.Setenv("JENTIC_MODE", "agent")

	app := testApp(t)
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"context", "list"})

	err := root.Execute()
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("expected a FENCED_COMMAND CodedError for `context list`, got %v", err)
	}
	if coded.Code != ux.CodeFenced {
		t.Errorf("error code = %q, want %q", coded.Code, ux.CodeFenced)
	}
}

// TestFencing_ServiceAccountModeRunsAsAgent pins theme-8 D4: the retired
// service-account mode is a deprecated alias of agent. It is fenced exactly
// like agent, and the deprecation notice goes to stderr as a JSON slog line
// (13 §1: stdout stays reserved for the single JSON document).
func TestFencing_ServiceAccountModeRunsAsAgent(t *testing.T) {
	for _, tc := range []struct {
		name string
		env  string
		args []string
	}{
		{"env", "service-account", []string{"reset"}},
		{"flag", "", []string{"--mode", "service-account", "reset"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("JENTIC_MODE", tc.env)

			app := testApp(t)
			root := newAPIRootCmd(app.App)
			out := new(bytes.Buffer)
			root.SetOut(out)
			root.SetErr(new(bytes.Buffer))
			root.SetArgs(tc.args)

			err := root.Execute()
			var coded *ux.CodedError
			if !errors.As(err, &coded) || coded.Code != ux.CodeFenced {
				t.Fatalf("service-account mode must be fenced like agent, got %v", err)
			}
			stderr := app.Err.(*bytes.Buffer).String()
			for _, want := range []string{`"msg":"deprecated mode; running in agent mode"`, `"mode":"service-account"`, `"replacement":"agent"`} {
				if !strings.Contains(stderr, want) {
					t.Errorf("stderr missing %s:\n%s", want, stderr)
				}
			}
			if strings.Contains(out.String(), "deprecated") {
				t.Errorf("deprecation notice leaked onto stdout: %q", out.String())
			}
		})
	}
}

// TestFencing_AgentModeHasNoDeprecationNotice: canonical agent mode stays quiet.
func TestFencing_AgentModeHasNoDeprecationNotice(t *testing.T) {
	t.Setenv("JENTIC_MODE", "agent")

	app := testApp(t)
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"reset"})
	_ = root.Execute()

	if s := app.Err.(*bytes.Buffer).String(); strings.Contains(s, "deprecated mode") {
		t.Errorf("agent mode must not warn:\n%s", s)
	}
}
