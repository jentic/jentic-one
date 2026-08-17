package auth

import (
	"fmt"
	"os"
	"path/filepath"
)

// writeFileAtomic writes data to path via a temp file in the SAME directory,
// fsync'd, then renamed into place — so a crash / ENOSPC between truncate and
// write can never leave a partial or empty credential file (F6, review round-3
// #7). `cli-conventions` §"State directory (XDG)" requires token/secret/key
// files be "0600, written atomically"; the plain os.WriteFile these callers used
// is O_TRUNC-then-write, which is not atomic. All credential files share the
// 0600 mode, so it is fixed here rather than a parameter.
//
// It deliberately keeps the callers' existing no-lock, last-writer-wins
// concurrency semantics (rename is atomic on POSIX and Windows-for-same-volume):
// two concurrent writers still race, but neither can observe a torn file — the
// loser's rename simply wins or loses wholesale. The temp file is created in the
// destination directory (not $TMPDIR) so the rename stays on one filesystem.
func writeFileAtomic(path string, data []byte) error {
	const perm os.FileMode = 0o600
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		return fmt.Errorf("create temp file in %s: %w", dir, err)
	}
	tmpName := tmp.Name()
	// Best-effort cleanup if we bail before the rename; a successful rename makes
	// this a no-op (the temp name no longer exists).
	defer func() { _ = os.Remove(tmpName) }()

	if err := tmp.Chmod(perm); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("chmod temp file: %w", err)
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("write temp file: %w", err)
	}
	// fsync the data before the rename so the rename can't land ahead of the
	// bytes on a crash (the config writer's sibling gap this closes too).
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("sync temp file: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("close temp file: %w", err)
	}
	if err := os.Rename(tmpName, path); err != nil {
		return fmt.Errorf("rename temp file into place: %w", err)
	}
	return nil
}
