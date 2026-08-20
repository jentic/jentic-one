package proc

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

func TestFileExists(t *testing.T) {
	dir := t.TempDir()
	f := filepath.Join(dir, "f.txt")
	if FileExists(f) {
		t.Errorf("missing file reported as existing")
	}
	if err := os.WriteFile(f, []byte("x"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	if !FileExists(f) {
		t.Errorf("existing file reported as missing")
	}
	if FileExists(dir) {
		t.Errorf("directory should not count as a regular file")
	}
}

func TestReadPIDFile(t *testing.T) {
	dir := t.TempDir()

	if _, err := ReadPIDFile(filepath.Join(dir, "none")); err == nil {
		t.Errorf("expected error for missing PID file")
	}

	good := filepath.Join(dir, "good.pid")
	if err := os.WriteFile(good, []byte(" 4321\n"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	pid, err := ReadPIDFile(good)
	if err != nil {
		t.Fatalf("ReadPIDFile: %v", err)
	}
	if pid != 4321 {
		t.Errorf("pid = %d, want 4321", pid)
	}

	bad := filepath.Join(dir, "bad.pid")
	if err := os.WriteFile(bad, []byte("not-a-number"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	if _, err := ReadPIDFile(bad); err == nil {
		t.Errorf("expected parse error for non-numeric PID")
	}
}

func TestAliveAndWaitForExit(t *testing.T) {
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Skipf("cannot start sleep: %v", err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _, _ = cmd.Process.Wait() })

	if !Alive(cmd.Process) {
		t.Fatalf("running process should be alive")
	}

	// Kill it and confirm WaitForExit observes the exit. Reap in the background
	// so the process leaves the OS process table.
	_ = cmd.Process.Kill()
	go func() { _, _ = cmd.Process.Wait() }()

	if !WaitForExit(cmd.Process, 3*time.Second) {
		t.Fatalf("WaitForExit should report exit after kill")
	}
}

// TestTerminateStopsProcess pins OPS-20: Terminate is the platform graceful-stop
// primitive (SIGTERM on Unix, TerminateProcess/Kill on Windows) and must
// actually stop a running process on every OS — the exact behavior `jenticctl
// stop` relies on. We start a long sleeper, Terminate it, and require it to be
// observed gone. Running on windows-latest CI (OPS-21) is what makes this catch
// the Signal(SIGTERM)-unsupported-on-Windows regression rather than only Linux.
func TestTerminateStopsProcess(t *testing.T) {
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Skipf("cannot start sleep: %v", err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _, _ = cmd.Process.Wait() })

	if err := Terminate(cmd.Process); err != nil {
		t.Fatalf("Terminate returned error (must be supported on every OS, OPS-20): %v", err)
	}
	go func() { _, _ = cmd.Process.Wait() }()
	if !WaitForExit(cmd.Process, 3*time.Second) {
		t.Fatalf("process should exit after Terminate")
	}
}

// TestLivePID exercises the stale-vs-live PID-file distinction the stop flow
// keys off. A missing file is (0,false,nil); a file pointing at a running
// process is (pid,true,nil); a file pointing at a dead pid is (pid,false,nil) —
// never an error on any OS.
func TestLivePID(t *testing.T) {
	dir := t.TempDir()

	// Missing file → (0,false,nil).
	if pid, alive, err := LivePID(filepath.Join(dir, "none.pid")); pid != 0 || alive || err != nil {
		t.Errorf("missing PID file: got (%d,%v,%v), want (0,false,nil)", pid, alive, err)
	}

	// Live process → alive true.
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Skipf("cannot start sleep: %v", err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _, _ = cmd.Process.Wait() })
	live := filepath.Join(dir, "live.pid")
	if err := os.WriteFile(live, []byte(strconv.Itoa(cmd.Process.Pid)), 0o600); err != nil {
		t.Fatal(err)
	}
	if pid, alive, err := LivePID(live); pid != cmd.Process.Pid || !alive || err != nil {
		t.Errorf("live PID file: got (%d,%v,%v), want (%d,true,nil)", pid, alive, err, cmd.Process.Pid)
	}

	// Dead process → alive false, no error (stale PID file).
	_ = cmd.Process.Kill()
	_, _ = cmd.Process.Wait()
	if pid, alive, err := LivePID(live); pid != cmd.Process.Pid || alive || err != nil {
		t.Errorf("stale PID file: got (%d,%v,%v), want (%d,false,nil)", pid, alive, err, cmd.Process.Pid)
	}
}
