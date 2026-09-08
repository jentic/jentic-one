package api

import (
	"net"
	"net/url"

	"github.com/jentic/jentic-one/cli/internal/agentops"
)

// Denial classification (isBrokerDenial / the Jentic-Error-Origin
// disambiguation) and the TLS-mismatch papercut detection moved to the UX-free
// core: agentops.Classify / agentops.IsBrokerDenial (classify.go) and the
// transport-error mapping inside agentops.Do. What remains here are the
// broker-TARGET guards — they read flag/environment state, so they are
// cobra-side by design (plan 0.2).

// brokerIsLoopbackDefault reports whether hostPort (host or host:port) is a
// loopback target — the built-in default the fail-closed guard must catch. An
// empty/malformed value is treated as loopback: it can only have come from the
// built-in default (config.DefaultBrokerHost), never from an explicit remote
// broker, so failing closed here is correct.
func brokerIsLoopbackDefault(hostPort string) bool {
	if hostPort == "" {
		return true
	}
	host := hostPort
	if h, _, err := net.SplitHostPort(hostPort); err == nil {
		host = h
	}
	return agentops.IsLoopbackHost(host)
}

// baseURLIsRemote reports whether the control-plane base_url points at a
// non-loopback host (the "remote install" signal for the fail-closed guard). A
// loopback, empty, or unparseable base_url is NOT remote, so a local workflow
// never trips the guard.
func baseURLIsRemote(base string) bool {
	if base == "" {
		return false
	}
	u, err := url.Parse(base)
	if err != nil || u.Hostname() == "" {
		return false
	}
	return !agentops.IsLoopbackHost(u.Hostname())
}
