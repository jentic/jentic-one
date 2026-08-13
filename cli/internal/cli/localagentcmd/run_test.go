package localagentcmd

import (
	"bytes"
	"context"
	"errors"
	"os/exec"
	"strings"
	"syscall"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/config"
)

func TestRunRejectsUnknownAgent(t *testing.T) {
	app := testApp(t)
	cmd := NewRunCmd(app.App)
	err := app.runE(cmd, &runOptions{}, []string{"definitely-not-an-agent"})
	if err == nil || !strings.Contains(err.Error(), "unknown agent") {
		t.Fatalf("expected unknown-agent error, got %v", err)
	}
}

func TestRunRequiresAgentArg(t *testing.T) {
	app := testApp(t)
	cmd := NewRunCmd(app.App)
	err := app.runE(cmd, &runOptions{}, nil)
	if err == nil || !strings.Contains(err.Error(), "missing agent") {
		t.Fatalf("expected missing-agent error, got %v", err)
	}
}

// TestRunGrantMgmtWithoutAccountIsCoded pins ARCH-23: a grant-management op
// (`--list-grants`) on a machine with no provisioned agent account returns a
// CODED RESOLVE_FAILED (exit 2, "provision first") rather than a raw exit-1
// string an agent can't branch on. Before ARCH-23 localagentcmd returned only
// raw fmt.Errorf, leaving the whole feature outside the machine contract.
func TestRunGrantMgmtWithoutAccountIsCoded(t *testing.T) {
	app := testApp(t) // fresh temp home: no agent user configured
	cmd := NewRunCmd(app.App)
	err := app.runE(cmd, &runOptions{listGrants: true}, []string{"claude"})
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("grant-mgmt without account returned %T (%v), want *ux.CodedError", err, err)
	}
	if coded.Code != ux.CodeResolveFailed {
		t.Errorf("code = %q, want RESOLVE_FAILED", coded.Code)
	}
	if !strings.Contains(coded.Actionable, "bootstrap") {
		t.Errorf("actionable should point at bootstrap: %q", coded.Actionable)
	}
}

// The Args validator counts only the positional args BEFORE a `--`, so any number
// of forwarded agent args is accepted while a third jentic positional is not.
func TestRunArgsValidatorCountsBeforeDash(t *testing.T) {
	app := testApp(t)
	cases := []struct {
		name    string
		argv    []string
		wantErr bool
	}{
		{"agent only", []string{"claude"}, false},
		{"agent + path", []string{"claude", "/work"}, false},
		{"three positionals", []string{"claude", "/work", "extra"}, true},
		{"agent + many forwarded", []string{"claude", "--", "--model", "opus", "-p", "hi"}, false},
		{"agent + path + forwarded", []string{"claude", "/work", "--", "-p", "hi"}, false},
		{"three before dash", []string{"claude", "/work", "extra", "--", "-p"}, true},
		{"leading dash agent + args", []string{"--", "claude", "--model", "opus"}, false},
		{"leading dash agent only", []string{"--", "claude"}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cmd := NewRunCmd(app.App)
			if err := cmd.ParseFlags(tc.argv); err != nil {
				t.Fatalf("parse: %v", err)
			}
			err := cmd.Args(cmd, cmd.Flags().Args())
			if tc.wantErr != (err != nil) {
				t.Fatalf("argv %v: wantErr=%v got %v", tc.argv, tc.wantErr, err)
			}
		})
	}
}

// runE splits jentic's positional args from the `--`-forwarded agent args using
// ArgsLenAtDash, which cobra sets during flag parsing. Verify the split boundary
// so a forwarded flag never lands in jentic's positional slice (which would be
// misread as a working-directory path).
func TestRunSplitsAtDash(t *testing.T) {
	cmd := NewRunCmd(testApp(t).App)
	if err := cmd.ParseFlags([]string{"claude", "/work", "--", "--model", "opus"}); err != nil {
		t.Fatalf("parse: %v", err)
	}
	args := cmd.Flags().Args()
	dash := cmd.ArgsLenAtDash()
	if dash != 2 {
		t.Fatalf("ArgsLenAtDash = %d, want 2 (claude, /work)", dash)
	}
	pos, forwarded := args[:dash], args[dash:]
	if strings.Join(pos, ",") != "claude,/work" {
		t.Errorf("positional args = %v, want [claude /work]", pos)
	}
	if strings.Join(forwarded, ",") != "--model,opus" {
		t.Errorf("forwarded args = %v, want [--model opus]", forwarded)
	}
}

