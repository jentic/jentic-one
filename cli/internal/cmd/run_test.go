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

func TestClassifyGrantStderr(t *testing.T) {
	cases := []struct {
		name        string
		in          string
		wantMissing int
		wantBenign  bool
	}{
		{"empty", "", 0, false},
		{"whitespace only", "\n  \n", 0, false},
		{"single missing", "chmod: Failed to set ACL on file 'a.js': No such file or directory", 1, true},
		{"many missing with blanks", "chmod: Failed to set ACL on file 'a.js': No such file or directory\n\nchmod: Failed to set ACL on file 'b.js': No such file or directory\n", 2, true},
		{"crlf tolerated", "chmod: Failed to set ACL on file 'a.js': No such file or directory\r", 1, true},
		{"real error aborts", "chmod: Failed to set ACL on file 'a.js': No such file or directory\nchmod: Operation not permitted", 1, false},
		{"permission denied", "chmod: /x: Permission denied", 0, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			missing, benign := classifyGrantStderr(c.in)
			if missing != c.wantMissing || benign != c.wantBenign {
				t.Errorf("classifyGrantStderr(%q) = (%d, %v), want (%d, %v)",
					c.in, missing, benign, c.wantMissing, c.wantBenign)
			}
		})
	}
}
