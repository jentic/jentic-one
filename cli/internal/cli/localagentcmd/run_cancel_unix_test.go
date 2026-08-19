//go:build !windows

package localagentcmd

import (
	"context"
	"errors"
	"os/exec"
	"syscall"
	"testing"
)

// TestWireGracefulCancelSendsSIGTERM proves cancellation terminates the child with
// a catchable SIGTERM (which sudo can relay down the launch chain) rather than the
// exec default SIGKILL to the direct child, and sets a WaitDelay SIGKILL backstop.
// Unix-only: it asserts POSIX signal semantics; the Windows Kill() contract is in
// run_cancel_windows_test.go.
func TestWireGracefulCancelSendsSIGTERM(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	// A long-lived child so the process is alive when we exercise Cancel.
	c := exec.CommandContext(ctx, "sleep", "60")
	wireGracefulCancel(c)
	if c.WaitDelay != cancelGracePeriod {
		t.Errorf("WaitDelay = %v, want %v", c.WaitDelay, cancelGracePeriod)
	}
	if c.Cancel == nil {
		t.Fatal("Cancel must be set so ctx-cancel sends SIGTERM, not SIGKILL")
	}
	if err := c.Start(); err != nil {
		t.Fatalf("start: %v", err)
	}
	if err := c.Cancel(); err != nil {
		t.Fatalf("Cancel: %v", err)
	}
	err := c.Wait()
	var exit *exec.ExitError
	if !errors.As(err, &exit) {
		t.Fatalf("expected ExitError from a signalled child, got %v", err)
	}
	ws, ok := exit.Sys().(syscall.WaitStatus)
	if !ok || !ws.Signaled() || ws.Signal() != syscall.SIGTERM {
		t.Errorf("child should have been terminated by SIGTERM, got %v", exit)
	}
}
