// Package update backs `jenticctl update`: it inspects what is installed (via the
// manifest and build-time metadata) and compares its version against the latest
// release tag to report whether a newer build is available, then fetches and
// swaps in the rebuilt binaries.
package update

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
)

// authArgs returns the `git -c http.extraheader=...` prefix carrying a Basic
// auth header for token, or nil when no token is set. It mirrors the auth
// scheme used by tools/install.sh so private repositories resolve.
func authArgs(token string) []string {
	if token == "" {
		return nil
	}
	basic := base64.StdEncoding.EncodeToString([]byte("x-access-token:" + token))
	return []string{"-c", "http.extraheader=Authorization: Basic " + basic}
}

// FetchInstaller downloads tools/install.sh for ref from repo's raw content,
// authenticating with a bearer token when one is given (required while the repo
// is private). The script is returned verbatim so the caller can run it.
func FetchInstaller(ctx context.Context, repo, ref, token string) ([]byte, error) {
	url := fmt.Sprintf("https://raw.githubusercontent.com/%s/%s/tools/install.sh", repo, ref)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("download installer: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("download installer: unexpected status %s (check the ref %q and, for a private repo, GITHUB_TOKEN)", resp.Status, ref)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 1<<20))
}

// ReplaceBinary atomically swaps the file at target with the freshly built
// binary at staged, after backing up the current target to "<target>.bak".
// staged is copied into target's directory first so the final rename is on the
// same filesystem (atomic), avoiding a cross-device rename error when the build
// was staged under a temp dir. Returns the backup path (empty if target did not
// previously exist).
func ReplaceBinary(target, staged string) (string, error) {
	dir := filepath.Dir(target)
	if err := os.MkdirAll(dir, 0o755); err != nil { //nolint:gosec // bin dir is conventionally world-readable.
		return "", err
	}

	tmp := filepath.Join(dir, ".jentic.new")
	if err := copyFile(staged, tmp, 0o755); err != nil {
		return "", fmt.Errorf("stage new binary: %w", err)
	}

	var backup string
	if _, err := os.Stat(target); err == nil {
		backup = target + ".bak"
		if err := copyFile(target, backup, 0o755); err != nil {
			_ = os.Remove(tmp)
			return "", fmt.Errorf("back up current binary: %w", err)
		}
	}

	// Rename over the (possibly running) target: on Linux/macOS this replaces the
	// directory entry while the running process keeps its old inode.
	if err := os.Rename(tmp, target); err != nil {
		_ = os.Remove(tmp)
		return backup, fmt.Errorf("install new binary: %w", err)
	}
	return backup, nil
}

// RestoreBinary copies a .bak backup produced by ReplaceBinary back over the
// target, undoing a swap. Used to roll back a multi-binary update when a later
// binary in the set fails to swap, so a half-updated pair can't persist.
func RestoreBinary(target, backup string) error {
	return copyFile(backup, target, 0o755)
}

func copyFile(src, dst string, mode os.FileMode) error {
	in, err := os.Open(src) //nolint:gosec // paths are CLI-internal (staged build artifact / install location).
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, mode) //nolint:gosec // see above.
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		return err
	}
	if err := out.Chmod(mode); err != nil {
		_ = out.Close()
		return err
	}
	return out.Close()
}

// SQLiteBackup is a snapshot of the local SQLite database files taken before a
// forward-only migration, so a failed migration can be rolled back to a
// known-good state (CLI-V2 Phase 6 lifecycle hardening: "DB backup before
// migration, rollback path"). It only covers the SQLite backend — the files
// live under ~/.jentic/data and the CLI owns them outright. The Postgres/Docker
// backend keeps the documented `pg_dump` warning: an in-CLI dump/restore of an
// operator-managed database is out of scope and would give false assurance.
type SQLiteBackup struct {
	// files maps each live *.db path to its sibling snapshot copy.
	files map[string]string
	dir   string
}

