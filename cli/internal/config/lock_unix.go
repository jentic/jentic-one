//go:build !windows

package config

import (
	"os"
	"syscall"
)

// lockFile takes an exclusive (LOCK_EX) advisory flock on f. flock is per-open-file
// and released automatically when the fd is closed or the process dies, giving the
// crash-safety lockConfig relies on.
func lockFile(f *os.File) error {
	return syscall.Flock(int(f.Fd()), syscall.LOCK_EX)
}

// unlockFile releases the advisory flock held on f (LOCK_UN). Closing the fd would
// release it too; unlocking explicitly keeps the release ordering obvious.
func unlockFile(f *os.File) error {
	return syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
}
