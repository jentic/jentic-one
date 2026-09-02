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

// TestCreateServiceAccountCmds asserts the NEW service-account profile: every
// step sudo-fronted, no login shell, no operator grant into the home, and the
// state dir pinned to exactly 0700 under the service uid. This is deliberately
// NOT the login-capable CreateAccountCmds recipe.
func TestCreateServiceAccountCmds(t *testing.T) {
	user := "_jentic-cursor"
	home := ServiceHomeDir(user)
	steps := CreateServiceAccountCmds(user, home)
	if len(steps) < 3 {
		t.Fatalf("expected the create + state-dir steps, got %d", len(steps))
	}

	var all strings.Builder
	for _, s := range steps {
		if s.Cmd.Args[0] != "sudo" {
			t.Errorf("step %q: expected sudo-fronted command, got %v", s.What, s.Cmd.Args)
		}
		all.WriteString(strings.Join(s.Cmd.Args, " "))
		all.WriteString("\n")
	}
	joined := all.String()

	first := strings.Join(steps[0].Cmd.Args, " ")
	if runtime.GOOS == "darwin" {
		if !strings.Contains(first, "sysadminctl") || !strings.Contains(first, "-roleAccount") {
			t.Errorf("macOS create step must use sysadminctl -roleAccount (system uid, `_` prefix): %s", first)
		}
		if !strings.Contains(first, "-shell /usr/bin/false") {
			t.Errorf("macOS service account must get the no-login shell: %s", first)
		}
	} else {
		if !strings.Contains(first, "useradd --system") {
			t.Errorf("Linux create step must use useradd --system: %s", first)
		}
		if !strings.Contains(first, "--shell /usr/sbin/nologin") {
			t.Errorf("Linux service account must get the nologin shell: %s", first)
		}
	}

	if !strings.Contains(joined, "chmod 700 "+home) {
		t.Errorf("state dir must be pinned 0700:\n%s", joined)
	}
	if !strings.Contains(joined, "chown -R "+user+": "+home) {
		t.Errorf("state dir must be owned by the service uid:\n%s", joined)
	}
	// The whole point of the profile: the DESKTOP USER gets nothing. The
	// login-capable recipe grants the operator an inherited ACL into the home
	// (chmod +a / setfacl); this one must never.
	for _, forbidden := range []string{"+a", "setfacl", "file_inherit", "createhomedir"} {
		if strings.Contains(joined, forbidden) {
			t.Errorf("service-account recipe must not grant the operator into the home (found %q):\n%s", forbidden, joined)
		}
	}
}

func TestMcpSudoersRuleIsArgvPinned(t *testing.T) {
	rule := McpSudoersRule("alice", "_jentic-cursor", "/opt/homebrew/bin/jentic", "cursor")
	want := "alice ALL=(_jentic-cursor) NOPASSWD: /opt/homebrew/bin/jentic mcp --context cursor"
	if rule != want {
		t.Fatalf("rule:\n got %q\nwant %q", rule, want)
	}
	// One source user → one target user → exactly the pinned argv: the rule
	// must never contain ALL as the command or a bare shell.
	if strings.Contains(rule, "NOPASSWD: ALL") || strings.Contains(rule, "/bin/bash") {
		t.Fatalf("rule must pin the exact mcp argv, got %q", rule)
	}
}

