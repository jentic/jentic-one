// Package container runs a coding agent for `jentic run --container`: in a
// Linux container with the work folder bind-mounted at its host path, reaching
// Jentic at the same loopback URLs the CLI uses and nothing else.
//
// A session is a handful of Docker objects that share one session id label:
//
//   - a per-session `internal` network with no gateway, whose members are the
//     loopback container and the egress sidecar, if there is one (Egress);
//   - with the default "proxy" egress, the egress proxy sidecar
//     (ProxyEgress), the only member of the internal network with a route
//     out, through a per-session egress network of its own, and a CA volume
//     holding its public certificates;
//   - the loopback container, which owns the agent's network namespace and
//     listens on 127.0.0.1 at Jentic's ports before the agent starts, so the
//     agent cannot bind them first;
//   - the relay container, outside that network, which carries those ports to
//     the host's Jentic over unix sockets in a volume only the two bridge
//     containers mount;
//   - the agent container, sharing the loopback's network namespace, running as
//     an unprivileged uid with every capability dropped, no-new-privileges and
//     no Docker socket, with its home in a per-session volume.
//
// Teardown removes every object carrying the session label, on a normal exit,
// a cancel and a signal alike; Reap removes the objects of sessions whose
// client died (SIGKILL, a crash) on the next run.
//
// Like the parent localagent package this package is stdlib-only and returns
// argv and data; it drives Docker through the `docker` CLI (Docker), so no
// Docker SDK dependency is linked into the CLI.
package container

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"regexp"
)

// Labels carried by every Docker object of a session. Reap and Teardown find a
// session's objects by these, never by name.
const (
	// LabelSession holds the session id.
	LabelSession = "dev.jentic.run.session"
	// LabelOwner identifies the client that created the session
	// (OwnerLabel), so a reaper only ever removes its own user's sessions.
	LabelOwner = "dev.jentic.run.owner"
	// LabelRole names the object's part in the session (RoleAgent, …).
	LabelRole = "dev.jentic.run.role"
)

// Session object roles (LabelRole values).
const (
	RoleAgent    = "agent"
	RoleLoopback = "loopback"
	RoleRelay    = "relay"
	RoleEgress   = "egress"
	RoleNetwork  = "network"
	RoleHome     = "home"
	RoleSockets  = "sockets"
)

// Default image references. None is published: build them locally with the
// Dockerfiles under deploy/docker (see BuildHint).
const (
	DefaultAgentImage  = "jentic-run-agent:local"
	DefaultBridgeImage = "jentic-run-bridge:local"
	DefaultProxyImage  = "jentic-run-proxy:local"
)

// AgentHome is the agent's home inside the agent container. The session's home
// volume is mounted here.
const AgentHome = "/home/agent"

// socketDir is where the bridge image keeps the relay's unix sockets; the
// session's socket volume is mounted here in the relay and loopback only.
const socketDir = "/run/jentic-relay"

// bridgeUser is the unprivileged uid:gid both bridge containers run as; the
// bridge image owns socketDir for it.
const bridgeUser = "65534:65534"

var sessionIDRe = regexp.MustCompile(`^[a-f0-9]{12}$`)

// NewSessionID returns a random session id (12 lowercase hex characters).
func NewSessionID() (string, error) {
	b := make([]byte, 6)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("generate a session id: %w", err)
	}
	return hex.EncodeToString(b), nil
}

// ValidSessionID reports whether id has the NewSessionID shape. Ids read back
// from Docker labels are checked before they reach a name or a filter.
func ValidSessionID(id string) bool { return sessionIDRe.MatchString(id) }

// OwnerLabel is the LabelOwner value for the client running as uid on host.
func OwnerLabel(uid int, host string) string { return fmt.Sprintf("%d@%s", uid, host) }

// BuildHint is the command that builds the default images from a jentic-one
// checkout, for the error a missing image produces.
func BuildHint() string {
	return "docker build -f deploy/docker/run-agent.Dockerfile -t " + DefaultAgentImage + " cli && " +
		"docker build -f deploy/docker/run-bridge.Dockerfile -t " + DefaultBridgeImage + " deploy/docker && " +
		"docker build -f deploy/docker/run-proxy.Dockerfile -t " + DefaultProxyImage + " cli"
}
