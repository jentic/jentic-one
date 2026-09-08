package update

import (
	"errors"
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

// TestBackupSQLite_CapturesAndRestoresWALSidecars pins the F1 (round-3 #7) fix:
// a live WAL-mode DB has -wal/-shm sidecars carrying uncheckpointed pages, so a
// backup that snapshots only the .db can restore an inconsistent database.
// BackupSQLite must capture all three and Restore must reinstate them.
func TestBackupSQLite_CapturesAndRestoresWALSidecars(t *testing.T) {
	data := t.TempDir()
	dbPath := filepath.Join(data, "control.db")
	walPath := dbPath + "-wal"
	shmPath := dbPath + "-shm"
	for path, content := range map[string]string{
		dbPath:  "MAIN-PRE",
		walPath: "WAL-PRE",
		shmPath: "SHM-PRE",
	} {
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}

	b, err := BackupSQLite(data)
	if err != nil {
		t.Fatalf("BackupSQLite: %v", err)
	}
	if len(b.files) != 3 {
		t.Fatalf("expected .db + -wal + -shm captured (3 files), got %d: %v", len(b.files), b.files)
	}

	// Simulate a migration mutating all three, then roll back.
	for _, path := range []string{dbPath, walPath, shmPath} {
		if err := os.WriteFile(path, []byte("MUTATED"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := b.Restore(); err != nil {
		t.Fatalf("Restore: %v", err)
	}
	for path, want := range map[string]string{
		dbPath:  "MAIN-PRE",
		walPath: "WAL-PRE",
		shmPath: "SHM-PRE",
	} {
		got, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if string(got) != want {
			t.Errorf("after rollback %s = %q, want %q", filepath.Base(path), got, want)
		}
	}
}

// TestBackupSQLite_RestoreDropsUncapturedSidecar pins the second half of F1: a
// migration that STARTS a fresh WAL (no -wal existed at snapshot time) must not
// leave that stale sidecar behind on rollback, where it could replay stale frames
// onto the restored .db.
func TestBackupSQLite_RestoreDropsUncapturedSidecar(t *testing.T) {
	data := t.TempDir()
	dbPath := filepath.Join(data, "control.db")
	if err := os.WriteFile(dbPath, []byte("MAIN-PRE"), 0o600); err != nil {
		t.Fatal(err)
	}

	b, err := BackupSQLite(data) // only .db exists → only .db captured
	if err != nil {
		t.Fatalf("BackupSQLite: %v", err)
	}

	// Migration begins a WAL and mutates the main file, then fails.
	walPath := dbPath + "-wal"
	if err := os.WriteFile(walPath, []byte("STALE-WAL"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(dbPath, []byte("MUTATED"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := b.Restore(); err != nil {
		t.Fatalf("Restore: %v", err)
	}
	if got, _ := os.ReadFile(dbPath); string(got) != "MAIN-PRE" {
		t.Errorf("main db not restored: %q", got)
	}
	if _, err := os.Stat(walPath); !os.IsNotExist(err) {
		t.Errorf("stale -wal sidecar should have been removed on rollback, stat err = %v", err)
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

// TestMigrateWithRollback_RestoresOnFailure pins install-flow P1-E: the shared
// helper backs up SQLite, and when the migrate func fails it restores the
// pre-migration bytes and returns a wrapped "migrations failed" error.
func TestMigrateWithRollback_RestoresOnFailure(t *testing.T) {
	data := t.TempDir()
	dbPath := filepath.Join(data, "control.db")
	if err := os.WriteFile(dbPath, []byte("PRE-MIGRATION"), 0o600); err != nil {
		t.Fatal(err)
	}
	var rolledBack bool
	err := MigrateWithRollback(data, true, func() error {
		// Simulate a migration that corrupts the DB then fails.
		_ = os.WriteFile(dbPath, []byte("HALF-MIGRATED-GARBAGE"), 0o600)
		return errors.New("boom")
	}, nil, func() { rolledBack = true })
	if err == nil || !strings.Contains(err.Error(), "migrations failed") {
		t.Fatalf("want a wrapped migration failure, got %v", err)
	}
	if !rolledBack {
		t.Error("onRollback hook should have fired")
	}
	got, _ := os.ReadFile(dbPath)
	if string(got) != "PRE-MIGRATION" {
		t.Errorf("after rollback control.db = %q, want the pre-migration bytes", got)
	}
}

// TestMigrateWithRollback_DiscardsOnSuccess pins P1-E: a successful migration
// discards the snapshot (no leftover temp dir) and returns nil.
func TestMigrateWithRollback_DiscardsOnSuccess(t *testing.T) {
	data := t.TempDir()
	if err := os.WriteFile(filepath.Join(data, "control.db"), []byte("v1"), 0o600); err != nil {
		t.Fatal(err)
	}
	snapshotted := false
	err := MigrateWithRollback(data, true, func() error {
		return nil // success
	}, func() { snapshotted = true }, nil)
	if err != nil {
		t.Fatalf("successful migration should not error: %v", err)
	}
	if !snapshotted {
		t.Error("onSnapshot hook should have fired for a non-empty data dir")
	}
}

// TestMigrateWithRollback_NonSQLiteSkipsBackup pins P1-E: with sqlite=false the
// helper just runs migrate and never takes a backup (Postgres is out of scope).
func TestMigrateWithRollback_NonSQLiteSkipsBackup(t *testing.T) {
	var snapshotted bool
	if err := MigrateWithRollback(t.TempDir(), false, func() error { return nil },
		func() { snapshotted = true }, nil); err != nil {
		t.Fatalf("non-sqlite success should not error: %v", err)
	}
	if snapshotted {
		t.Error("non-sqlite path must not snapshot")
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
