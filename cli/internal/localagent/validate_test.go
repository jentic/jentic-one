package localagent

import (
	"runtime"
	"strings"
	"testing"
)

func TestValidateAgentUserAcceptsWellFormedNames(t *testing.T) {
	for _, name := range []string{
		"alice-local-agent",
		"a",
		"_agent",
		"claude_agent",
		"user123-local-agent",
		strings.Repeat("a", maxAgentUserLen),
	} {
		if err := ValidateAgentUser(name); err != nil {
			t.Errorf("ValidateAgentUser(%q) = %v, want nil", name, err)
		}
	}
}

func TestValidateAgentUserRejectsInjectionAndMalformed(t *testing.T) {
	// Each of these must be refused: they either carry a shell/sudoers/ACL
	// metacharacter, a space or newline, an illegal leading character, or exceed
	// the length bound — the vectors behind the command/sudoers/ACE injections.
	for _, name := range []string{
		"",
		"1agent",                               // leading digit
		"-agent",                               // leading hyphen
		"Alice",                                // uppercase
		"alice agent",                          // space
		"alice;rm -rf /",                       // shell metachar
		"alice$(whoami)",                       // command substitution
		"alice\nroot ALL=(ALL) NOPASSWD: ALL",  // sudoers newline injection
		"alice allow execute",                  // ACE-grammar tokens (space)
		"a/b",                                  // slash
		"a.b",                                  // dot
		"root\t",                               // tab
		strings.Repeat("a", maxAgentUserLen+1), // too long
	} {
		if err := ValidateAgentUser(name); err == nil {
			t.Errorf("ValidateAgentUser(%q) = nil, want error", name)
		}
	}
}

func TestValidateHomeDirAcceptsUnderSharedRoot(t *testing.T) {
	root := AgentHomeRoot()
	for _, home := range []string{
		root + "/alice-local-agent",
		root + "/team/alice-local-agent",
	} {
		if err := ValidateHomeDir(home); err != nil {
			t.Errorf("ValidateHomeDir(%q) = %v, want nil", home, err)
		}
	}
	// DefaultHomeDir must always satisfy the validator on this platform.
	if err := ValidateHomeDir(DefaultHomeDir("alice-local-agent")); err != nil {
		t.Errorf("DefaultHomeDir output failed ValidateHomeDir: %v", err)
	}
}

func TestValidateHomeDirRejectsUnsafe(t *testing.T) {
	root := AgentHomeRoot()
	cases := []string{
		"",
		"relative/path",
		root,                      // the root itself, not a subdir
		root + "/",                // trailing slash (unclean)
		root + "/../alice",        // escapes via ..
		root + "/alice/../../etc", // escapes the root
		root + "//alice",          // doubled separator (unclean)
		"/etc",                    // system tree
		"/tmp/agent",              // outside the shared root
		root + "/alice\nroot",     // control char / newline
	}
	// A human home root must always be rejected regardless of platform.
	if runtime.GOOS == "darwin" {
		cases = append(cases, "/Users/alice", "/Users")
	} else {
		cases = append(cases, "/home/alice", "/home")
	}
	for _, home := range cases {
		if err := ValidateHomeDir(home); err == nil {
			t.Errorf("ValidateHomeDir(%q) = nil, want error", home)
		}
	}
}

func TestValidateGrantPath(t *testing.T) {
	if err := ValidateGrantPath("/Users/alice/projects/api"); err != nil {
		t.Errorf("ValidateGrantPath(valid) = %v, want nil", err)
	}
	for _, dir := range []string{"", "relative", "/tmp/a\nb"} {
		if err := ValidateGrantPath(dir); err == nil {
			t.Errorf("ValidateGrantPath(%q) = nil, want error", dir)
		}
	}
}

func TestValidateConfigDirRequiresHomesOwnJentic(t *testing.T) {
	home := AgentHomeRoot() + "/alice-local-agent"
	// The one accepted shape: exactly the home's own .jentic.
	if err := ValidateConfigDir(home, AgentConfigDir(home)); err != nil {
		t.Errorf("ValidateConfigDir(home, home/.jentic) = %v, want nil", err)
	}
	// Everything else — empty, a system tree, a sibling, the home itself, or a
	// config dir under a DIFFERENT (or invalid) home — must be refused, so a
	// hand-edited config_dir can never reach `rm -rf`/`chown -R`.
	bad := []struct{ home, configDir string }{
		{home, ""},
		{home, "/etc"},
		{home, home}, // the home itself, not its .jentic
		{home, home + "/.other"},
		{home, AgentHomeRoot() + "/bob-local-agent/.jentic"}, // a different home's .jentic
		{"", AgentConfigDir(home)},                           // no home to anchor against
		{"/etc", "/etc/.jentic"},                             // invalid home
		{home, AgentConfigDir(home) + "\nx"},                 // control char
	}
	for _, c := range bad {
		if err := ValidateConfigDir(c.home, c.configDir); err == nil {
			t.Errorf("ValidateConfigDir(%q, %q) = nil, want error", c.home, c.configDir)
		}
	}
}
