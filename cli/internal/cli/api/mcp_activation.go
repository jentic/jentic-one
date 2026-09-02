package api

// mcp_activation.go inherits socket-activation listeners so the daemon
// spawns on first connection and can idle-exit (deploy/mcp-daemon/):
//
//   - systemd: the sd_listen_fds protocol — LISTEN_PID/LISTEN_FDS in the
//     environment, the socket on fd 3 (SD_LISTEN_FDS_START). Pure Go, no
//     libsystemd.
//   - launchd: inetdCompatibility "wait" mode — launchd hands the LISTENING
//     socket to the process as fd 0 and resumes listening itself when the
//     process exits. Chosen over launch_activate_socket, which needs cgo.

import (
	"fmt"
	"net"
	"os"
	"strconv"
)

// listenFDsStart is systemd's SD_LISTEN_FDS_START: inherited sockets begin
// at fd 3.
const listenFDsStart = 3

// systemdListener returns the socket-activation listener when systemd
// provided one, nil when the environment carries no (valid, for-this-pid)
// activation vars, and an error for a malformed or multi-socket handoff.
func systemdListener(pid int, getenv func(string) string) (net.Listener, error) {
	fdsRaw := getenv("LISTEN_FDS")
	if fdsRaw == "" {
		return nil, nil
	}
	if pidRaw := getenv("LISTEN_PID"); pidRaw != "" {
		envPid, err := strconv.Atoi(pidRaw)
		if err != nil || envPid != pid {
			// Activation vars addressed to another process — ignore them.
			return nil, nil //nolint:nilerr // not addressed to us is "no activation", not a failure.
		}
	}
	nfds, err := strconv.Atoi(fdsRaw)
	if err != nil || nfds < 1 {
		return nil, fmt.Errorf("systemd socket activation: LISTEN_FDS=%q is not a positive integer", fdsRaw)
	}
	if nfds > 1 {
		return nil, fmt.Errorf("systemd socket activation passed %d sockets; this daemon serves exactly one — declare one ListenStream per unit", nfds)
	}
	return listenerFromFD(listenFDsStart, "systemd activation socket")
}

// launchdListener returns the inetd-wait listening socket launchd passes on
// fd 0 (--from-launchd).
func launchdListener() (net.Listener, error) {
	return listenerFromFD(0, "launchd inetd-wait socket")
}

// listenerFromFD adopts an inherited descriptor as a net.Listener.
func listenerFromFD(fd uintptr, name string) (net.Listener, error) {
	f := os.NewFile(fd, name)
	if f == nil {
		return nil, fmt.Errorf("%s: fd %d is not open", name, fd)
	}
	ln, err := net.FileListener(f)
	// FileListener dups the fd; close our copy either way.
	_ = f.Close()
	if err != nil {
		return nil, fmt.Errorf("%s: fd %d is not a listening socket: %w", name, fd, err)
	}
	return ln, nil
}
