// Package atomicfile provides crash-safe atomic file writes for SDK config and
// credential files via a same-directory temporary file, fsync, and rename.
package atomicfile

import (
	"fmt"
	"os"
	"path/filepath"
)

// Options configures an atomic file write.
type Options struct {
	Perm         os.FileMode // mode for a new file (and always, unless PreserveMode)
	PreserveMode bool        // keep an existing target's permission bits
	MkdirPerm    os.FileMode // when non-zero, MkdirAll(dir, MkdirPerm) first
	SyncDir      bool        // fsync the directory after rename (best-effort)
}

// Write writes data to path via a temp file in the same directory, fsyncs it,
// and renames it into place so a reader or crash never observes a torn file.
func Write(path string, data []byte, o Options) error {
	dir := filepath.Dir(path)
	if o.MkdirPerm != 0 {
		if err := os.MkdirAll(dir, o.MkdirPerm); err != nil {
			return fmt.Errorf("create dir %s: %w", dir, err)
		}
	}

	perm := o.Perm
	if o.PreserveMode {
		if info, err := os.Lstat(path); err == nil && info.Mode().IsRegular() {
			perm = info.Mode().Perm()
		}
	}

	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		return fmt.Errorf("create temp file in %s: %w", dir, err)
	}
	tmpName := tmp.Name()
	renamed := false
	defer func() {
		if !renamed {
			_ = os.Remove(tmpName)
		}
	}()

	if err := tmp.Chmod(perm); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("chmod temp file %s: %w", tmpName, err)
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("write temp file %s: %w", tmpName, err)
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("sync temp file %s: %w", tmpName, err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("close temp file %s: %w", tmpName, err)
	}
	if err := os.Rename(tmpName, path); err != nil {
		return fmt.Errorf("rename temp file %s to %s: %w", tmpName, path, err)
	}
	renamed = true
	if o.SyncDir {
		syncDir(dir)
	}
	return nil
}

// syncDir flushes a directory entry so a completed rename is durable where the
// platform supports it. Flushing a directory handle is a POSIX notion and fails
// on Windows, so the sync is best-effort.
func syncDir(dir string) {
	d, err := os.Open(dir) //nolint:gosec // dir is derived from the caller's target path.
	if err != nil {
		return
	}
	_ = d.Sync()
	_ = d.Close()
}