// TestInstallSudoersRuleCmdSharesValidatedPlumbing: the MCP rule rides the
// same visudo-validated, idempotent drop-in edit as the launch rule, and
// RemoveSudoersCmd (anchored on the runas spec) removes exactly it.
func TestInstallSudoersRuleCmdSharesValidatedPlumbing(t *testing.T) {
	rule := McpSudoersRule("alice", "_jentic-cursor", "/usr/local/bin/jentic", "cursor")
	joined := strings.Join(InstallSudoersRuleCmd(rule).Args, " ")
	for _, needle := range []string{"visudo -cf", "install -m 0440", "grep -qxF", "/etc/sudoers.d/jentic-agent"} {
		if !strings.Contains(joined, needle) {
			t.Errorf("install cmd missing %q: %s", needle, joined)
		}
	}
	if !strings.Contains(joined, "'"+rule+"'") {
		t.Errorf("install cmd must quote the exact rule line: %s", joined)
	}
	// The teardown anchor: RemoveSudoersCmd greps for "(<user>)", which the
	// rule's runas spec carries — so the standard removal reverses this rule.
	if !strings.Contains(rule, "(_jentic-cursor)") {
		t.Error("rule must carry the parenthesised runas spec RemoveSudoersCmd anchors on")
	}
	remove := strings.Join(RemoveSudoersCmd("_jentic-cursor").Args, " ")
	if !strings.Contains(remove, "'(_jentic-cursor)'") {
		t.Errorf("RemoveSudoersCmd must anchor on the service user's runas spec: %s", remove)
	}
}

func TestValidateMcpSudoersInputs(t *testing.T) {
	if err := ValidateMcpSudoersInputs("/opt/homebrew/bin/jentic", "cursor"); err != nil {
		t.Fatalf("valid inputs rejected: %v", err)
	}
	bad := []struct{ bin, ctx string }{
		{"jentic", "cursor"},                  // relative path
		{"/path with space/jentic", "cursor"}, // space breaks the argv pin
		{"/bin/jentic", ""},                   // empty context
		{"/bin/jentic", "Has Caps"},           // outside the config-name charset
		{"/bin/jentic", "a,b"},                // sudoers separator
		{"/bin/jentic\n/etc", "cursor"},       // control char
		{"/bin/jentic", "-leading-hyphen"},    // must start alnum
	}
	for _, c := range bad {
		if err := ValidateMcpSudoersInputs(c.bin, c.ctx); err == nil {
			t.Errorf("ValidateMcpSudoersInputs(%q, %q) should fail", c.bin, c.ctx)
		}
	}
}

// TestExportInstallCmds pins the ROOT-SIDE export recipe: the operator never
// writes into the 0700 service home itself — every directory lands via
// `sudo install -d -o <svc> -m 0700` and every file via
// `sudo install -o <svc> -m 0600 <staging>/<rel> <home>/<rel>`, dirs strictly
// before files.
func TestExportInstallCmds(t *testing.T) {
	user := "_jentic-cursor"
	home := ServiceHomeDir(user)
	staging := "/tmp/jentic-mcp-export-123"
	steps := ExportInstallCmds(user, home, staging,
		[]string{".config/jentic", ".config/jentic/keys"},
		[]string{".config/jentic/config.yaml", ".config/jentic/keys/id.key"})
	if len(steps) != 4 {
		t.Fatalf("want 2 dir + 2 file steps, got %d", len(steps))
	}
	lines := make([]string, 0, len(steps))
	for _, s := range steps {
		if s.Cmd.Args[0] != "sudo" || s.Cmd.Args[1] != "install" {
			t.Errorf("step %q: expected sudo install, got %v", s.What, s.Cmd.Args)
		}
		lines = append(lines, strings.Join(s.Cmd.Args, " "))
	}
	if want := "sudo install -d -o " + user + " -m 0700 " + home + "/.config/jentic"; lines[0] != want {
		t.Errorf("dir step:\n got %q\nwant %q", lines[0], want)
	}
	if want := "sudo install -o " + user + " -m 0600 " + staging + "/.config/jentic/config.yaml " + home + "/.config/jentic/config.yaml"; lines[2] != want {
		t.Errorf("file step:\n got %q\nwant %q", lines[2], want)
	}
	// Files land 0600 (no exec, no group/world), dirs 0700 — never wider.
	joined := strings.Join(lines, "\n")
	for _, forbidden := range []string{"0644", "0755", "chmod", "chown"} {
		if strings.Contains(joined, forbidden) {
			t.Errorf("export install must not use %q:\n%s", forbidden, joined)
		}
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
