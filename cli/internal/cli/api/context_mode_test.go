package api

import (
	"errors"
	"testing"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
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

// TestContextCreate_ModeAgentNotFencedInHumanSession pins #1412: the local
// `context create --mode` is the mode to SAVE, not the session mode, so a human
// session can create an agent context without the fence blocking it.
func TestContextCreate_ModeAgentNotFencedInHumanSession(t *testing.T) {
	withXDG(t)
	seedEnvAndIdentity(t)

	if err := runJentic(t, "context", "create", "bot", "--env", "prod", "--identity", "ci", "--mode", "agent"); err != nil {
		t.Fatalf("context create --mode agent in a human session: %v", err)
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if got := cfg.Contexts["bot"].Mode; got != "agent" {
		t.Errorf("saved mode = %q, want agent", got)
	}
}

// TestContextCreate_LocalModeDoesNotUnfenceAgentSession pins the other half of
// #1412: in an agent session, `context create --mode human` must stay fenced —
// the saved-context value must never become the session mode.
func TestContextCreate_LocalModeDoesNotUnfenceAgentSession(t *testing.T) {
	withXDG(t)
	seedEnvAndIdentity(t)
	t.Setenv("JENTIC_MODE", "agent")

	err := runJentic(t, "context", "create", "bot", "--env", "prod", "--identity", "ci", "--mode", "human")
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeFenced {
		t.Fatalf("expected FENCED_COMMAND in an agent session, got %v", err)
	}
	cfg, _ := sdkconfig.Load()
	if _, ok := cfg.Contexts["bot"]; ok {
		t.Error("a fenced context create must not write the context")
	}
}

// TestFencing_PersistentModeAgentStillFences confirms the root --mode flag keeps
// driving the session mode: `--mode agent` fences a management command even in
// an otherwise-human session.
func TestFencing_PersistentModeAgentStillFences(t *testing.T) {
	withXDG(t)
	seedContext(t)

	err := runJentic(t, "--mode", "agent", "context", "use", "main")
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeFenced {
		t.Fatalf("expected FENCED_COMMAND for root --mode agent, got %v", err)
	}
}

// TestContextCreate_RejectsInvalidMode pins the closed human|agent enum for the
// saved context mode: a typo (or the retired service-account alias) is refused
// up front rather than persisted.
func TestContextCreate_RejectsInvalidMode(t *testing.T) {
	for _, mode := range []string{"agnet", "service-account", "Agent"} {
		t.Run(mode, func(t *testing.T) {
			withXDG(t)
			seedEnvAndIdentity(t)

			err := runJentic(t, "context", "create", "bot", "--env", "prod", "--identity", "ci", "--mode", mode)
			var coded *ux.CodedError
			if !errors.As(err, &coded) || coded.Code != ux.CodeMissingArgument {
				t.Fatalf("expected MISSING_ARGUMENT for --mode %q, got %v", mode, err)
			}
			cfg, _ := sdkconfig.Load()
			if _, ok := cfg.Contexts["bot"]; ok {
				t.Errorf("an invalid --mode %q must not write the context", mode)
			}
		})
	}
}
