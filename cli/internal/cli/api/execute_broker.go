package api

import (
	"net"
	"net/http"
	"net/url"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// brokerTLSMismatch reports whether err is the "https client hit an http server"
// signature against a loopback broker — the local default-scheme papercut UX-4
// makes actionable. Restricted to loopback so we never suggest downgrading a
// remote broker to plaintext.
func brokerTLSMismatch(err error, brokerURL string) bool {
	if err == nil || !strings.Contains(err.Error(), "server gave HTTP response to HTTPS client") {
		return false
	}
	u, perr := url.Parse(brokerURL)
	if perr != nil {
		return false
	}
	return isLoopbackHostname(u.Hostname())
}

// isLoopbackHostname reports whether host is "localhost" or a loopback IP.
func isLoopbackHostname(host string) bool {
	if host == "localhost" {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return false
}

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
	return isLoopbackHostname(host)
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
	return !isLoopbackHostname(u.Hostname())
}

// brokerDeniedErr is the typed denial every broker-denial exit shares (AGT-6):
// BROKER_DENIED (exit 2) with the denying HTTP status in Details, so the agent
// envelope and the exit code come from the same table. The response body /
// directive recovery text is printed by the caller before this is returned.
func brokerDeniedErr(resp *http.Response) *ux.CodedError {
	return &ux.CodedError{
		Code:    ux.CodeBrokerDenied,
		Msg:     "the broker denied this call before it reached the upstream API",
		Details: map[string]any{"http_status": resp.StatusCode},
	}
}

// isBrokerDenial reports whether a response is one the broker itself emitted to
// deny a call the agent can recover from: missing toolkit binding → 403,
// ambiguous toolkit → 409, credential needs reconnect → 401, no credential →
// 424. Each carries an agent_directive (see broker/web/errors.STATUS_BY_ERROR).
//
// Status alone is NOT sufficient: the broker is a transparent forward proxy, so
// an *upstream* API can return these same 4xx codes on a call the broker
// successfully proxied (the upstream auth failed, the resource is forbidden,
// etc.). Treating those as broker denials would exit 2 and print a misleading
// "recovery required" for a call that actually ran. The broker disambiguates
// with the Jentic-Error-Origin response header (broker/core/headers): it stamps
// "broker" on its own errors and "upstream" on mirrored pass-through 4xx/5xx
// (broker/services/execution/pipeline.enrich_error_origin). So a denial-class
// status is a broker denial only when the origin is not "upstream" (a missing
// header is treated as broker, since the loopback broker always sets it on its
// own errors and only a non-conformant proxy would omit it).
func isBrokerDenial(resp *http.Response) bool {
	if resp == nil {
		return false
	}
	if errorOrigin(resp) == errorOriginUpstream {
		return false
	}
	switch resp.StatusCode {
	case http.StatusUnauthorized,
		http.StatusForbidden,
		http.StatusConflict,
		http.StatusFailedDependency:
		return true
	default:
		return false
	}
}

// errorOriginUpstream is the Jentic-Error-Origin value the broker stamps on a
// mirrored upstream response (broker ErrorOrigin.UPSTREAM). The matching header
// name mirrors broker/core/headers.JenticHeader.ERROR_ORIGIN.
const errorOriginUpstream = "upstream"

func errorOrigin(resp *http.Response) string {
	if resp == nil {
		return ""
	}
	return strings.ToLower(strings.TrimSpace(resp.Header.Get("Jentic-Error-Origin")))
}
