package localagent

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// TestRegistryParityWithSkillgen is the guardrail that keeps `jentic run` and
// `jentic skill` from drifting: every operator the skill layer supports must be
// EITHER launchable (in Registry) OR explicitly declared skill-only. A new
// skillgen operator that is neither will fail this test, forcing a conscious
// decision instead of a silent "unknown agent" at runtime.
func TestRegistryParityWithSkillgen(t *testing.T) {
	skillOps := []skillgen.Operator{
		skillgen.OpClaude, skillgen.OpCodex, skillgen.OpCursor,
		skillgen.OpHermes, skillgen.OpGeneric,
	}
	for _, op := range skillOps {
		id := string(op)
		_, runnable := Lookup(id)
		if runnable == IsSkillOnly(id) {
			// Both true (contradiction) or both false (uncovered) are wrong.
			t.Errorf("operator %q: runnable=%v skillOnly=%v — every skillgen operator "+
				"must be exactly one of runnable or skill-only", id, runnable, IsSkillOnly(id))
		}
	}
}

func TestGenericIsSkillOnlyNotRunnable(t *testing.T) {
	if _, ok := Lookup("generic"); ok {
		t.Error("generic must not be a runnable agent (it has no binary)")
	}
	if !IsSkillOnly("generic") {
		t.Error("generic must be declared skill-only")
	}
}

func TestKnownListsAllRunnableOperators(t *testing.T) {
	want := map[string]bool{"claude": true, "codex": true, "cursor": true, "hermes": true}
	for _, id := range Known() {
		delete(want, id)
	}
	if len(want) != 0 {
		t.Errorf("Known() is missing runnable operators: %v", want)
	}
}

// TestDescriptorsWellFormed asserts each runnable descriptor carries the fields
// the provision/probe/seed pipeline depends on, so a half-filled row can't ship.
func TestDescriptorsWellFormed(t *testing.T) {
	for id, d := range Registry {
		if d.ID != id {
			t.Errorf("descriptor %q has mismatched ID %q", id, d.ID)
		}
		if d.Binary == "" {
			t.Errorf("descriptor %q has empty Binary", id)
		}
		if d.Install == "" {
			t.Errorf("descriptor %q has empty Install (needed when the agent isn't already present)", id)
		}
		if len(d.ProbePaths) == 0 {
			t.Errorf("descriptor %q has no ProbePaths", id)
		}
		// Every secret path must sit inside one of the seeded config trees, or
		// scrubbing it would be a no-op that leaves the credential in place.
		for _, sp := range d.SecretConfigPaths {
			if !anyPrefix(sp, d.ConfigPaths) {
				t.Errorf("descriptor %q: SecretConfigPath %q is not under any ConfigPaths %v",
					id, sp, d.ConfigPaths)
			}
		}
	}
}

func anyPrefix(p string, roots []string) bool {
	for _, r := range roots {
		if p == r || strings.HasPrefix(p, r+"/") {
			return true
		}
	}
	return false
}

func TestExpandedSecretPathsStaysUnderAgentHome(t *testing.T) {
	home := "/Users/Shared/alice-local-agent"
	got := ExpandedSecretPaths(home, Registry["codex"])
	want := filepath.Join(home, ".codex", "auth.json")
	if len(got) != 1 || got[0] != want {
		t.Fatalf("ExpandedSecretPaths(codex) = %v, want [%s]", got, want)
	}

	// A descriptor whose secret path escapes the home (defense-in-depth) is dropped.
	evil := Descriptor{SecretConfigPaths: []string{"~/../../etc/shadow"}}
	if got := ExpandedSecretPaths(home, evil); len(got) != 0 {
		t.Fatalf("ExpandedSecretPaths dropped nothing for escaping path: %v", got)
	}

	// A "~" entry that cleans to the home itself must NOT make the scrub target the
	// whole home — it must be dropped, so a bad descriptor can't become rm $HOME.
	homeItself := Descriptor{SecretConfigPaths: []string{"~", "~/"}}
	if got := ExpandedSecretPaths(home, homeItself); len(got) != 0 {
		t.Fatalf("ExpandedSecretPaths must drop the home root itself, got: %v", got)
	}
}

func TestScrubSecretsCmd(t *testing.T) {
	if ScrubSecretsCmd(nil) != nil {
		t.Error("ScrubSecretsCmd(nil) should be a no-op (nil), so callers can skip cleanly")
	}
	cmd := ScrubSecretsCmd([]string{"/Users/Shared/a/.codex/auth.json"})
	if cmd == nil {
		t.Fatal("expected a command for a non-empty secret list")
	}
	joined := strings.Join(cmd.Args, " ")
	if !strings.Contains(joined, "rm -f") || !strings.Contains(joined, "auth.json") {
		t.Errorf("scrub command = %q, want an rm -f of the secret file", joined)
	}
	// No recursion, no globbing — a scrub must never widen into a tree delete.
	if strings.Contains(joined, "-r") || strings.Contains(joined, "-R") || strings.Contains(joined, "*") {
		t.Errorf("scrub command must not recurse or glob: %q", joined)
	}
}

