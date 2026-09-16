//go:build unix

package api

// stdinbody_unix_test.go pins the #1354 no-hang contract against the real
// failure shape: an idle socketpair stdin — open, dataless, never EOF —
// exactly what a backgrounded process or an agent/harness hands its child.
// The portable /dev/null test in stdinbody_test.go cannot pin this (a char
// device EOFs instantly, so even an unguarded drain returns); only a
// genuinely blocking fd proves the resolvers decline to read. Unix-tagged:
// socketpair(2) is raw syscall territory.

import (
	"io"
	"os"
	"syscall"
	"testing"
	"time"
)

// idleSocketStdin returns the read side of a socketpair whose peer is held
// open with no bytes written: a read on it blocks until the peer closes.
func idleSocketStdin(t *testing.T) *os.File {
	t.Helper()
	fds, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_STREAM, 0)
	if err != nil {
		t.Fatalf("socketpair: %v", err)
	}
	peer := os.NewFile(uintptr(fds[1]), "stdin-peer")
	// Closing the peer in cleanup unblocks (EOF) any reader goroutine a
	// regression leaves stranded, so the process still exits cleanly.
	t.Cleanup(func() { _ = peer.Close() })
	f := os.NewFile(uintptr(fds[0]), "stdin")
	t.Cleanup(func() { _ = f.Close() })
	return f
}

func TestStdinHasPipedBody_IdleSocketIsFalse(t *testing.T) {
	if stdinHasPipedBody(idleSocketStdin(t)) {
		t.Error("an idle socket stdin must not classify as a piped body")
	}
}

// TestResolveAPIBody_BlockingSocketStdinDoesNotHang pins #1354 end to end for
// `jentic api`: with a body-less invocation and a blocking, never-EOF socket
// stdin, body resolution must return no body immediately instead of draining
// stdin. A regression to an unguarded read (e.g. keying on !IsTerminal alone)
// blocks here and fails via the timeout guard.
func TestResolveAPIBody_BlockingSocketStdinDoesNotHang(t *testing.T) {
	sock := idleSocketStdin(t)

	var body io.Reader
	var rerr error
	withStdin(t, sock, func() {
		runWithTimeout(t, 5*time.Second, "resolveAPIBody with blocking socket stdin", func() {
			body, rerr = resolveAPIBody(&apiOptions{})
		})
	})
	if rerr != nil {
		t.Fatalf("resolveAPIBody: %v", rerr)
	}
	if body != nil {
		t.Errorf("body = %v, want nil (an idle socket stdin must not be read as a body)", body)
	}
}

// TestResolveExecuteBody_BlockingSocketStdinDoesNotHang pins the same
// contract for `jentic execute`, the command #1354 reported.
func TestResolveExecuteBody_BlockingSocketStdinDoesNotHang(t *testing.T) {
	sock := idleSocketStdin(t)

	var body io.Reader
	var ct string
	var rerr error
	withStdin(t, sock, func() {
		runWithTimeout(t, 5*time.Second, "resolveExecuteBody with blocking socket stdin", func() {
			body, ct, rerr = resolveExecuteBody(&executeOptions{})
		})
	})
	if rerr != nil {
		t.Fatalf("resolveExecuteBody: %v", rerr)
	}
	if ct != "" {
		t.Errorf("multipart content type = %q, want empty", ct)
	}
	if body != nil {
		t.Errorf("body = %v, want nil (an idle socket stdin must not be read as a body)", body)
	}
}
