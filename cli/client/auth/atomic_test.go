package auth

import (
	"os"
	"path/filepath"
	"testing"
)

func TestWriteFileAtomic_WritesContentAndPerm(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "secret.key")
	if err := writeFileAtomic(path, []byte("PRIVATE")); err != nil {
		t.Fatalf("writeFileAtomic: %v", err)
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "PRIVATE" {
		t.Errorf("content = %q, want PRIVATE", got)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Errorf("perm = %o, want 600", perm)
	}
}

func TestWriteFileAtomic_OverwritesInPlace(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "tokens.json")
	if err := os.WriteFile(path, []byte("OLD"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := writeFileAtomic(path, []byte("NEW-CONTENT")); err != nil {
		t.Fatalf("writeFileAtomic: %v", err)
	}
	got, _ := os.ReadFile(path)
	if string(got) != "NEW-CONTENT" {
		t.Errorf("content = %q, want NEW-CONTENT", got)
	}
}

// TestWriteFileAtomic_LeavesNoTempOnSuccess ensures the temp file is renamed (not
// left as a sibling) — the whole point of the atomic write is a clean directory
// with exactly the target file.
func TestWriteFileAtomic_LeavesNoTempOnSuccess(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "cred.apikey")
	if err := writeFileAtomic(path, []byte("k")); err != nil {
		t.Fatalf("writeFileAtomic: %v", err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != "cred.apikey" {
		names := make([]string, 0, len(entries))
		for _, e := range entries {
			names = append(names, e.Name())
		}
		t.Errorf("expected exactly [cred.apikey] in dir, got %v", names)
	}
}

// TestWriteFileAtomic_NoPartialFileOnEmptyWrite guards the core invariant: even a
// zero-length payload results in a valid (empty) file, never a missing/torn one.
func TestWriteFileAtomic_ZeroLengthWrite(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "empty")
	if err := writeFileAtomic(path, nil); err != nil {
		t.Fatalf("writeFileAtomic: %v", err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("file should exist: %v", err)
	}
	if info.Size() != 0 {
		t.Errorf("size = %d, want 0", info.Size())
	}
}
