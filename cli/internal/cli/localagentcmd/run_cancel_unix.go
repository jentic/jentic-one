//go:build !windows

package localagentcmd

import (
	"os"
	"syscall"
)

// terminateForCancel stops a launched agent session's process on ctx-cancel.
// On Unix this is a catchable SIGTERM: exec.CommandContext's default cancel
// sends SIGKILL to the DIRECT child, but for a confined launch that child is
// `sudo`, which cannot relay an uncatchable SIGKILL — so the agent process tree
// beneath it would be orphaned. SIGTERM lets sudo (and the shells below it)
// forward the signal so the whole tree unwinds; wireGracefulCancel's WaitDelay
// is the SIGKILL backstop. See run.go for the full rationale.
func terminateForCancel(proc *os.Process) error {
	return proc.Signal(syscall.SIGTERM)
}
