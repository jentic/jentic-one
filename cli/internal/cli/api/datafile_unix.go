//go:build unix

package api

import (
	"os"
	"syscall"
)

// openDataFile opens path read-only with O_NONBLOCK so a writer-less FIFO
// never blocks in open(2) before f.Stat() can reject it (#1793).
func openDataFile(path string) (*os.File, error) {
	return os.OpenFile(path, os.O_RDONLY|syscall.O_NONBLOCK, 0) //nolint:gosec // --data-file opens the caller-supplied regular file path by contract.
}

// clearNonblock restores blocking mode on f once f.Stat() has confirmed it is
// a regular file.
func clearNonblock(f *os.File) {
	raw, err := f.SyscallConn()
	if err != nil {
		return
	}
	_ = raw.Control(func(fd uintptr) {
		_ = syscall.SetNonblock(int(fd), false)
	})
}