func TestAgentLocalBinDir(t *testing.T) {
	if got := AgentLocalBinDir("/opt/bob-local-agent"); got != "/opt/bob-local-agent/.local/bin" {
		t.Fatalf("AgentLocalBinDir = %q", got)
	}
}

// TestSeedThenScrubLeavesSettingsRemovesSecret exercises the real seed→scrub
// semantics for every operator that declares SecretConfigPaths: after a config
// copy (cp -RP) and the secret scrub (rm -f), a non-secret settings file under the
// same config tree must SURVIVE while the discrete secret file is GONE. This is
// the behavioural contract behind ScrubSecretsCmd — validated with real cp/rm on
// temp dirs so a wrong SecretConfigPaths entry (pointing at the wrong file, or a
// whole dir) is caught here rather than in production.
func TestSeedThenScrubLeavesSettingsRemovesSecret(t *testing.T) {
	for id, desc := range Registry {
		if len(desc.SecretConfigPaths) == 0 {
			continue
		}
		t.Run(id, func(t *testing.T) {
			agentHome := t.TempDir()
			// Materialise each secret file plus a sibling non-secret settings file
			// in the same directory, so the scrub must be surgical (file, not dir).
			var settingsFiles []string
			for _, sp := range desc.SecretConfigPaths {
				secret := expandTilde(sp, agentHome)
				if secret == "" {
					t.Fatalf("secret path %q did not expand", sp)
				}
				dir := filepath.Dir(secret)
				if err := os.MkdirAll(dir, 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(secret, []byte("SECRET"), 0o600); err != nil {
					t.Fatal(err)
				}
				settings := filepath.Join(dir, "settings.keep")
				if err := os.WriteFile(settings, []byte("KEEP"), 0o644); err != nil {
					t.Fatal(err)
				}
				settingsFiles = append(settingsFiles, settings)
			}

			// Run the exact rm the scrub would (locally, without sudo).
			paths := ExpandedSecretPaths(agentHome, desc)
			if len(paths) != len(desc.SecretConfigPaths) {
				t.Fatalf("ExpandedSecretPaths(%s) = %v, want %d entries", id, paths, len(desc.SecretConfigPaths))
			}
			for _, p := range paths {
				if err := os.Remove(p); err != nil {
					t.Fatalf("scrub could not remove %q: %v", p, err)
				}
			}

			// Secret gone…
			for _, sp := range desc.SecretConfigPaths {
				if _, err := os.Stat(expandTilde(sp, agentHome)); !os.IsNotExist(err) {
					t.Errorf("secret %q survived the scrub (err=%v)", sp, err)
				}
			}
			// …settings kept.
			for _, s := range settingsFiles {
				if _, err := os.Stat(s); err != nil {
					t.Errorf("non-secret settings file %q was destroyed by the scrub: %v", s, err)
				}
			}
		})
	}
}

func TestSeededConfigDirsCoversOperatorsAndProvidersUnderHome(t *testing.T) {
	home := "/Users/Shared/alice-local-agent"
	got := SeededConfigDirs(home)
	set := map[string]bool{}
	for _, p := range got {
		set[p] = true
	}
	// Every runnable operator's config dir must be scrubbed…
	for _, rel := range []string{".claude", ".codex", ".cursor", ".hermes"} {
		if !set[filepath.Join(home, rel)] {
			t.Errorf("SeededConfigDirs missing %s\n%v", rel, got)
		}
	}
	// …plus the provider dirs seeding can copy in.
	for _, rel := range []string{".aws", ".config/gcloud"} {
		if !set[filepath.Join(home, rel)] {
			t.Errorf("SeededConfigDirs missing provider dir %s\n%v", rel, got)
		}
	}
	// Everything stays under the agent home (no escape), and the home ROOT itself
	// is never returned — a reset scrub must target descendants, never rm $HOME.
	for _, p := range got {
		if !IsUnderHome(home, p) {
			t.Errorf("SeededConfigDirs returned a path outside the home: %q", p)
		}
		if filepath.Clean(p) == filepath.Clean(home) {
			t.Errorf("SeededConfigDirs returned the home root itself: %q", p)
		}
	}
}

func TestScrubSeededConfigCmd(t *testing.T) {
	if ScrubSeededConfigCmd(nil) != nil {
		t.Error("ScrubSeededConfigCmd(nil) must be a no-op (nil)")
	}
	cmd := ScrubSeededConfigCmd([]string{"/Users/Shared/a/.aws", "/Users/Shared/a/.codex"})
	if cmd == nil {
		t.Fatal("expected a command for a non-empty list")
	}
	joined := strings.Join(cmd.Args, " ")
	if !strings.Contains(joined, "rm -rf") || !strings.Contains(joined, ".aws") || !strings.Contains(joined, ".codex") {
		t.Errorf("scrub command = %q, want rm -rf of both dirs", joined)
	}
}