// A LEADING `--` (jentic run -- claude --flag) reports ArgsLenAtDash()==0 and
// captures the whole agent command (including its flags) as positional args,
// bypassing jentic's flag parser — so an agent flag can never collide with a
// jentic flag. The agent id is then the first forwarded token.
func TestRunLeadingDashCapturesAgentCommand(t *testing.T) {
	cmd := NewRunCmd(testApp(t).App)
	// --resumeSessionId is not a jentic flag; parsing must NOT error on it.
	if err := cmd.ParseFlags([]string{"--", "claude", "--resumeSessionId=1234", "-p", "hi"}); err != nil {
		t.Fatalf("parse: %v", err)
	}
	if dash := cmd.ArgsLenAtDash(); dash != 0 {
		t.Fatalf("ArgsLenAtDash = %d, want 0 for a leading `--`", dash)
	}
	args := cmd.Flags().Args()
	// The Args validator sees 0 jentic positionals → accepted.
	if err := cmd.Args(cmd, args); err != nil {
		t.Fatalf("Args validator rejected a leading-dash command: %v", err)
	}
	// runE takes args[0] as the agent id and args[1:] as the forwarded argv.
	if args[0] != "claude" {
		t.Errorf("agent id = %q, want claude", args[0])
	}
	if strings.Join(args[1:], ",") != "--resumeSessionId=1234,-p,hi" {
		t.Errorf("forwarded argv = %v, want [--resumeSessionId=1234 -p hi]", args[1:])
	}
}

// A bare leading `--` with nothing after it is a missing-agent error, not a panic.
func TestRunLeadingDashRequiresAgent(t *testing.T) {
	app := testApp(t)
	cmd := NewRunCmd(app.App)
	if err := cmd.ParseFlags([]string{"--"}); err != nil {
		t.Fatalf("parse: %v", err)
	}
	// Simulate cobra's post-parse state: ArgsLenAtDash()==0 with no args. runE must
	// surface the missing-agent error.
	err := app.runE(cmd, &runOptions{}, cmd.Flags().Args())
	if err == nil || !strings.Contains(err.Error(), "missing agent") {
		t.Fatalf("expected missing-agent error, got %v", err)
	}
}

func TestRunListGrantsEmpty(t *testing.T) {
	app := testApp(t)
	cmd := NewRunCmd(app.App)
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
	cfg.SetAgentAccount(config.AgentAccount{User: "x-local-agent", AccountCreated: true, Enabled: true})
	cfg.AddGrantedDir("/Users/Shared/x-local-agent/work")
	if err := cfg.Save(app.Paths); err != nil {
		t.Fatalf("save: %v", err)
	}

	cmd := NewRunCmd(app.App)
	if err := app.runE(cmd, &runOptions{listGrants: true}, []string{"claude"}); err != nil {
		t.Fatalf("list-grants: %v", err)
	}
	out := app.Out.(interface{ String() string }).String()
	if !strings.Contains(out, "/Users/Shared/x-local-agent/work") {
		t.Errorf("expected recorded grant in output, got %q", out)
	}
}

// TestWarnSameUserOnce proves the unconfined-launch notice is shown exactly once:
// the first call emits it and persists SameUserNoticeSeen; a second call (fresh
// config loaded from disk) stays silent.
func TestWarnSameUserOnce(t *testing.T) {
	app := testApp(t)
	out := app.Out.(*bytes.Buffer)

	cfg1, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	app.warnSameUserOnce(cfg1)
	if !strings.Contains(out.String(), "no confinement") {
		t.Fatalf("first launch should warn, got: %q", out.String())
	}
	if !cfg1.SameUserNoticeSeen {
		t.Error("in-memory cfg should record the notice as seen")
	}

	// A subsequent launch reloads config from disk and must stay silent.
	out.Reset()
	cfg2, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	if !cfg2.SameUserNoticeSeen {
		t.Fatal("SameUserNoticeSeen must persist across reload")
	}
	app.warnSameUserOnce(cfg2)
	if out.String() != "" {
		t.Errorf("second launch must not warn again, got: %q", out.String())
	}
}

// TestWireGracefulCancelSendsSIGTERM proves cancellation terminates the child with
// a catchable SIGTERM (which sudo can relay down the launch chain) rather than the
// exec default SIGKILL to the direct child, and sets a WaitDelay SIGKILL backstop.
func TestWireGracefulCancelSendsSIGTERM(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	// A long-lived child so the process is alive when we exercise Cancel.
	c := exec.CommandContext(ctx, "sleep", "60")
	wireGracefulCancel(c)
	if c.WaitDelay != cancelGracePeriod {
		t.Errorf("WaitDelay = %v, want %v", c.WaitDelay, cancelGracePeriod)
	}
	if c.Cancel == nil {
		t.Fatal("Cancel must be set so ctx-cancel sends SIGTERM, not SIGKILL")
	}
	if err := c.Start(); err != nil {
		t.Fatalf("start: %v", err)
	}
	if err := c.Cancel(); err != nil {
		t.Fatalf("Cancel: %v", err)
	}
	err := c.Wait()
	var exit *exec.ExitError
	if !errors.As(err, &exit) {
		t.Fatalf("expected ExitError from a signalled child, got %v", err)
	}
	ws, ok := exit.Sys().(syscall.WaitStatus)
	if !ok || !ws.Signaled() || ws.Signal() != syscall.SIGTERM {
		t.Errorf("child should have been terminated by SIGTERM, got %v", exit)
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
