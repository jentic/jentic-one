//go:build darwin

package api

import (
	"fmt"
	"net"

	"golang.org/x/sys/unix"
)

// peerUID returns the kernel-asserted uid of the process on the other end of
// a unix-domain connection (LOCAL_PEERCRED).
func peerUID(conn *net.UnixConn) (uint32, error) {
	raw, err := conn.SyscallConn()
	if err != nil {
		return 0, fmt.Errorf("raw conn: %w", err)
	}
	var cred *unix.Xucred
	var credErr error
	if err := raw.Control(func(fd uintptr) {
		cred, credErr = unix.GetsockoptXucred(int(fd), unix.SOL_LOCAL, unix.LOCAL_PEERCRED)
	}); err != nil {
		return 0, fmt.Errorf("control: %w", err)
	}
	if credErr != nil {
		return 0, fmt.Errorf("LOCAL_PEERCRED: %w", credErr)
	}
	return cred.Uid, nil
}
