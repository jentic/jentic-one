package update

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestBrewManaged(t *testing.T) {
	tests := []struct {
		name     string
		resolved string
		brewBin  string
		want     bool
	}{
		{
			name:     "cask install resolved into Caskroom",
			resolved: "/opt/homebrew/Caskroom/jentic/0.16.0/jenticctl",
			brewBin:  "/opt/homebrew/bin",
			want:     true,
		},
		{
			name:     "formula install resolved into Cellar",
			resolved: "/usr/local/Cellar/jentic/0.16.0/bin/jenticctl",
			brewBin:  "/usr/local/bin",
			want:     true,
		},
		{
			name:     "Caskroom detected even without brew on PATH",
			resolved: "/opt/homebrew/Caskroom/jentic/0.16.0/jenticctl",
			brewBin:  "",
			want:     true,
		},
		{
			name:     "regular file directly in brew bin",
			resolved: "/opt/homebrew/bin/jenticctl",
			brewBin:  "/opt/homebrew/bin",
			want:     true,
		},
		{
			name:     "source install under ~/.jentic/bin",
			resolved: "/home/u/.jentic/bin/jenticctl",
			brewBin:  "/home/linuxbrew/.linuxbrew/bin",
			want:     false,
		},
		{
			name:     "source install resolved out of the brew prefix",
			resolved: "/Users/u/.jentic/bin/jenticctl",
			brewBin:  "/usr/local/bin",
			want:     false,
		},
		{
			name:     "no brew installed, generic path",
			resolved: "/usr/local/bin/jenticctl",
			brewBin:  "",
			want:     false,
		},
		{
			name:     "nested under brew bin but not directly in it",
			resolved: "/opt/homebrew/bin/sub/jenticctl",
			brewBin:  "/opt/homebrew/bin",
			want:     false,
		},
		{
			name:     "Cellar as a filename component, not a segment",
			resolved: "/data/myCellar/bin/jenticctl",
			brewBin:  "",
			want:     false,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := brewManaged(tt.resolved, tt.brewBin); got != tt.want {
				t.Errorf("brewManaged(%q, %q) = %v, want %v", tt.resolved, tt.brewBin, got, tt.want)
			}
		})
	}
}

// TestBrewManagedResolvesSymlinks builds a Caskroom-shaped layout in a temp dir
// with a bin symlink pointing into it (the shape `brew install --cask` leaves
// behind) and checks the symlink is detected as brew-managed.
func TestBrewManagedResolvesSymlinks(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink layout test targets unix")
	}
	root := t.TempDir()
	caskDir := filepath.Join(root, "Caskroom", "jentic", "0.16.0")
	binDir := filepath.Join(root, "bin")
	for _, d := range []string{caskDir, binDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	realBin := filepath.Join(caskDir, "jenticctl")
	if err := os.WriteFile(realBin, []byte("bin"), 0o755); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(binDir, "jenticctl")
	if err := os.Symlink(realBin, link); err != nil {
		t.Fatal(err)
	}

	if !BrewManaged(link) {
		t.Errorf("BrewManaged(%q) = false, want true (resolves into Caskroom)", link)
	}
	if !BrewManaged(realBin) {
		t.Errorf("BrewManaged(%q) = false, want true", realBin)
	}
	outside := filepath.Join(root, "elsewhere", "jenticctl")
	if err := os.MkdirAll(filepath.Dir(outside), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(outside, []byte("bin"), 0o755); err != nil {
		t.Fatal(err)
	}
	if BrewManaged(outside) {
		t.Errorf("BrewManaged(%q) = true, want false", outside)
	}
}
