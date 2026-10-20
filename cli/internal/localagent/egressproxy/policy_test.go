package egressproxy

import (
	"slices"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/localagent/container"
)

// The CLI decides which vendors a session governs from the container
// package's list; the proxy must govern exactly those.
func TestGovernableVendorsMatchTheCLI(t *testing.T) {
	if !slices.Equal(GovernableVendors(), container.GovernableVendors()) {
		t.Errorf("proxy vendors %v != CLI vendors %v", GovernableVendors(), container.GovernableVendors())
	}
}

var claudeTunnel = []string{
	"api.anthropic.com", "platform.claude.com", "statsig.anthropic.com", "console.anthropic.com",
	"bedrock-runtime.*.amazonaws.com", "oidc.*.amazonaws.com", "portal.sso.*.amazonaws.com",
}

func TestPolicyPrecedence(t *testing.T) {
	p, err := NewPolicy([]string{"github", "cloudflare", "aws"}, claudeTunnel)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		target string
		action Action
		vendor string
	}{
		{"api.anthropic.com:443", Tunnel, ""},
		{"API.Anthropic.com.:443", Tunnel, ""},
		{"bedrock-runtime.us-east-1.amazonaws.com:443", Tunnel, ""},
		{"oidc.eu-west-1.amazonaws.com:443", Tunnel, ""},
		{"portal.sso.us-east-1.amazonaws.com:443", Tunnel, ""},
		{"sts.amazonaws.com:443", Govern, "aws"},
		{"s3.us-east-1.amazonaws.com:443", Govern, "aws"},
		{"bedrock-runtime.amazonaws.com:443", Govern, "aws"}, // "*" is exactly one label
		{"bedrock-runtime.a.b.amazonaws.com:443", Govern, "aws"},
		{"github.com:443", Govern, "github"},
		{"api.github.com:443", Govern, "github"},
		{"uploads.github.com:443", Govern, "github"},
		{"api.cloudflare.com:443", Govern, "cloudflare"},
		{"dash.cloudflare.com:443", Refuse, ""},
		{"example.com:443", Refuse, ""},
		{"notgithub.com:443", Refuse, ""},
		{"github.com.evil.com:443", Refuse, ""},
		{"api.anthropic.com.evil.com:443", Refuse, ""},
		{"api.github.com:80", Refuse, ""},
		{"api.anthropic.com:8443", Refuse, ""},
		{"140.82.112.3:443", Refuse, ""},
		{"[2606:4700::1111]:443", Refuse, ""},
		{"localhost:443", Refuse, ""},
		{"github.com", Refuse, ""},
		{"gith_ub.com:443", Refuse, ""},
		{"1.2.3.4.5:443", Refuse, ""},
	} {
		d := p.Decide(tc.target)
		if d.Action != tc.action || d.Vendor != tc.vendor {
			t.Errorf("Decide(%q) = %s/%q, want %s/%q (%s)", tc.target, d.Action, d.Vendor, tc.action, tc.vendor, d.Reason)
		}
	}
}

func TestPolicyGovernsOnlyEnabledVendors(t *testing.T) {
	p, err := NewPolicy([]string{"github"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if d := p.Decide("api.cloudflare.com:443"); d.Action != Refuse {
		t.Errorf("a vendor that is not enabled must be refused, got %s", d.Action)
	}
	if got := p.GovernedDomains(); len(got) != 1 || got[0] != "github.com" {
		t.Errorf("GovernedDomains = %v", got)
	}
}

func TestPolicyRejectsBadConfig(t *testing.T) {
	if _, err := NewPolicy([]string{"gitlab"}, nil); err == nil {
		t.Error("an unknown vendor must be refused")
	}
	for _, bad := range []string{"*.com", "*", "api.*", "a.*.*", "exa mple.com", "*.*.com", "com"} {
		if _, err := NewPolicy(nil, []string{bad}); err == nil {
			t.Errorf("tunnel pattern %q must be refused", bad)
		}
	}
}
