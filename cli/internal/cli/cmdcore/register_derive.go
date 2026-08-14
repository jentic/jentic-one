package cmdcore

import (
	"fmt"
	"net"
	"net/url"
	"os"
	"strings"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/config"
)

func notEmptyField(label string) func(string) error {
	return func(s string) error {
		if strings.TrimSpace(s) == "" {
			return fmt.Errorf("%s must not be empty", label)
		}
		return nil
	}
}

// deriveEnvName proposes an environment name from the install URL: the first
// DNS label of the host, sanitized to the config-name charset ("default" when
// nothing survives — e.g. a bare IP). Predictable and overridable via --env.
func deriveEnvName(installURL string) string {
	u, err := url.Parse(installURL)
	if err != nil || u.Hostname() == "" {
		return "default"
	}
	label, _, _ := strings.Cut(u.Hostname(), ".")
	if s := sanitizeConfigName(label); s != "" {
		return s
	}
	return "default"
}

// localBrokerURL returns the co-located local broker URL for a loopback control
// plane, or "" for any non-loopback (remote/enterprise) URL. On a local install
// jenticctl stands the broker up on the standard broker port over plain HTTP, on
// the same loopback host, so `jentic execute` should target it there rather than
// the https default. It is deliberately NOT a general base_url→broker_url
// derivation: remote deployments run the broker on a different domain and must
// set broker_url explicitly.
func localBrokerURL(installURL string) string {
	u, err := url.Parse(installURL)
	if err != nil {
		return ""
	}
	host := u.Hostname()
	if !isLoopbackHost(host) {
		return ""
	}
	// QA-9: canonicalise a "localhost" broker host to 127.0.0.1 too, so a seeded
	// broker_url never carries the audience-mismatching name even if this is ever
	// called with a non-normalised URL.
	if host == "localhost" {
		host = "127.0.0.1"
	}
	_, port, _ := strings.Cut(config.DefaultBrokerHost, ":")
	return "http://" + net.JoinHostPort(host, port)
}

// normalizeLoopbackURL rewrites a "localhost" host to "127.0.0.1", preserving
// scheme, port, and path. It returns the (possibly unchanged) URL and whether a
// rewrite happened. Non-localhost hosts (including 127.0.0.1 and remote hosts)
// are returned verbatim. A malformed URL is returned unchanged.
func normalizeLoopbackURL(raw string) (string, bool) {
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() != "localhost" {
		return raw, false
	}
	if p := u.Port(); p != "" {
		u.Host = net.JoinHostPort("127.0.0.1", p)
	} else {
		u.Host = "127.0.0.1"
	}
	return u.String(), true
}

// isLoopbackHost reports whether host is a loopback name/address ("localhost",
// 127.0.0.0/8, or ::1).
func isLoopbackHost(host string) bool {
	if host == "localhost" {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return false
}

// defaultIdentityName proposes an identity name from the machine hostname
// (first label, sanitized), falling back to "agent".
func defaultIdentityName() string {
	host, err := os.Hostname()
	if err == nil {
		label, _, _ := strings.Cut(host, ".")
		if s := sanitizeConfigName(label); s != "" {
			return s
		}
	}
	return "agent"
}

// sanitizeConfigName coerces s into the config name charset via the canonical
// config.SanitizeName (ARCH-22), returning "" when nothing valid survives so the
// callers (deriveEnvName/deriveIdentityName) can apply their own fallback.
func sanitizeConfigName(s string) string {
	return sdkconfig.SanitizeName(s)
}
