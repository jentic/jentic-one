// Package proc holds small filesystem/process helpers shared by the start and
// stop commands: reading a PID file, liveness probing, and waiting for exit.
//
// Liveness (Alive) and graceful stop (Terminate) are split across build tags —
// proc_unix.go uses signal semantics (signal 0 to probe, SIGTERM to stop),
// proc_windows.go uses the Win32 process API (OpenProcess/exit-code probe,
// TerminateProcess) — because os.Process.Signal on native Windows supports ONLY
// Kill and rejects both signal-0 and SIGTERM (OPS-20). Without the split, a
// local (non-Docker) install on Windows would see a live app reported as dead
// (Alive→false clears the PID file) or a failed SIGTERM out of `jenticctl stop`.
package proc

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// FileExists reports whether path exists and is a regular file.
func FileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

// ReadPIDFile reads and parses the PID stored at path.
func ReadPIDFile(path string) (int, error) {
	data, err := os.ReadFile(path) //nolint:gosec // path is a CLI-managed PID file under JENTIC_HOME, not user input.
	if err != nil {
		return 0, err
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil {
		return 0, fmt.Errorf("parse PID in %s: %w", path, err)
	}
	return pid, nil
}

// Alive reports whether the process is currently running. The probe is
// platform-specific (see aliveImpl in proc_unix.go / proc_windows.go): a signal-0
// send on Unix, an OpenProcess + exit-code check on Windows.
func Alive(proc *os.Process) bool {
	return aliveImpl(proc)
}

// Terminate requests a graceful stop of the process, returning any error from
// the platform stop primitive. On Unix it sends SIGTERM (the process can run its
// shutdown handlers); on Windows — where os.Process.Signal accepts only Kill —
// it falls back to a hard TerminateProcess via p.Kill(), since there is no
// portable graceful-signal equivalent (OPS-20). Callers still escalate to Kill
// after a timeout via the existing stop flow.
func Terminate(proc *os.Process) error {
	return terminateImpl(proc)
}

// LivePID reads the PID file at path and reports the recorded process id and
// whether that process is currently running. A missing PID file yields
// (0, false, nil); only a malformed/unreadable file returns an error. A present
// file whose process has exited yields (pid, false, nil) — a stale PID file.
func LivePID(path string) (pid int, alive bool, err error) {
	pid, err = ReadPIDFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return 0, false, nil
		}
		return 0, false, err
	}
	// os.FindProcess never fails on Unix; it returns a usable handle whether or
	// not the process is still alive, so liveness is decided by the platform
	// probe. On Windows FindProcess opens a handle and errors for a dead pid.
	p, ferr := os.FindProcess(pid)
	if ferr != nil {
		return pid, false, nil //nolint:nilerr // a pid with no openable process is simply not alive (stale PID file), not a hard error.
	}
	return pid, Alive(p), nil
}

// WaitForExit polls until the process exits or timeout elapses, returning true
// if it exited.
func WaitForExit(proc *os.Process, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if !Alive(proc) {
			return true
		}
		time.Sleep(100 * time.Millisecond)
	}
	return !Alive(proc)
}
