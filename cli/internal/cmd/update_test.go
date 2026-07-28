package cmd

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
)

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
