package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"testing"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// seedEnvAndIdentity creates env "prod" and identity "ci" (no context), in the
// human session withXDG sets up.
func seedEnvAndIdentity(t *testing.T) {
	t.Helper()
	if err := runJentic(t, "env", "add", "prod", "--url", "https://api.example.com"); err != nil {
		t.Fatal(err)
	}
	if err := runJentic(t, "identity", "add", "ci"); err != nil {
		t.Fatal(err)
	}
}

// requireFenced fails unless err is the FENCED_COMMAND CodedError.
func requireFenced(t *testing.T, err error) {
	t.Helper()
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeFenced {
		t.Fatalf("expected FENCED_COMMAND, got %v", err)
	}
}

// requireNoContext fails if context name was written to config.yaml.
func requireNoContext(t *testing.T, name string) {
	t.Helper()
	cfg, _ := sdkconfig.Load()
	if _, ok := cfg.Contexts[name]; ok {
		t.Errorf("context %q must not be written", name)
	}
}

// TestContextCreate_ContextModeAgentNotFencedInHumanSession pins #1412: the
// mode SAVED in a new context is not the session mode, so a human session can
// create an agent context without the fence blocking it.
func TestContextCreate_ContextModeAgentNotFencedInHumanSession(t *testing.T) {
	withXDG(t)
	seedEnvAndIdentity(t)

	if err := runJentic(t, "context", "create", "bot", "--env", "prod", "--identity", "ci", "--context-mode", "agent"); err != nil {
		t.Fatalf("context create --context-mode agent in a human session: %v", err)
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if got := cfg.Contexts["bot"].Mode; got != "agent" {
		t.Errorf("saved mode = %q, want agent", got)
	}
}

// TestContextCreate_SessionModeStillFences pins that every session-mode source
// keeps fencing `context create`, whatever --context-mode says: the root --mode
// flag in either position, $JENTIC_MODE, and a persisted agent context.
func TestContextCreate_SessionModeStillFences(t *testing.T) {
	create := []string{"context", "create", "escape", "--env", "prod", "--identity", "ci", "--context-mode", "human"}
	for _, tc := range []struct {
		name string
		env  string
		args []string
	}{
		{"root --mode before subcommand", "human", append([]string{"--mode", "agent"}, create...)},
		{"root --mode after subcommand", "human", append(append([]string{}, create...), "--mode", "agent")},
		{"JENTIC_MODE=agent", "agent", create},
	} {
		t.Run(tc.name, func(t *testing.T) {
			withXDG(t)
			seedEnvAndIdentity(t)
			t.Setenv("JENTIC_MODE", tc.env)

			requireFenced(t, runJentic(t, tc.args...))
			requireNoContext(t, "escape")
		})
	}

	t.Run("persisted agent context", func(t *testing.T) {
		withXDG(t)
		seedEnvAndIdentity(t)
		if err := runJentic(t, "context", "create", "bot", "--env", "prod", "--identity", "ci", "--context-mode", "agent", "--use"); err != nil {
			t.Fatalf("seed agent context: %v", err)
		}
		t.Setenv("JENTIC_MODE", "")

		requireFenced(t, runJentic(t, create...))
		requireNoContext(t, "escape")
	})
}

// TestContextCreate_RootModeAgentInvocationErrorIsJSON pins AGT-20 on the
// shadow-free tree: `jentic --mode agent context create` (missing <name>)
// reports its invocation error as the machine JSON envelope on stderr.
func TestContextCreate_RootModeAgentInvocationErrorIsJSON(t *testing.T) {
	withXDG(t)
	app := testApp(t)
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"--mode", "agent", "context", "create", "--env", "prod", "--identity", "ci"})

	stderr := captureStderr(t, func() {
		_ = cmdcore.InvocationErrorMapper(root, root.Execute())
	})
	var env struct {
		ErrorCode string `json:"error_code"`
	}
	if err := json.Unmarshal([]byte(stderr), &env); err != nil {
		t.Fatalf("stderr is not one JSON envelope: %v\n%s", err, stderr)
	}
	if env.ErrorCode != string(ux.CodeMissingArgument) {
		t.Errorf("error_code = %q, want %q", env.ErrorCode, ux.CodeMissingArgument)
	}
}

// TestContextCreate_RejectsInvalidContextMode pins the closed human|agent enum
// for the saved context mode: a typo (or the retired service-account alias) is
// refused up front rather than persisted.
func TestContextCreate_RejectsInvalidContextMode(t *testing.T) {
	for _, mode := range []string{"agnet", "service-account", "Agent"} {
		t.Run(mode, func(t *testing.T) {
			withXDG(t)
			seedEnvAndIdentity(t)

			err := runJentic(t, "context", "create", "bot", "--env", "prod", "--identity", "ci", "--context-mode", mode)
			var coded *ux.CodedError
			if !errors.As(err, &coded) || coded.Code != ux.CodeMissingArgument {
				t.Fatalf("expected MISSING_ARGUMENT for --context-mode %q, got %v", mode, err)
			}
			requireNoContext(t, "bot")
		})
	}
}

// TestAPITree_NoLocalFlagShadowsRootPersistent guards #1412 structurally: a
// command-local flag named like a root persistent flag (--context/--mode/
// --theme) absorbs every token of that name on the command, so the session
// value is silently lost there (and with it fencing on --mode).
func TestAPITree_NoLocalFlagShadowsRootPersistent(t *testing.T) {
	root := newAPIRootCmd(testApp(t).App)
	var walk func(c *cobra.Command)
	walk = func(c *cobra.Command) {
		c.LocalNonPersistentFlags().VisitAll(func(f *pflag.Flag) {
			if root.PersistentFlags().Lookup(f.Name) != nil {
				t.Errorf("%q defines local --%s, shadowing the root persistent flag", c.CommandPath(), f.Name)
			}
		})
		for _, sub := range c.Commands() {
			walk(sub)
		}
	}
	for _, sub := range root.Commands() {
		walk(sub)
	}
}

// captureStderr redirects os.Stderr for fn's duration: the Audience writes the
// machine error envelope to the process stream directly, never through app.Err.
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
	out, _ := io.ReadAll(r)
	return string(out)
}
