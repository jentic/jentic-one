package update

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReplaceBinaryBacksUpAndSwaps(t *testing.T) {
	dir := t.TempDir()
	stageDir := t.TempDir() // separate dir to exercise the copy-then-rename path

	target := filepath.Join(dir, "jentic")
	staged := filepath.Join(stageDir, "jentic")
	if err := os.WriteFile(target, []byte("OLD"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(staged, []byte("NEW"), 0o755); err != nil {
		t.Fatal(err)
	}

	backup, err := ReplaceBinary(target, staged)
	if err != nil {
		t.Fatalf("ReplaceBinary: %v", err)
	}
	if backup != target+".bak" {
		t.Errorf("backup = %q, want %q", backup, target+".bak")
	}
	if got, _ := os.ReadFile(target); string(got) != "NEW" {
		t.Errorf("target content = %q, want NEW", got)
	}
	if got, _ := os.ReadFile(backup); string(got) != "OLD" {
		t.Errorf("backup content = %q, want OLD", got)
	}
}

func TestReplaceBinaryNoBackupWhenTargetMissing(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "jentic")
	staged := filepath.Join(dir, "staged")
	if err := os.WriteFile(staged, []byte("NEW"), 0o755); err != nil {
		t.Fatal(err)
	}

	backup, err := ReplaceBinary(target, staged)
	if err != nil {
		t.Fatalf("ReplaceBinary: %v", err)
	}
	if backup != "" {
		t.Errorf("backup = %q, want empty when target did not exist", backup)
	}
	if got, _ := os.ReadFile(target); string(got) != "NEW" {
		t.Errorf("target content = %q, want NEW", got)
	}
}

func TestAuthArgs(t *testing.T) {
	if got := authArgs(""); got != nil {
		t.Errorf("authArgs(\"\") = %v, want nil", got)
	}
	got := authArgs("tok123")
	if len(got) != 2 || got[0] != "-c" || !strings.HasPrefix(got[1], "http.extraheader=Authorization: Basic ") {
		t.Errorf("authArgs(token) = %v, want -c http.extraheader basic auth", got)
	}
}
