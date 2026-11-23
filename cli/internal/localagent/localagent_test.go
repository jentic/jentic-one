package localagent

import (
	"os/user"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestDefaultUserName(t *testing.T) {
	if got := DefaultUserName("alice"); got != "alice-local-agent" {
		t.Fatalf("DefaultUserName = %q", got)
	}
}

// TestAgentIdentityDirs pins the identity-scrub surface `jentic reset` removes
// from a kept home: the exported XDG config/state trees plus the legacy
// ~/.jentic, every one a strict descendant of the agent's home so the list is
// safe to hand to a privileged rm.
func TestAgentIdentityDirs(t *testing.T) {
	home := "/Users/Shared/alice-local-agent"
	got := AgentIdentityDirs(home)
	want := []string{
		filepath.Join(home, ".config", "jentic"),
		filepath.Join(home, ".local", "state", "jentic"),
		filepath.Join(home, ".jentic"),
	}
	if len(got) != len(want) {
		t.Fatalf("AgentIdentityDirs = %v, want %v", got, want)
	}
	for i, dir := range want {
		if got[i] != dir {
			t.Errorf("AgentIdentityDirs[%d] = %q, want %q", i, got[i], dir)
		}
		if filepath.Clean(got[i]) == filepath.Clean(home) || !IsUnderHome(home, got[i]) {
			t.Errorf("AgentIdentityDirs[%d] = %q must be a strict descendant of %q", i, got[i], home)
		}
	}
}

// TestRemoveSudoersIsSafe guards two properties of the sudoers teardown: it edits
// the fixed jentic-agent drop-in and validates with visudo before installing, so
// a malformed result can never brick sudo.
func TestRemoveSudoersIsSafe(t *testing.T) {
	joined := strings.Join(RemoveSudoersCmd("alice-local-agent").Args, " ")
	if !strings.Contains(joined, "/etc/sudoers.d/jentic-agent") {
		t.Errorf("sudoers removal must target the jentic-agent drop-in: %s", joined)
	}
	if !strings.Contains(joined, "visudo") {
		t.Errorf("sudoers removal must validate with visudo before installing: %s", joined)
	}
}

// TestACLUserNeedleTerminated proves the ACL-presence needle is terminated so a
// prefix account can't false-match: probing `alice-local-agent` must not be
// satisfied by an ACE for `alice-local-agent-2`.
func TestACLUserNeedleTerminated(t *testing.T) {
	needle := aclUserNeedle("alice-local-agent")
	// The needle ends in the per-tool delimiter (space on darwin, colon elsewhere),
	// so the sibling's rendered ACE token does not contain it.
	var siblingACE string
	if runtime.GOOS == "darwin" {
		siblingACE = "0: user:alice-local-agent-2 allow read"
	} else {
		siblingACE = "user:alice-local-agent-2:rwx"
	}
	if strings.Contains(siblingACE, needle) {
		t.Errorf("needle %q false-matches sibling ACE %q", needle, siblingACE)
	}
	// And it DOES match the exact account's ACE.
	var ownACE string
	if runtime.GOOS == "darwin" {
		ownACE = "0: user:alice-local-agent allow read"
	} else {
		ownACE = "user:alice-local-agent:rwx"
	}
	if !strings.Contains(ownACE, needle) {
		t.Errorf("needle %q must match own ACE %q", needle, ownACE)
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

func TestVerifyManagedHome(t *testing.T) {
	// A recorded home outside the managed root is refused before any account
	// lookup — this is a real human home, never a jentic-managed one.
	if err := VerifyManagedHome("root", "/home/alice"); err == nil {
		t.Error("expected a non-managed recorded home to be refused")
	}
	// A managed-looking home for an account that does not exist is refused
	// (lookup fails), so reset/reuse never proceed against a phantom account.
	if err := VerifyManagedHome("nope-no-such-agent-xyz", AgentHomeRoot()+"/nope-no-such-agent-xyz"); err == nil {
		t.Error("expected a missing account to be refused")
	}
	// An EXISTING account whose live home differs from the recorded managed home
	// is refused — the name has collided with a different account. We use the
	// current user (which exists but whose home is NOT the managed path).
	me, err := user.Current()
	if err != nil {
		t.Skip("cannot resolve current user")
	}
	if err := VerifyManagedHome(me.Username, AgentHomeRoot()+"/"+me.Username); err == nil {
		t.Error("expected a home mismatch against an existing account to be refused")
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
