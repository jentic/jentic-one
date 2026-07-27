package localagent

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestLookupAndKnown(t *testing.T) {
	if _, ok := Lookup("claude"); !ok {
		t.Fatal("expected claude to be a known agent")
	}
	if _, ok := Lookup("nope"); ok {
		t.Fatal("did not expect an unknown agent to resolve")
	}
	known := Known()
	if len(known) == 0 || known[0] != "claude" {
		t.Fatalf("Known() = %v, want claude present", known)
	}
}

func TestDefaultUserName(t *testing.T) {
	if got := DefaultUserName("alice"); got != "alice-local-agent" {
		t.Fatalf("DefaultUserName = %q", got)
	}
}

func TestDangerReason(t *testing.T) {
	home := "/Users/alice"
	cases := []struct {
		name       string
		dir        string
		wantDanger bool
	}{
		{"operator home root", "/Users/alice", true},
		{"operator ssh dir", "/Users/alice/.ssh", true},
		{"operator jentic dir", "/Users/alice/.jentic", true},
		{"another user home", "/Users/bob", true},
		{"linux other home", "/home/bob", true},
		{"system etc", "/etc", true},
		{"system usr subdir", "/usr/local/bin", true},
		{"root", "/", true},
		{"neutral shared path", "/Users/Shared/alice-local-agent/work", false},
		{"project under home is not the home root", "/Users/alice/projects/api", false},
		{"opt path", "/opt/alice-local-agent", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := DangerReason(tc.dir, home)
			if (got != "") != tc.wantDanger {
				t.Fatalf("DangerReason(%q) = %q, wantDanger=%v", tc.dir, got, tc.wantDanger)
			}
		})
	}
}

func TestGrantAndRevokeCmdShape(t *testing.T) {
	// The exact args are platform-specific; assert the command is sudo-fronted
	// and names the agent user + a resolved path so it can't silently no-op.
	dir := filepath.Clean("/Users/Shared/x/work")
	for _, c := range []struct {
		name string
		args []string
	}{
		{"grant", GrantDirCmd("a-local-agent", dir).Args},
		{"revoke", RevokeDirCmd("a-local-agent", dir).Args},
	} {
		if c.args[0] != "sudo" {
			t.Errorf("%s: expected sudo-fronted command, got %v", c.name, c.args)
		}
		joined := strings.Join(c.args, " ")
		if !strings.Contains(joined, "a-local-agent") || !strings.Contains(joined, dir) {
			t.Errorf("%s: args missing user or dir: %v", c.name, c.args)
		}
	}
}
