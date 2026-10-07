//go:build !windows

package localagentcmd

import (
	"os"
	"syscall"
)

// exportOpenFlags are added to every export write into an agent home. The
// home is agent-writable, so the final path component may be something the
// agent placed there: O_NOFOLLOW makes a symlink fail the open (ELOOP) instead
// of redirecting the write, and O_NONBLOCK keeps a FIFO from blocking it (the
// open fails with ENXIO, or the regular-file check below rejects it).
const exportOpenFlags = syscall.O_NOFOLLOW | syscall.O_NONBLOCK

// multiplyLinked reports whether a file has more than one hard link.
func multiplyLinked(info os.FileInfo) bool {
	st, ok := info.Sys().(*syscall.Stat_t)
	return ok && st.Nlink > 1
}
