package egressproxy

import (
	"fmt"
	"net"
	"sort"
	"strings"
)

// Vendor domains the proxy can govern: every host at or below a vendor's
// domain is TLS-terminated and relayed to the broker under that vendor's
// invocation token. The domains are also the session CA's permitted subtrees.
var vendorDomains = map[string]string{
	"github":     "github.com",
	"cloudflare": "api.cloudflare.com",
	"aws":        "amazonaws.com",
}

// GovernableVendors lists the vendors the proxy can govern, sorted.
func GovernableVendors() []string {
	out := make([]string, 0, len(vendorDomains))
	for v := range vendorDomains {
		out = append(out, v)
	}
	sort.Strings(out)
	return out
}

// VendorDomain is the domain governed for vendor, or "" for a vendor the
// proxy cannot govern.
func VendorDomain(vendor string) string { return vendorDomains[vendor] }

// Action is what the proxy does with a CONNECT.
type Action int

const (
	// Refuse answers 403 and logs the attempt.
	Refuse Action = iota
	// Tunnel splices the connection to the host without terminating TLS.
	Tunnel
	// Govern terminates TLS with a session-CA leaf and relays each request to
	// the broker.
	Govern
)

func (a Action) String() string {
	switch a {
	case Tunnel:
		return "tunnel"
	case Govern:
		return "govern"
	default:
		return "refuse"
	}
}

// Decision is the policy's answer for one CONNECT target.
type Decision struct {
	Action Action
	// Host is the normalised host name.
	Host string
	// Vendor is set for Govern.
	Vendor string
	// Reason explains a Refuse, for the log line.
	Reason string
}

// Policy maps a CONNECT target to an Action. Tunnel patterns are checked
// before governed domains, so an LLM endpoint under a governed domain (Bedrock
// under amazonaws.com) is tunnelled, never decrypted. Only port 443 is ever
// tunnelled or governed; every other target is refused.
type Policy struct {
	governed map[string]string // domain → vendor
	tunnel   []string
}

// NewPolicy builds a Policy for the governed vendors and the tunnel patterns.
// A tunnel pattern is an exact host name or one with "*" standing for exactly
// one whole label ("bedrock-runtime.*.amazonaws.com").
func NewPolicy(vendors, tunnel []string) (*Policy, error) {
	p := &Policy{governed: map[string]string{}}
	for _, v := range vendors {
		domain, ok := vendorDomains[v]
		if !ok {
			return nil, fmt.Errorf("the egress proxy cannot govern vendor %q (supported: %s)", v, strings.Join(GovernableVendors(), ", "))
		}
		p.governed[domain] = v
	}
	for _, pattern := range tunnel {
		norm, err := normalizePattern(pattern)
		if err != nil {
			return nil, err
		}
		p.tunnel = append(p.tunnel, norm)
	}
	return p, nil
}

// GovernedDomains lists the governed domains, sorted: the session CA's
// permitted subtrees.
func (p *Policy) GovernedDomains() []string {
	out := make([]string, 0, len(p.governed))
	for d := range p.governed {
		out = append(out, d)
	}
	sort.Strings(out)
	return out
}

// Decide classifies a CONNECT authority ("host:port").
func (p *Policy) Decide(authority string) Decision {
	rawHost, port, err := net.SplitHostPort(authority)
	if err != nil {
		return Decision{Action: Refuse, Host: authority, Reason: "malformed CONNECT target"}
	}
	host, ok := NormalizeHost(rawHost)
	if !ok {
		return Decision{Action: Refuse, Host: rawHost, Reason: "not a DNS host name"}
	}
	if port != "443" {
		return Decision{Action: Refuse, Host: host, Reason: "only port 443 is allowed"}
	}
	for _, pattern := range p.tunnel {
		if matchPattern(pattern, host) {
			return Decision{Action: Tunnel, Host: host}
		}
	}
	for domain, vendor := range p.governed {
		if host == domain || strings.HasSuffix(host, "."+domain) {
			return Decision{Action: Govern, Host: host, Vendor: vendor}
		}
	}
	return Decision{Action: Refuse, Host: host, Reason: "host is neither governed nor on the agent's tunnel allowlist"}
}

// NormalizeHost lowercases a DNS host name and drops one trailing dot. It
// reports false for an IP literal, an empty name or one with a character or
// label shape a DNS host name cannot have.
func NormalizeHost(host string) (string, bool) {
	h := strings.TrimSuffix(strings.ToLower(host), ".")
	if h == "" || len(h) > 253 || net.ParseIP(strings.Trim(h, "[]")) != nil {
		return "", false
	}
	labels := strings.Split(h, ".")
	if len(labels) < 2 {
		return "", false
	}
	for _, label := range labels {
		if !validLabel(label) {
			return "", false
		}
	}
	// A purely numeric last label is an IPv4 shorthand to some resolvers.
	if strings.Trim(labels[len(labels)-1], "0123456789") == "" {
		return "", false
	}
	return h, true
}

func validLabel(label string) bool {
	if label == "" || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
		return false
	}
	for _, r := range label {
		if (r < 'a' || r > 'z') && (r < '0' || r > '9') && r != '-' {
			return false
		}
	}
	return true
}

func normalizePattern(pattern string) (string, error) {
	p := strings.TrimSuffix(strings.ToLower(strings.TrimSpace(pattern)), ".")
	labels := strings.Split(p, ".")
	if len(labels) < 2 {
		return "", fmt.Errorf("invalid tunnel host pattern %q", pattern)
	}
	concrete := 0
	for _, label := range labels {
		if label == "*" {
			continue
		}
		if !validLabel(label) {
			return "", fmt.Errorf("invalid tunnel host pattern %q", pattern)
		}
		concrete++
	}
	// The last two labels must be concrete, so a pattern never spans a whole
	// public suffix ("*.com").
	if labels[len(labels)-1] == "*" || labels[len(labels)-2] == "*" || concrete < 2 {
		return "", fmt.Errorf("tunnel host pattern %q must end in a concrete domain", pattern)
	}
	return p, nil
}

// matchPattern reports whether host matches pattern label by label; "*"
// matches exactly one label.
func matchPattern(pattern, host string) bool {
	pl := strings.Split(pattern, ".")
	hl := strings.Split(host, ".")
	if len(pl) != len(hl) {
		return false
	}
	for i := range pl {
		if pl[i] != "*" && pl[i] != hl[i] {
			return false
		}
	}
	return true
}

// matchesDomain reports whether host is one of domains or below one.
func matchesDomain(host string, domains []string) bool {
	for _, d := range domains {
		if host == d || strings.HasSuffix(host, "."+d) {
			return true
		}
	}
	return false
}
