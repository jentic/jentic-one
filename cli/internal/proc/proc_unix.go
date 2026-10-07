//go:build !windows

package proc

import (
	"os"
	"syscall"
)

// aliveImpl probes liveness by sending signal 0 (the POSIX "does this process
// exist and can I signal it?" no-op): a nil error means the process is alive.
// This is the classic Unix liveness check and works for any process the caller
// may signal.
func aliveImpl(proc *os.Process) bool {
	return proc.Signal(syscall.Signal(0)) == nil
}

// terminateImpl asks the process to stop gracefully with SIGTERM so it can run
// its shutdown handlers; the stop flow escalates to Kill (SIGKILL) after a
// timeout. On Unix this is the natural graceful-stop signal.
func terminateImpl(proc *os.Process) error {
	return proc.Signal(syscall.SIGTERM)
}
