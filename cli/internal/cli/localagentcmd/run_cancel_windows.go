//go:build windows

package localagentcmd

import "os"

// terminateForCancel stops a launched agent session's process on ctx-cancel.
// Windows has no relayable graceful termination signal (os.Process.Signal
// accepts only Kill; SIGTERM is unsupported — OPS-20), so this uses Kill()
// (TerminateProcess). The confined-launch/sudo relay concern that motivates
// SIGTERM on Unix does not apply: there is no sudo chain in the Windows launch
// path. wireGracefulCancel's WaitDelay still bounds the wait.
func terminateForCancel(proc *os.Process) error {
	return proc.Kill()
}
