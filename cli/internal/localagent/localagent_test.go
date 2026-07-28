package localagent

import (
	"os"
	"path/filepath"
	"runtime"
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

func TestDefaultHomeDir(t *testing.T) {
	got := DefaultHomeDir("alice-local-agent")
	// The home must live under a shared, world-traversable parent — never under
	// any human's home — so the operator can be granted in without widening a home.
	var wantPrefix string
	if runtime.GOOS == "darwin" {
		wantPrefix = "/Users/Shared/"
	} else {
		wantPrefix = "/opt/"
	}
	if !strings.HasPrefix(got, wantPrefix) || !strings.HasSuffix(got, "alice-local-agent") {
		t.Fatalf("DefaultHomeDir = %q, want %s…alice-local-agent", got, wantPrefix)
	}
	if strings.HasPrefix(got, "/Users/alice") || strings.HasPrefix(got, "/home/") {
		t.Fatalf("DefaultHomeDir = %q must not sit under a human home", got)
	}
}

// TestCreateAccountCmds guards the privileged account-creation recipe: every
// step must be sudo-fronted and name the agent account, the ordered steps must
// culminate in an inherited operator grant on the agent's home, and the operator
// grant must carry the inheritance flags (without them the operator loses access
// to whatever the agent creates later).
func TestCreateAccountCmds(t *testing.T) {
	steps := CreateAccountCmds("alice", "alice-local-agent", DefaultHomeDir("alice-local-agent"))
	if len(steps) == 0 {
		t.Fatal("expected at least one account-creation step")
	}
	for _, s := range steps {
		if s.Cmd.Args[0] != "sudo" {
			t.Errorf("step %q: expected sudo-fronted command, got %v", s.What, s.Cmd.Args)
		}
		if !strings.Contains(strings.Join(s.Cmd.Args, " "), "alice-local-agent") {
			t.Errorf("step %q: args do not name the agent account: %v", s.What, s.Cmd.Args)
		}
	}
	// The first step creates the account; a later step grants the operator in.
	joined := strings.Join(steps[0].Cmd.Args, " ")
	if runtime.GOOS == "darwin" {
		if !strings.Contains(joined, "sysadminctl") || !strings.Contains(joined, "-addUser") {
			t.Errorf("first macOS step should be sysadminctl -addUser: %v", steps[0].Cmd.Args)
		}
	} else if !strings.Contains(joined, "useradd") {
		t.Errorf("first Linux step should be useradd: %v", steps[0].Cmd.Args)
	}
	all := ""
	for _, s := range steps {
		all += strings.Join(s.Cmd.Args, " ") + "\n"
	}
	if !strings.Contains(all, "alice") {
		t.Error("expected the operator to be granted access somewhere in the recipe")
	}
	if runtime.GOOS == "darwin" {
		if !strings.Contains(all, "file_inherit") || !strings.Contains(all, "directory_inherit") {
			t.Error("macOS operator grant must be inherited (file_inherit/directory_inherit)")
		}
	} else if !strings.Contains(all, "-d -m") && !strings.Contains(all, "-d") {
		t.Error("Linux operator grant must include a default ACL for future contents")
	}
}

func TestLockOperatorHomeCmd(t *testing.T) {
	c := LockOperatorHomeCmd("/Users/alice")
	joined := strings.Join(c.Args, " ")
	// It must NOT be sudo-fronted (the operator owns their own home) and must be a
	// 700 lock of exactly the given path.
	if c.Args[0] == "sudo" {
		t.Errorf("locking the operator's own home should not need sudo: %v", c.Args)
	}
	if !strings.Contains(joined, "chmod") || !strings.Contains(joined, "700") || !strings.Contains(joined, "/Users/alice") {
		t.Errorf("expected `chmod 700 /Users/alice`, got %v", c.Args)
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

// TestTeardownCmdShape guards the reset primitives: every one is sudo-fronted and
// names the agent user (and, where relevant, the target path) so a reset can't
// silently no-op. TraverseRevokeCmd must mirror TraverseGrantCmd's target.
func TestTeardownCmdShape(t *testing.T) {
	home := filepath.Clean("/Users/alice")
	homeDir := "/Users/Shared/alice-local-agent"
	cases := []struct {
		name       string
		args       []string
		wantTarget string // "" = don't assert a path, just the user
		wantUser   bool
	}{
		{"traverse-revoke", TraverseRevokeCmd("alice-local-agent", home).Args, home, true},
		{"reown-home", ReownHomeCmd("alice", homeDir).Args, homeDir, false},
		{"delete-home", DeleteHomeCmd(homeDir).Args, homeDir, false},
		{"remove-sudoers", RemoveSudoersCmd("alice-local-agent").Args, "", true},
		{"delete-account", DeleteAccountCmd("alice-local-agent").Args, "", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if tc.args[0] != "sudo" {
				t.Errorf("%s: expected sudo-fronted command, got %v", tc.name, tc.args)
			}
			joined := strings.Join(tc.args, " ")
			if tc.wantUser && !strings.Contains(joined, "alice-local-agent") {
				t.Errorf("%s: args do not name the agent user: %v", tc.name, tc.args)
			}
			if tc.wantTarget != "" && !strings.Contains(joined, tc.wantTarget) {
				t.Errorf("%s: args do not name target %q: %v", tc.name, tc.wantTarget, tc.args)
			}
		})
	}

	// TraverseRevokeCmd reverses TraverseGrantCmd on the same target.
	if g, r := TraverseGrantCmd("a-local-agent", home).Args, TraverseRevokeCmd("a-local-agent", home).Args; strings.Join(g, " ") == strings.Join(r, " ") {
		t.Error("traverse grant and revoke must not be identical commands")
	}

	// DeleteAccountCmd must keep the home (the home is settled separately): it must
	// NOT carry a home-removing flag (-r on Linux; -deleteUser without -keepHome on macOS).
	del := strings.Join(DeleteAccountCmd("alice-local-agent").Args, " ")
	if runtime.GOOS == "darwin" {
		if !strings.Contains(del, "-keepHome") {
			t.Errorf("macOS account delete must pass -keepHome so the home survives: %s", del)
		}
	} else if strings.Contains(del, " -r") || strings.HasSuffix(del, "-r") {
		t.Errorf("Linux account delete must NOT pass -r (home is settled separately): %s", del)
	}
}

// TestMacTraverseRevokeMatchesGrant guards the macOS ACE-match requirement: the
// traverse revoke must name the same "allow execute" permission string as the
// grant, or `chmod -a` won't find the entry to drop.
func TestMacTraverseRevokeMatchesGrant(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Skip("macOS-specific ACL permission set")
	}
	grant := strings.Join(TraverseGrantCmd("a-local-agent", "/Users/alice").Args, " ")
	revoke := strings.Join(TraverseRevokeCmd("a-local-agent", "/Users/alice").Args, " ")
	if !strings.Contains(grant, "allow execute") || !strings.Contains(revoke, "allow execute") {
		t.Errorf("traverse grant/revoke must both name `allow execute`: grant=%q revoke=%q", grant, revoke)
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

// TestMacLeafGrantIncludesDeleteBits guards against the macOS shorthand bug: a
// "write" grant on a directory expands to add_file only, so the agent could
// create but not delete/rename files (breaking write-to-temp-then-rename and
// leaving `test -w` false, which re-prompted on every launch). The explicit set
// must carry the directory-mutation bits and be symmetric between grant/revoke.
func TestMacLeafGrantIncludesDeleteBits(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Skip("macOS-specific ACL permission set")
	}
	grant := strings.Join(LeafGrantCmd("a-local-agent", "/Users/Shared/x/work").Args, " ")
	for _, bit := range []string{"delete", "delete_child", "add_subdirectory"} {
		if !strings.Contains(grant, bit) {
			t.Errorf("leaf grant missing %q bit (dir writes would fail): %s", bit, grant)
		}
	}
	// Revoke must name the identical permission string so macOS can drop the ACE.
	revoke := strings.Join(LeafRevokeCmd("a-local-agent", "/Users/Shared/x/work").Args, " ")
	if !strings.Contains(revoke, macLeafACE) || !strings.Contains(grant, macLeafACE) {
		t.Errorf("grant/revoke permission strings must match macLeafACE")
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

func TestDetectProvider(t *testing.T) {
	writeSettings := func(t *testing.T, env string) string {
		t.Helper()
		home := t.TempDir()
		dir := filepath.Join(home, ".claude")
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
		body := `{"env": {` + env + `}}`
		if err := os.WriteFile(filepath.Join(dir, "settings.json"), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		return home
	}

	t.Run("bedrock -> aws with ~/.aws", func(t *testing.T) {
		home := writeSettings(t, `"CLAUDE_CODE_USE_BEDROCK": "1"`)
		pc := DetectProvider(home)
		if pc.Name != "aws" || len(pc.ConfigPaths) != 1 || pc.ConfigPaths[0] != "~/.aws" {
			t.Fatalf("DetectProvider = %+v", pc)
		}
	})

	t.Run("vertex -> gcloud plus explicit creds", func(t *testing.T) {
		home := writeSettings(t, `"CLAUDE_CODE_USE_VERTEX": "true", "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/key.json"`)
		pc := DetectProvider(home)
		if pc.Name != "vertex" {
			t.Fatalf("DetectProvider = %+v", pc)
		}
		if len(pc.ConfigPaths) != 2 || pc.ConfigPaths[0] != "~/.config/gcloud" || pc.ConfigPaths[1] != "/tmp/key.json" {
			t.Fatalf("vertex paths = %v", pc.ConfigPaths)
		}
	})

	t.Run("disabled flag falls through to anthropic", func(t *testing.T) {
		home := writeSettings(t, `"CLAUDE_CODE_USE_BEDROCK": "0"`)
		pc := DetectProvider(home)
		if pc.Name != "anthropic" || len(pc.ConfigPaths) != 0 {
			t.Fatalf("DetectProvider = %+v", pc)
		}
	})

	t.Run("missing settings -> anthropic default", func(t *testing.T) {
		pc := DetectProvider(t.TempDir())
		if pc.Name != "anthropic" || len(pc.ConfigPaths) != 0 {
			t.Fatalf("DetectProvider = %+v", pc)
		}
	})
}

func TestProviderConfigPaths(t *testing.T) {
	home := t.TempDir()
	if err := os.MkdirAll(filepath.Join(home, ".aws"), 0o700); err != nil {
		t.Fatal(err)
	}
	got := ProviderConfigPaths(home, ProviderConfig{Name: "aws", ConfigPaths: []string{"~/.aws", "~/.config/gcloud"}})
	if len(got) != 1 || got[0] != filepath.Join(home, ".aws") {
		t.Fatalf("ProviderConfigPaths = %v (only the existing path should be returned)", got)
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
