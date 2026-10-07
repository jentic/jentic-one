//go:build unix

package api

// mcp_activation_unix_test.go pins socket-activation fd adoption against a
// real inherited descriptor. Unix-tagged: fd donation is a systemd/launchd
// affair, and the dup below is raw syscall territory.

import (
	"net"
	"syscall"
	"testing"
)

// TestListenerFromFD proves inherited-fd adoption against a real socket.
//
// The donated fd must be a dedicated dup that listenerFromFD exclusively
// owns. An earlier version donated f.Fd() while keeping f alive — two owners
// of one fd number. listenerFromFD closes its side, the number gets recycled,
// and f's GC finalizer later closes whatever unrelated file or socket now
// holds that number: that stray close surfaced as the cross-test CI flakes
// in TestRegister_FreshMachine_OneCommand (EBADF on an atomic key-write temp
// file) and TestMCPSession_AccessLoopDeniedToApprovedRetry (a killed httptest
// connection turning a normal pending filing into a soft error).
func TestListenerFromFD(t *testing.T) {
	ln, err := net.Listen("unix", shortSocketPath(t))
	if err != nil {
		t.Fatalf("bind: %v", err)
	}
	t.Cleanup(func() { _ = ln.Close() })
	f, err := ln.(*net.UnixListener).File()
	if err != nil {
		t.Fatalf("File: %v", err)
	}
	defer func() { _ = f.Close() }()
	donated, err := syscall.Dup(int(f.Fd()))
	if err != nil {
		t.Fatalf("dup: %v", err)
	}
	adopted, err := listenerFromFD(uintptr(donated), "test socket")
	if err != nil {
		t.Fatalf("listenerFromFD: %v", err)
	}
	_ = adopted.Close()
}
