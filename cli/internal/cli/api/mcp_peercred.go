package api

// mcp_peercred.go enforces the §3.7.5 OS-identity boundary on the daemon's
// unix socket: every accepted connection's peer credential (the connecting
// process's uid, kernel-asserted) must be on the allowlist. Socket file
// permissions are the first line; this check holds even when a service
// manager created the socket with looser modes than intended.

import (
	"log/slog"
	"math"
	"net"
	"os"
	"slices"
	"strconv"
	"strings"
)

// allowedPeerUIDSet builds the peer allowlist: the --allow-uid values plus
// always the daemon's own uid and root (root can read the keys regardless —
// refusing it draws no boundary).
func allowedPeerUIDSet(uids []int) map[uint32]bool {
	set := make(map[uint32]bool, len(uids)+2)
	set[uint32(os.Getuid())] = true //nolint:gosec // uids are non-negative.
	set[0] = true
	for _, uid := range uids {
		if uid >= 0 && uid <= math.MaxUint32 {
			set[uint32(uid)] = true
		}
	}
	return set
}

// uidSetForLog renders the allowlist compactly for the startup log line.
func uidSetForLog(set map[uint32]bool) string {
	uids := make([]int, 0, len(set))
	for uid := range set {
		uids = append(uids, int(uid))
	}
	slices.Sort(uids)
	parts := make([]string, len(uids))
	for i, uid := range uids {
		parts[i] = strconv.Itoa(uid)
	}
	return strings.Join(parts, ",")
}

// peerCredListener filters accepted unix connections by peer uid. A
// disallowed or unverifiable peer is closed immediately — fail closed, no
// HTTP-level answer (the caller never authenticated to deserve one).
type peerCredListener struct {
	net.Listener
	allowed map[uint32]bool
	logger  *slog.Logger
}

func (l *peerCredListener) Accept() (net.Conn, error) {
	for {
		conn, err := l.Listener.Accept()
		if err != nil {
			return nil, err
		}
		uc, ok := conn.(*net.UnixConn)
		if !ok {
			l.logger.Warn("mcp connection refused: not a unix conn", "type_ok", ok)
			_ = conn.Close()
			continue
		}
		uid, err := peerUID(uc)
		if err != nil {
			l.logger.Warn("mcp connection refused: peer credential unavailable", "error", err)
			_ = conn.Close()
			continue
		}
		if !l.allowed[uid] {
			l.logger.Warn("mcp connection refused: peer uid not allowed", "uid", uid)
			_ = conn.Close()
			continue
		}
		return conn, nil
	}
}
