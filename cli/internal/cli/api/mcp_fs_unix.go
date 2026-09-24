//go:build unix

package api

// mcp_fs_unix.go carries the unix-only filesystem primitives the daemon's
// socket/token hygiene needs: the umask dance around the socket bind, and
// file-ownership introspection for the token-file check.

import (
	"os"
	"syscall"
)

// setUmask swaps the process umask, returning the previous one — used to
// bind the unix socket with no world-connectable window.
func setUmask(mask int) int {
	return syscall.Umask(mask)
}

// fileOwnerUID returns the file's owner uid. The bool is false only when the
// stat carries no unix ownership (never on unix).
func fileOwnerUID(info os.FileInfo) (uint32, bool) {
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, false
	}
	return st.Uid, true
}
