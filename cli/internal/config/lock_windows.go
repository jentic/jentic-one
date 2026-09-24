//go:build windows

package config

import (
	"os"

	"golang.org/x/sys/windows"
)

// lockFile takes an exclusive, blocking advisory lock on f via LockFileEx — the
// Windows analogue of flock(LOCK_EX). We lock a single byte at offset 0 (the
// conventional whole-file advisory lock: the byte need not exist). The lock is
// tied to the file handle and released when the handle closes or the process
// dies, matching the Unix crash-safety lockConfig relies on.
func lockFile(f *os.File) error {
	// LOCKFILE_EXCLUSIVE_LOCK => exclusive; no LOCKFILE_FAIL_IMMEDIATELY => block
	// until acquired, mirroring flock(LOCK_EX).
	ol := new(windows.Overlapped)
	return windows.LockFileEx(
		windows.Handle(f.Fd()),
		windows.LOCKFILE_EXCLUSIVE_LOCK,
		0,    // reserved
		1, 0, // lock 1 byte (low, high)
		ol,
	)
}

// unlockFile releases the LockFileEx lock held on f. Closing the handle would
// release it too; unlocking explicitly keeps the release ordering obvious.
func unlockFile(f *os.File) error {
	ol := new(windows.Overlapped)
	return windows.UnlockFileEx(
		windows.Handle(f.Fd()),
		0,    // reserved
		1, 0, // unlock the same 1 byte (low, high)
		ol,
	)
}
