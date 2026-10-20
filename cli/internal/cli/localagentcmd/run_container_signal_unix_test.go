//go:build unix

package localagentcmd

import (
	"context"
	"syscall"
	"testing"
	"time"
)

// TestContainerSessionContextCancelsOnHangup pins that a closed terminal
// (SIGHUP) cancels a container session's context, so its deferred teardown
// runs, instead of SIGHUP's default action killing the process.
func TestContainerSessionContextCancelsOnHangup(t *testing.T) {
	ctx, stop := containerSessionContext(context.Background())
	defer stop()

	if err := syscall.Kill(syscall.Getpid(), syscall.SIGHUP); err != nil {
		t.Fatalf("send SIGHUP: %v", err)
	}
	select {
	case <-ctx.Done():
	case <-time.After(5 * time.Second):
		t.Fatal("SIGHUP did not cancel the container session context")
	}
}
