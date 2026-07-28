package cmd

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
)

// TestUpdateNeeded covers the per-half gating: the stack half must be gated on
// its own recorded ref, not the CLI binary's version (a brew-managed CLI is
// refreshed out-of-band while the stack lags).
func TestUpdateNeeded(t *testing.T) {
	tests := []struct {
		name                             string
		doCLI, doStack                   bool
		cliVersion, stackVersion, latest string
		want                             bool
	}{
		{"lockstep source install up to date", true, true, "0.16.0", "v0.16.0", "v0.16.0", false},
		{"lockstep source install behind", true, true, "0.15.0", "v0.15.0", "v0.16.0", true},
		{"brew degrade: fresh CLI, stale stack", false, true, "0.16.0", "v0.15.0", "v0.16.0", true},
		{"brew degrade: fresh CLI, fresh stack", false, true, "0.16.0", "v0.16.0", "v0.16.0", false},
		{"brew degrade: no stack manifest falls back to cli version", false, true, "0.16.0", "0.16.0", "v0.16.0", false},
		{"cli-only behind", true, false, "0.15.0", "v0.16.0", "v0.16.0", true},
		{"cli-only current, stack stale but not requested", true, false, "0.16.0", "v0.15.0", "v0.16.0", false},
		{"unparseable stack ref (branch install) always offers rebuild", false, true, "0.16.0", "main", "v0.16.0", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := updateNeeded(tt.doCLI, tt.doStack, tt.cliVersion, tt.stackVersion, tt.latest)
			if got != tt.want {
				t.Errorf("updateNeeded(%v, %v, %q, %q, %q) = %v, want %v",
					tt.doCLI, tt.doStack, tt.cliVersion, tt.stackVersion, tt.latest, got, tt.want)
			}
		})
	}
}

// TestResolveCtlTargetResolvesManifestSymlink covers the manifest branch: a
// recorded BinaryPath that is a PATH symlink (what `jenticctl install` records
// under a linked install) must resolve to the real file so the update swaps
// the binary, not the link.
func TestResolveCtlTargetResolvesManifestSymlink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink test targets unix")
	}
	root := t.TempDir()
	realDir := filepath.Join(root, "real")
	linkDir := filepath.Join(root, "linkbin")
	for _, d := range []string{realDir, linkDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	realBin := filepath.Join(realDir, "jenticctl")
	if err := os.WriteFile(realBin, []byte("bin"), 0o755); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(linkDir, "jenticctl")
	if err := os.Symlink(realBin, link); err != nil {
		t.Fatal(err)
	}

	got, err := resolveCtlTarget(&config.Manifest{BinaryPath: link})
	if err != nil {
		t.Fatalf("resolveCtlTarget: %v", err)
	}
	want, _ := filepath.EvalSymlinks(realBin) // temp dirs may themselves be symlinked (macOS /tmp)
	if got != want {
		t.Errorf("resolveCtlTarget = %q, want %q", got, want)
	}
}

// TestResolveCtlTargetKeepsMissingManifestPath: a stale manifest path that no
// longer exists is returned verbatim (the swap recreates it).
func TestResolveCtlTargetKeepsMissingManifestPath(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "gone", "jenticctl")
	got, err := resolveCtlTarget(&config.Manifest{BinaryPath: missing})
	if err != nil {
		t.Fatalf("resolveCtlTarget: %v", err)
	}
	if got != missing {
		t.Errorf("resolveCtlTarget = %q, want %q", got, missing)
	}
}

// TestResolveCtlTargetFallsBackToExecutable: with no manifest path the running
// executable is used.
func TestResolveCtlTargetFallsBackToExecutable(t *testing.T) {
	got, err := resolveCtlTarget(&config.Manifest{})
	if err != nil {
		t.Fatalf("resolveCtlTarget: %v", err)
	}
	if got == "" {
		t.Error("resolveCtlTarget returned empty path for executable fallback")
	}
}

// TestApplyPromptTitle: the confirm prompt must not promise repo@ref for a
// brew-managed CLI half (brew ships only its latest cask), while non-brew
// wording is unchanged.
func TestApplyPromptTitle(t *testing.T) {
	const repo, ref = "jentic/jentic-one", "v0.21.0"
	tests := []struct {
		name                 string
		doCLI, doStack, brew bool
		want                 string
	}{
		{"source combined", true, true, false, "Update the CLI and the stack to jentic/jentic-one@v0.21.0?"},
		{"source cli-only", true, false, false, "Update the CLI to jentic/jentic-one@v0.21.0?"},
		{"stack-only", false, true, false, "Update the stack to jentic/jentic-one@v0.21.0?"},
		{"brew combined", true, true, true, "Update the CLI (via `brew upgrade jentic`) and the stack (from jentic/jentic-one@v0.21.0)?"},
		{"brew cli-only", true, false, true, "Update the CLI via `brew upgrade jentic`?"},
		{"brew stack-only unaffected", false, true, true, "Update the stack to jentic/jentic-one@v0.21.0?"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := applyPromptTitle(tt.doCLI, tt.doStack, tt.brew, repo, ref)
			if got != tt.want {
				t.Errorf("applyPromptTitle(%v, %v, %v) = %q, want %q", tt.doCLI, tt.doStack, tt.brew, got, tt.want)
			}
		})
	}
}
