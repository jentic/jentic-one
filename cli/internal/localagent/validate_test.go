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
