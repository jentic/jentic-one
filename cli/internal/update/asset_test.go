package update

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestAssetNameGoldenTable pins the exact published asset filenames for the
// os/arch matrix so the installer's shell name-construction and this Go helper
// can never drift apart (binary-distribution P0↔P3 lockstep).
func TestAssetNameGoldenTable(t *testing.T) {
	cases := []struct {
		binary, version, goos, goarch, want string
	}{
		{"jentic", "v0.31.0", "linux", "amd64", "jentic_0.31.0_linux_amd64.tar.gz"},
		{"jentic", "0.31.0", "darwin", "arm64", "jentic_0.31.0_darwin_arm64.tar.gz"},
		{"jentic", "v0.31.0", "windows", "amd64", "jentic_0.31.0_windows_amd64.zip"},
		{"jenticctl", "v0.31.0", "linux", "arm64", "jenticctl_0.31.0_linux_arm64.tar.gz"},
	}
	for _, c := range cases {
		if got := AssetName(c.binary, c.version, c.goos, c.goarch); got != c.want {
			t.Errorf("AssetName(%q,%q,%q,%q) = %q, want %q", c.binary, c.version, c.goos, c.goarch, got, c.want)
		}
	}
}

// TestAssetNameMatchesGoreleaserTemplate locks the helper to the actual YAML
// name_template so a revert of the goreleaser config to a different template
// (e.g. re-adding goreleaser's default Amd64/Arm suffixes, or the old
// ProjectName-based name) can't silently change the download URL the updater
// builds. It reads the committed .goreleaser.yaml and asserts the per-binary
// template string is present verbatim.
func TestAssetNameMatchesGoreleaserTemplate(t *testing.T) {
	// internal/update/asset_test.go → ../../.goreleaser.yaml (cli/.goreleaser.yaml).
	path := filepath.Join("..", "..", ".goreleaser.yaml")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read goreleaser config: %v", err)
	}
	yaml := string(data)
	// The jentic per-binary archive uses this exact template (whitespace-folded
	// YAML `>-`). AssetName reproduces it: <binary>_<version>_<os>_<arch>.
	want := "jentic_{{ .Version }}_{{ .Os }}_{{ .Arch }}"
	if !strings.Contains(yaml, want) {
		t.Errorf("goreleaser config no longer contains the per-binary jentic name_template %q; "+
			"AssetName would drift from the published asset URL", want)
	}
	if !strings.Contains(yaml, "jenticctl_{{ .Version }}_{{ .Os }}_{{ .Arch }}") {
		t.Errorf("goreleaser config no longer contains the jenticctl per-binary name_template")
	}
}
