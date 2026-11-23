package localagent

import (
	"path/filepath"
	"testing"
)

func TestSeededConfigDirsCoversOperatorsAndProvidersUnderHome(t *testing.T) {
	home := "/Users/Shared/alice-local-agent"
	got := SeededConfigDirs(home)
	set := map[string]bool{}
	for _, p := range got {
		set[p] = true
	}
	// Every coding agent's config dir must be scrubbed…
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
	paths := []string{"/Users/Shared/a/.aws", "/Users/Shared/a/.codex"}
	cmd := ScrubSeededConfigCmd(paths)
	if cmd == nil {
		t.Fatal("expected a command for a non-empty list")
	}
	// Injection-proof shape: a DIRECT sudo argv (never `sh -c`), `rm -rf`, then a
	// `--` guard, then each path as its OWN argv element. This is what stops a path
	// beginning with '-' from being read as a flag and stops any shell metachar in
	// a path from being interpreted — there is no shell in the loop at all.
	want := []string{"sudo", "rm", "-rf", "--", paths[0], paths[1]}
	if len(cmd.Args) != len(want) {
		t.Fatalf("argv = %v, want %v", cmd.Args, want)
	}
	for i := range want {
		if cmd.Args[i] != want[i] {
			t.Errorf("argv[%d] = %q, want %q (full: %v)", i, cmd.Args[i], want[i], cmd.Args)
		}
	}
	if cmd.Args[0] == "sh" || (len(cmd.Args) > 1 && cmd.Args[1] == "-c") {
		t.Errorf("scrub must not route through a shell: %v", cmd.Args)
	}
}
