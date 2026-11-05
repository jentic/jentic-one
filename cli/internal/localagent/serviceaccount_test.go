package localagent

import (
	"runtime"
	"strings"
	"testing"
)

// --- MCP service-account profile (local-MCP §3.7.5 rung 2) ------------------
// Step-assembly tests only: the recipe is enumerated and asserted, never run —
// no live sudo in CI (mirrors TestCreateAccountCmds).

func TestServiceUserName(t *testing.T) {
	got := ServiceUserName("cursor")
	if got != "_jentic-cursor" {
		t.Fatalf("ServiceUserName = %q, want _jentic-cursor", got)
	}
	// The derived name must satisfy the account-name choke point for every
	// supported runtime, so the privileged steps can always be built.
	for _, rt := range []string{"cursor", "claude-desktop", "claude-code", "codex"} {
		if err := ValidateAgentUser(ServiceUserName(rt)); err != nil {
			t.Errorf("ServiceUserName(%q) fails validation: %v", rt, err)
		}
	}
}

func TestServiceHomeDirIsManagedAndValid(t *testing.T) {
	home := ServiceHomeDir("_jentic-cursor")
	if err := ValidateHomeDir(home); err != nil {
		t.Fatalf("ServiceHomeDir %q must pass ValidateHomeDir: %v", home, err)
	}
	if !strings.HasPrefix(home, AgentHomeRoot()+"/") {
		t.Fatalf("ServiceHomeDir %q must live under %s", home, AgentHomeRoot())
	}
}

// TestMcpServiceTeardownCmds pins the isolation teardown recipe and its
// ordering: the NOPASSWD sudoers line first, then the home holding the
// exported key material, then the account itself — all sudo-fronted.
func TestMcpServiceTeardownCmds(t *testing.T) {
	user := "_jentic-cursor"
	home := ServiceHomeDir(user)
	steps := McpServiceTeardownCmds(user, home, true)
	if len(steps) != 3 {
		t.Fatalf("want sudoers+home+account steps, got %d", len(steps))
	}
	for _, s := range steps {
		if s.Cmd.Args[0] != "sudo" {
			t.Errorf("step %q must be sudo-fronted: %v", s.What, s.Cmd.Args)
		}
	}
	first := strings.Join(steps[0].Cmd.Args, " ")
	if !strings.Contains(first, "/etc/sudoers.d/jentic-agent") || !strings.Contains(first, "'(_jentic-cursor)'") {
		t.Errorf("first step must remove the runas-anchored sudoers line: %s", first)
	}
	second := strings.Join(steps[1].Cmd.Args, " ")
	if !strings.Contains(second, "rm -rf "+home) {
		t.Errorf("second step must delete the service home: %s", second)
	}
	third := strings.Join(steps[2].Cmd.Args, " ")
	if runtime.GOOS == "darwin" {
		if !strings.Contains(third, "dscl . -delete /Users/"+user) {
			t.Errorf("third step must delete the account: %s", third)
		}
	} else if !strings.Contains(third, "userdel") {
		t.Errorf("third step must delete the account: %s", third)
	}

	// Home unknown / account gone: the sudoers line is still removed.
	minimal := McpServiceTeardownCmds(user, "", false)
	if len(minimal) != 1 || !strings.Contains(strings.Join(minimal[0].Cmd.Args, " "), "sudoers") {
		t.Errorf("minimal teardown must still drop the sudoers line: %+v", minimal)
	}
}
