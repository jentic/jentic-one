//go:build windows

package api

// mcp_fs_windows.go: the unix filesystem primitives are no-ops on windows —
// the daemon's unix-socket mode fails closed there anyway (mcp_peercred_other.go),
// and token-file ownership has no unix uid to assert.

import "os"

// setUmask is a no-op on windows (no process umask).
func setUmask(int) int { return 0 }

// fileOwnerUID reports no unix ownership on windows.
func fileOwnerUID(os.FileInfo) (uint32, bool) { return 0, false }
