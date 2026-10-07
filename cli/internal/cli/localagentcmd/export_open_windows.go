//go:build windows

package localagentcmd

import "os"

// exportOpenFlags: Windows has no local-agent launch path (no sudo chain), so
// the export never writes into another account's home there.
const exportOpenFlags = 0

// multiplyLinked is not tracked on Windows.
func multiplyLinked(os.FileInfo) bool { return false }
