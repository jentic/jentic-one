package update

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSystemPackageManaged(t *testing.T) {
	tests := []struct {
		name   string
		target string
		want   bool
	}{
		{"deb/rpm/apk install", "/usr/bin/jenticctl", true},
		{"unclean path into /usr/bin", "/usr/lib/../bin/jenticctl", true},
		{"install.sh default dir", "/home/u/.jentic/bin/jenticctl", false},
		{"manual install into /usr/local/bin", "/usr/local/bin/jenticctl", false},
		{"Linuxbrew cask", "/home/linuxbrew/.linuxbrew/Caskroom/jentic/0.44.0/jenticctl", false},
		{"nested under /usr/bin", "/usr/bin/jentic/jenticctl", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := SystemPackageManaged(tt.target); got != tt.want {
				t.Errorf("SystemPackageManaged(%q) = %v, want %v", tt.target, got, tt.want)
			}
		})
	}
}

// TestSystemPackageManagedResolvesSymlinks pins that a path reached through a
// symlink is judged by where it resolves to, in both directions.
func TestSystemPackageManagedResolvesSymlinks(t *testing.T) {
	root := t.TempDir()
	pkgDir := filepath.Join(root, "usr", "bin")
	otherDir := filepath.Join(root, "home", "bin")
	linkDir := filepath.Join(root, "usr", "local", "bin")
	for _, d := range []string{pkgDir, otherDir, linkDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	pkgBin := filepath.Join(pkgDir, "jenticctl")
	userBin := filepath.Join(otherDir, "jenticctl")
	for _, f := range []string{pkgBin, userBin} {
		if err := os.WriteFile(f, []byte("x"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	toPkg := filepath.Join(linkDir, "to-pkg")
	toUser := filepath.Join(pkgDir, "to-user")
	if err := os.Symlink(pkgBin, toPkg); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if err := os.Symlink(userBin, toUser); err != nil {
		t.Fatal(err)
	}
	aliasDir := filepath.Join(root, "bin")
	if err := os.Symlink(pkgDir, aliasDir); err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name   string
		target string
		want   bool
	}{
		{"binary in the package dir", pkgBin, true},
		{"symlink elsewhere pointing into the package dir", toPkg, true},
		{"symlink in the package dir pointing out of it", toUser, false},
		{"binary outside the package dir", userBin, false},
		{"reached through a merged-/usr style dir alias", filepath.Join(aliasDir, "jenticctl"), true},
		{"missing file in the package dir", filepath.Join(pkgDir, "absent"), true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := systemPackageManaged(tt.target, pkgDir); got != tt.want {
				t.Errorf("systemPackageManaged(%q) = %v, want %v", tt.target, got, tt.want)
			}
		})
	}
}
