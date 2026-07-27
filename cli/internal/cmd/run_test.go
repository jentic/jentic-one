package cmd

import (
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
)

func TestRunRejectsUnknownAgent(t *testing.T) {
	app := testApp(t)
	cmd := newRunCmd(app)
	err := app.runE(cmd, &runOptions{}, []string{"definitely-not-an-agent"})
	if err == nil || !strings.Contains(err.Error(), "unknown agent") {
		t.Fatalf("expected unknown-agent error, got %v", err)
	}
}

func TestRunRequiresAgentArg(t *testing.T) {
	app := testApp(t)
	cmd := newRunCmd(app)
	err := app.runE(cmd, &runOptions{}, nil)
	if err == nil || !strings.Contains(err.Error(), "missing agent") {
		t.Fatalf("expected missing-agent error, got %v", err)
	}
}

func TestRunListGrantsEmpty(t *testing.T) {
	app := testApp(t)
	cmd := newRunCmd(app)
	if err := app.runE(cmd, &runOptions{listGrants: true, agentUser: "x-local-agent"}, []string{"claude"}); err != nil {
		t.Fatalf("list-grants: %v", err)
	}
	out := app.Out.(interface{ String() string }).String()
	if !strings.Contains(out, "no directories granted") {
		t.Errorf("expected empty-grant notice, got %q", out)
	}
}

func TestRunListGrantsShowsRecorded(t *testing.T) {
	app := testApp(t)
	cfg, _ := config.Load(app.Paths)
	cfg.SetLocalAgent("claude", config.LocalAgent{User: "x-local-agent"})
	cfg.AddGrantedDir("claude", "/Users/Shared/x-local-agent/work")
	if err := cfg.Save(app.Paths); err != nil {
		t.Fatalf("save: %v", err)
	}

	cmd := newRunCmd(app)
	if err := app.runE(cmd, &runOptions{listGrants: true}, []string{"claude"}); err != nil {
		t.Fatalf("list-grants: %v", err)
	}
	out := app.Out.(interface{ String() string }).String()
	if !strings.Contains(out, "/Users/Shared/x-local-agent/work") {
		t.Errorf("expected recorded grant in output, got %q", out)
	}
}

func TestRunRegisteredOnAPITree(t *testing.T) {
	root := newAPIRootCmd(testApp(t))
	if !hasCommand(root, "run") {
		t.Error("jentic root missing 'run' command")
	}
	// And it must not leak onto the lifecycle CLI.
	ctl := newCtlRootCmd(testApp(t))
	if hasCommand(ctl, "run") {
		t.Error("jenticctl root unexpectedly registers 'run'")
	}
}
