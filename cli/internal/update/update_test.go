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

func TestBackupSQLite_RestoresAfterFailedMigration(t *testing.T) {
	data := t.TempDir()
	dbPath := filepath.Join(data, "control.db")
	if err := os.WriteFile(dbPath, []byte("PRE-MIGRATION"), 0o600); err != nil {
		t.Fatal(err)
	}
	// A non-.db sibling must NOT be captured (only the SQLite files are ours).
	if err := os.WriteFile(filepath.Join(data, "notes.txt"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}

	b, err := BackupSQLite(data)
	if err != nil {
		t.Fatalf("BackupSQLite: %v", err)
	}
	if b.Empty() {
		t.Fatal("backup should have captured control.db")
	}

	// Simulate a migration that corrupts the DB, then roll back.
	if err := os.WriteFile(dbPath, []byte("HALF-MIGRATED-GARBAGE"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := b.Restore(); err != nil {
		t.Fatalf("Restore: %v", err)
	}
	got, err := os.ReadFile(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "PRE-MIGRATION" {
		t.Errorf("after rollback control.db = %q, want the pre-migration bytes", got)
	}
}

func TestBackupSQLite_DiscardRemovesSnapshot(t *testing.T) {
	data := t.TempDir()
	if err := os.WriteFile(filepath.Join(data, "x.db"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	b, err := BackupSQLite(data)
	if err != nil {
		t.Fatalf("BackupSQLite: %v", err)
	}
	if b.dir == "" {
		t.Fatal("expected a snapshot dir")
	}
	b.Discard()
	if _, err := os.Stat(b.dir); !os.IsNotExist(err) {
		t.Errorf("snapshot dir should be gone after Discard, stat err = %v", err)
	}
}

func TestBackupSQLite_EmptyWhenNoDBFiles(t *testing.T) {
	// A fresh install (data dir absent) yields a harmless empty backup with
	// nothing to roll back to — the migration will create the files.
	b, err := BackupSQLite(filepath.Join(t.TempDir(), "does-not-exist"))
	if err != nil {
		t.Fatalf("BackupSQLite on missing dir: %v", err)
	}
	if !b.Empty() {
		t.Error("backup of a missing data dir should be Empty()")
	}
	// Restore/Discard on an empty backup must be safe no-ops.
	if err := b.Restore(); err != nil {
		t.Errorf("Restore on empty backup: %v", err)
	}
	b.Discard()
}
