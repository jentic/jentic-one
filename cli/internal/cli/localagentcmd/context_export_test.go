// The context export pins POSIX permission bits (0700 dirs / 0600 files),
// which Windows does not model — Lstat reports 0777/0666 there regardless of
// Chmod — and the isolated-agent launch this file supports is Unix-only.
//
//go:build !windows

package localagentcmd

import (
	"os"
	"path/filepath"
	"testing"
)

// TestPinDirMode0700 guards the repeat-launch behaviour of the context export:
// the export chowns the agent's XDG dirs to the agent uid after the first
// launch, and POSIX gates chmod on ownership, so the pin MUST skip a dir whose
// mode is already 0700 (the operator can no longer chmod it and doesn't need
// to) while still tightening a dir that is genuinely wider.
func TestPinDirMode0700(t *testing.T) {
	// Already at the floor → no-op, no error. (We can't drop ownership in a
	// unit test, but the skip path is exactly what makes the agent-owned case
	// work: chmod is never attempted.)
	tight := filepath.Join(t.TempDir(), "tight")
	if err := os.Mkdir(tight, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := pinDirMode0700(tight); err != nil {
		t.Fatalf("pinDirMode0700(already 0700) = %v, want nil", err)
	}

	// Wider than the floor → tightened to exactly 0700.
	wide := filepath.Join(t.TempDir(), "wide")
	if err := os.Mkdir(wide, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := pinDirMode0700(wide); err != nil {
		t.Fatalf("pinDirMode0700(0755) = %v", err)
	}
	info, err := os.Lstat(wide)
	if err != nil {
		t.Fatal(err)
	}
	if got := info.Mode().Perm(); got != 0o700 {
		t.Errorf("mode after pin = %o, want 0700", got)
	}

	// A missing dir is a real error (the caller just created it).
	if err := pinDirMode0700(filepath.Join(t.TempDir(), "missing")); err == nil {
		t.Error("pinDirMode0700(missing) = nil, want error")
	}
}

// TestWriteFile0600 covers fresh creation, overwrite of a pre-existing 0600
// file (the repeat-launch path: the file may be agent-owned, so chmod must be
// skipped when the mode already matches), and tightening a wider file.
func TestWriteFile0600(t *testing.T) {
	dir := t.TempDir()

	// Fresh file lands 0600 with the payload.
	fresh := filepath.Join(dir, "fresh.yaml")
	if err := writeFile0600(fresh, []byte("a: 1\n")); err != nil {
		t.Fatalf("writeFile0600(fresh) = %v", err)
	}
	assertMode(t, fresh, 0o600)
	if data, _ := os.ReadFile(fresh); string(data) != "a: 1\n" {
		t.Errorf("payload = %q", data)
	}

	// Overwriting an already-0600 file succeeds and replaces the content.
	if err := writeFile0600(fresh, []byte("b: 2\n")); err != nil {
		t.Fatalf("writeFile0600(existing 0600) = %v", err)
	}
	assertMode(t, fresh, 0o600)
	if data, _ := os.ReadFile(fresh); string(data) != "b: 2\n" {
		t.Errorf("payload after overwrite = %q", data)
	}

	// A pre-existing wider file is tightened to 0600 on write.
	wide := filepath.Join(dir, "wide.yaml")
	if err := os.WriteFile(wide, []byte("old"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := writeFile0600(wide, []byte("new")); err != nil {
		t.Fatalf("writeFile0600(existing 0644) = %v", err)
	}
	assertMode(t, wide, 0o600)
}

func assertMode(t *testing.T, path string, want os.FileMode) {
	t.Helper()
	info, err := os.Lstat(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := info.Mode().Perm(); got != want {
		t.Errorf("%s mode = %o, want %o", path, got, want)
	}
}
