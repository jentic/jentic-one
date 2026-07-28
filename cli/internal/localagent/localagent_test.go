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
	// The exact args are platform-specific; assert each layer's command is
	// sudo-fronted and names the agent user + its target path so it can't
	// silently no-op.
	dir := filepath.Clean("/Users/Shared/x/work")
	home := filepath.Clean("/Users/alice")
	for _, c := range []struct {
		name   string
		args   []string
		target string
	}{
		{"traverse", TraverseGrantCmd("a-local-agent", home).Args, home},
		{"leaf-grant", LeafGrantCmd("a-local-agent", dir).Args, dir},
		{"leaf-revoke", LeafRevokeCmd("a-local-agent", dir).Args, dir},
	} {
		if c.args[0] != "sudo" {
			t.Errorf("%s: expected sudo-fronted command, got %v", c.name, c.args)
		}
		joined := strings.Join(c.args, " ")
		if !strings.Contains(joined, "a-local-agent") || !strings.Contains(joined, c.target) {
			t.Errorf("%s: args missing user or target: %v", c.name, c.args)
		}
	}
}

func TestAncestorChain(t *testing.T) {
	home := "/Users/alice"
	got := AncestorChain(home, "/Users/alice/projects/api")
	want := []string{"/Users/alice", "/Users/alice/projects"}
	if len(got) != len(want) {
		t.Fatalf("AncestorChain = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("AncestorChain = %v, want %v", got, want)
		}
	}
	// A path outside the home has no chain.
	if c := AncestorChain(home, "/Users/Shared/x/work"); c != nil {
		t.Fatalf("expected nil chain for out-of-home path, got %v", c)
	}
	// The home itself (a grant at the home root) has just the home's parent? No —
	// leaf==home means the leaf is the home; chain walks from Dir(home).
	if c := AncestorChain(home, home); len(c) == 0 {
		t.Fatalf("expected non-empty chain for home leaf, got %v", c)
	}
}

func TestIsUnderHome(t *testing.T) {
	home := "/Users/alice"
	if !IsUnderHome(home, "/Users/alice/projects/api") {
		t.Error("expected in-home path to be under home")
	}
	if !IsUnderHome(home, home) {
		t.Error("expected home itself to be under home")
	}
	if IsUnderHome(home, "/Users/Shared/x") {
		t.Error("did not expect shared path to be under home")
	}
	if IsUnderHome(home, "/Users/alice-other") {
		t.Error("did not expect sibling-prefix path to be under home")
	}
}
