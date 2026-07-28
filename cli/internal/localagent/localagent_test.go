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
