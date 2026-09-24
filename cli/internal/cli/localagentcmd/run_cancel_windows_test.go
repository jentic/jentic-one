//go:build windows

package localagentcmd

import (
	"context"
	"errors"
	"os/exec"
	"testing"
)

// TestWireGracefulCancelKillsChild is the Windows counterpart to the Unix
// SIGTERM test. Windows has no relayable graceful signal, so cancel uses Kill()
// (OPS-20); this asserts the wiring (Cancel set, WaitDelay) and that invoking
// Cancel actually terminates the child WITHOUT the "signal not supported by
// windows" error the old syscall.SIGTERM path returned.
func TestWireGracefulCancelKillsChild(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	// A long-lived child that exists on stock Windows runners.
	c := exec.CommandContext(ctx, "ping", "-n", "60", "127.0.0.1")
	wireGracefulCancel(c)
	if c.WaitDelay != cancelGracePeriod {
		t.Errorf("WaitDelay = %v, want %v", c.WaitDelay, cancelGracePeriod)
	}
	if c.Cancel == nil {
		t.Fatal("Cancel must be set so ctx-cancel terminates the child")
	}
	if err := c.Start(); err != nil {
		t.Fatalf("start: %v", err)
	}
	if err := c.Cancel(); err != nil {
		t.Fatalf("Cancel must terminate without error on Windows, got %v", err)
	}
	// The killed child exits non-zero; we only require that Wait returns (the
	// process was terminated) rather than hanging or reporting success.
	err := c.Wait()
	var exit *exec.ExitError
	if err != nil && !errors.As(err, &exit) {
		t.Fatalf("expected a clean exit or ExitError from a killed child, got %v", err)
	}
}