// BackupSQLite snapshots every *.db file directly under dataDir into a temp
// directory, returning a handle that can Restore them if the migration fails or
// be Discarded on success. A dataDir with no *.db files (a fresh install whose
// migration will create them) yields an empty, harmless backup. The snapshot is
// a plain file copy: SQLite is quiescent here because the local app/broker are
// bounced around the migration, so no writer is mid-transaction.
func BackupSQLite(dataDir string) (*SQLiteBackup, error) {
	entries, err := os.ReadDir(dataDir)
	if err != nil {
		if os.IsNotExist(err) {
			return &SQLiteBackup{files: map[string]string{}}, nil
		}
		return nil, fmt.Errorf("read data dir: %w", err)
	}
	tmp, err := os.MkdirTemp("", "jentic-db-backup-*")
	if err != nil {
		return nil, fmt.Errorf("create backup dir: %w", err)
	}
	b := &SQLiteBackup{files: map[string]string{}, dir: tmp}
	for _, e := range entries {
		if e.IsDir() || filepath.Ext(e.Name()) != ".db" {
			continue
		}
		src := filepath.Join(dataDir, e.Name())
		dst := filepath.Join(tmp, e.Name())
		if err := copyFile(src, dst, 0o600); err != nil {
			_ = os.RemoveAll(tmp) // don't leak a partial backup dir
			return nil, fmt.Errorf("back up %s: %w", e.Name(), err)
		}
		b.files[src] = dst
	}
	return b, nil
}

// Restore copies each snapshot back over its live file, undoing a failed
// migration. It restores as many files as it can and joins any errors so one
// bad file does not silently strand the rest half-rolled-back.
func (b *SQLiteBackup) Restore() error {
	if b == nil {
		return nil
	}
	var errs []error
	for live, snap := range b.files {
		if err := copyFile(snap, live, 0o600); err != nil {
			errs = append(errs, fmt.Errorf("restore %s: %w", filepath.Base(live), err))
		}
	}
	return errors.Join(errs...)
}

// Discard removes the snapshot directory once it is no longer needed (a
// successful migration). Best-effort: a leftover temp dir is harmless.
func (b *SQLiteBackup) Discard() {
	if b == nil || b.dir == "" {
		return
	}
	_ = os.RemoveAll(b.dir)
}

// Empty reports whether the backup captured no files (nothing to roll back to).
func (b *SQLiteBackup) Empty() bool { return b == nil || len(b.files) == 0 }

// MigrateWithRollback runs a forward-only SQLite migration inside a
// snapshot/restore net: it backs up the *.db files under dataDir, runs migrate,
// and on failure rolls the database back to its pre-migration bytes before
// returning the (wrapped) migration error. On success the snapshot is discarded.
// It is the single source of truth shared by both first-`install` and `update`
// so the two can never drift (install-flow review P1-E). onSnapshot/onRollback
// are optional progress hooks (nil-safe) so callers can print their own status
// lines. For a non-SQLite backend it just runs migrate (Postgres backup/restore
// is out of scope — see the SQLiteBackup doc), so callers pass sqlite=false.
func MigrateWithRollback(dataDir string, sqlite bool, migrate func() error, onSnapshot, onRollback func()) error {
	var backup *SQLiteBackup
	if sqlite {
		b, err := BackupSQLite(dataDir)
		if err != nil {
			return fmt.Errorf("back up database before migration: %w", err)
		}
		backup = b
		if !backup.Empty() && onSnapshot != nil {
			onSnapshot()
		}
	}
	if err := migrate(); err != nil {
		if backup != nil && !backup.Empty() {
			if rerr := backup.Restore(); rerr != nil {
				//nolint:errorlint // primary %w is the migration failure; rerr is contextual detail (fmt.Errorf allows one %w).
				return fmt.Errorf("migrations failed: %w; ALSO failed to roll back the database: %v — restore from a backup", err, rerr)
			}
			if onRollback != nil {
				onRollback()
			}
		}
		return fmt.Errorf("migrations failed: %w", err)
	}
	if backup != nil {
		backup.Discard()
	}
	return nil
}
