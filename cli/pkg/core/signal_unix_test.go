//go:build !windows

package core_test

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"syscall"
	"testing"
	"time"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/pkg/core"
)

const signalHelperEnv = "JENTIC_CORE_SIGNAL_HELPER"

// TestSignalHelperProcess is the child half of TestSecondSIGINTKillsProcess: it
// runs a command through core.Run that, once its context is cancelled, keeps
// blocking without ever observing ctx again — the shape of huh's accessible-mode
// stdin loop (jentic-one#841). It is a no-op in a normal test run.
func TestSignalHelperProcess(t *testing.T) {
	if os.Getenv(signalHelperEnv) != "1" {
		t.Skip("helper process for TestSecondSIGINTKillsProcess")
	}
	root := &cobra.Command{
		Use:           "helper",
		SilenceUsage:  true,
		SilenceErrors: true,
		RunE: func(cmd *cobra.Command, _ []string) error {
			fmt.Println("ready")
			<-cmd.Context().Done()
			fmt.Println("cancelled")
			// Uncancellable from here on: only the default SIGINT disposition
			// can end the process before this sleep does.
			time.Sleep(time.Minute)
			return context.Cause(cmd.Context())
		},
	}
	root.SetArgs([]string{})
	os.Exit(core.Run(root))
}

// TestSecondSIGINTKillsProcess pins the double-Ctrl-C contract end to end: the
// first SIGINT cancels the command context, and a later SIGINT terminates the
// process via the default disposition even though the command never returns.
func TestSecondSIGINTKillsProcess(t *testing.T) {
	if testing.Short() {
		t.Skip("spawns a subprocess")
	}
	cmd := exec.Command(os.Args[0], "-test.run=^TestSignalHelperProcess$")
	cmd.Env = append(os.Environ(), signalHelperEnv+"=1")
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatalf("stdout pipe: %v", err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatalf("start helper: %v", err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill() })

	lines := make(chan string, 4)
	go func() {
		sc := bufio.NewScanner(stdout)
		for sc.Scan() {
			lines <- sc.Text()
		}
		close(lines)
	}()
	waitLine := func(want string) {
		t.Helper()
		deadline := time.After(10 * time.Second)
		for {
			select {
			case l, ok := <-lines:
				if !ok {
					t.Fatalf("helper exited before printing %q", want)
				}
				if l == want {
					return
				}
			case <-deadline:
				t.Fatalf("timed out waiting for helper to print %q", want)
			}
		}
	}

	waitLine("ready")
	if err := cmd.Process.Signal(syscall.SIGINT); err != nil {
		t.Fatalf("first SIGINT: %v", err)
	}
	waitLine("cancelled")

	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()

	// Keep sending SIGINT: the subscription is released asynchronously right
	// after cancellation, so an early repeat may still be absorbed. If the
	// subscription is never released, every repeat is swallowed and this loop
	// times out.
	tick := time.NewTicker(50 * time.Millisecond)
	defer tick.Stop()
	deadline := time.After(10 * time.Second)
	for {
		select {
		case err := <-done:
			var exitErr *exec.ExitError
			if !errors.As(err, &exitErr) {
				t.Fatalf("helper exited with %v, want death by SIGINT", err)
			}
			ws, ok := exitErr.Sys().(syscall.WaitStatus)
			if !ok || !ws.Signaled() || ws.Signal() != syscall.SIGINT {
				t.Fatalf("helper exit status = %v, want killed by SIGINT", exitErr)
			}
			return
		case <-tick.C:
			_ = cmd.Process.Signal(syscall.SIGINT)
		case <-deadline:
			t.Fatal("process survived repeated SIGINT after the first cancelled ctx (signal swallowed)")
		}
	}
}
