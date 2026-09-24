//go:build windows

package proc

import (
	"os"

	"golang.org/x/sys/windows"
)

// stillActive is the Win32 STILL_ACTIVE exit code (259): GetExitCodeProcess
// returns it while a process is still running. Any other value means the
// process has exited with that code.
const stillActive = 259

// aliveImpl reports liveness on Windows, where os.Process.Signal accepts only
// Kill and cannot be used as a signal-0 liveness probe (OPS-20). It opens the
// process for exit-code query and checks GetExitCodeProcess: STILL_ACTIVE means
// running. A process that cannot be opened (bad/absent pid) is treated as not
// alive — matching the Unix "signal-0 failed → dead" outcome.
func aliveImpl(proc *os.Process) bool {
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(proc.Pid))
	if err != nil {
		return false
	}
	defer windows.CloseHandle(h) //nolint:errcheck // best-effort close of a query handle.

	var code uint32
	if err := windows.GetExitCodeProcess(h, &code); err != nil {
		return false
	}
	return code == stillActive
}

// terminateImpl stops the process on Windows. There is no portable graceful
// signal (SIGTERM is unsupported), so this uses p.Kill() — TerminateProcess,
// the only stop primitive os.Process exposes on Windows (OPS-20). The stop flow
// treats this as the terminate step; its later Kill escalation is a no-op if the
// process is already gone.
func terminateImpl(proc *os.Process) error {
	return proc.Kill()
}
