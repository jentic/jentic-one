//go:build !linux && !darwin

package api

import (
	"errors"
	"net"
)

// peerUID is unsupported off Linux/macOS: the daemon's unix-socket mode
// fails closed rather than serve without the peer-credential boundary.
func peerUID(_ *net.UnixConn) (uint32, error) {
	return 0, errors.New("peer-credential checks are not supported on this platform — use --listen with --token-file instead")
}
