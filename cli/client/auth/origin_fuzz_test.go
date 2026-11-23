package auth

import (
	"net/url"
	"strings"
	"testing"
)

func FuzzNormalizeOrigin(f *testing.F) {
	seeds := []string{
		"",
		"https://origin-a.example.com",
		"https://origin-a.example.com/v1",
		"https://ORIGIN-A.EXAMPLE.COM:443/v2?x=1#frag",
		"http://127.0.0.1:80/path",
		"http://127.0.0.1:8080/path",
		"http://[::1]:8080/api",
		"https://[::1]:443/api",
		"https://user:pass@origin-a.example.com:8443/v1?query=1#hash", // pragma: allowlist secret
		"https://jentic.corp@evil.example.com/login",
		"://bad",
		"relative/path",
		"http:///missing-host",
		"mailto:user@example.com",
		"A://::",
		"http://[::1%25lo0]:8080/path",
	}
	for _, s := range seeds {
		f.Add(s)
	}

	f.Fuzz(func(t *testing.T, raw string) {
		n := NormalizeOrigin(raw)
		if n == "" {
			return
		}
		if strings.ContainsAny(n, "@?#") {
			t.Fatalf("NormalizeOrigin(%q) = %q contains userinfo, query, or fragment delimiter", raw, n)
		}
		uRaw, err := url.Parse(raw)
		if err != nil {
			t.Fatalf("NormalizeOrigin(%q) = %q, but url.Parse(raw) failed: %v", raw, n, err)
		}
		// Skip RFC 3986-invalid unbracketed multi-colon hosts (which Go's
		// url.Parse only rejects once bracketed) and RFC 6874 zone IDs whose
		// unescaped '%' is rejected on re-parse.
		if (strings.Contains(uRaw.Hostname(), ":") && !strings.HasPrefix(uRaw.Host, "[")) ||
			strings.Contains(uRaw.Hostname(), "%") {
			return
		}
		if got := NormalizeOrigin(n); got != n {
			t.Fatalf("NormalizeOrigin(%q) = %q is not idempotent: NormalizeOrigin(%q) = %q", raw, n, n, got)
		}
		uNorm, err := url.Parse(n)
		if err != nil {
			t.Fatalf("NormalizeOrigin(%q) produced unparseable origin %q: %v", raw, n, err)
		}
		if uNorm.User != nil || uNorm.Path != "" || uNorm.RawQuery != "" || uNorm.Fragment != "" {
			t.Fatalf("NormalizeOrigin(%q) = %q retained non-origin components: %+v", raw, n, uNorm)
		}
		if !SameOrigin(uRaw, uNorm) {
			t.Fatalf("SameOrigin(parse(%q), parse(%q)) = false", raw, n)
		}
	})
}

func FuzzMatchesBaseOrigin(f *testing.F) {
	seeds := []struct {
		base   string
		target string
	}{
		{"https://origin-a.example.com", "https://origin-a.example.com/v1"},
		{"https://origin-a.example.com/v1", "https://ORIGIN-A.EXAMPLE.COM:443/v2"},
		{"http://127.0.0.1:80", "http://127.0.0.1/apis"},
		{"https://origin-a.example.com", "https://origin-b.example.com/v1"},
		{"https://origin-a.example.com", "http://origin-a.example.com/v1"},
		{"https://origin-a.example.com", "https://origin-a.example.com:8443/v1"},
		{"https://jentic.corp", "https://jentic.corp@evil.example.com/v1"},
		{"https://jentic.corp:8443", "https://jentic.corp:8443@evil.example.com:8443/v1"},
		{"://bad", "https://origin-a.example.com"},
		{"https://origin-a.example.com", "://bad"},
	}
	for _, s := range seeds {
		f.Add(s.base, s.target)
	}

	f.Fuzz(func(t *testing.T, base, target string) {
		uTarget, _ := url.Parse(target)
		matched := MatchesBaseOrigin(Credentials{BaseURL: base}, uTarget)
		if base == "" {
			if !matched {
				t.Fatalf("MatchesBaseOrigin with empty BaseURL returned false")
			}
			return
		}
		if matched {
			uBase, err := url.Parse(base)
			if err != nil || uBase == nil || uTarget == nil {
				t.Fatalf("MatchesBaseOrigin(%q, %q) = true with invalid parse", base, target)
			}
			if !strings.EqualFold(uBase.Scheme, uTarget.Scheme) {
				t.Fatalf("MatchesBaseOrigin(%q, %q) = true with mismatched schemes %q vs %q", base, target, uBase.Scheme, uTarget.Scheme)
			}
			if !strings.EqualFold(uBase.Hostname(), uTarget.Hostname()) {
				t.Fatalf("MatchesBaseOrigin(%q, %q) = true with mismatched hostnames %q vs %q", base, target, uBase.Hostname(), uTarget.Hostname())
			}
			if originPort(uBase) != originPort(uTarget) {
				t.Fatalf("MatchesBaseOrigin(%q, %q) = true with mismatched ports %q vs %q", base, target, originPort(uBase), originPort(uTarget))
			}
		}

		// Userinfo in target must never make a foreign host match.
		if n := NormalizeOrigin(base); n != "" {
			uBase, err := url.Parse(n)
			if err == nil && uBase.Hostname() != "" && !strings.EqualFold(uBase.Hostname(), "foreign.example.com") {
				spoofed, err := url.Parse(uBase.Scheme + "://" + uBase.Hostname() + "@foreign.example.com/v1")
				if err == nil && MatchesBaseOrigin(Credentials{BaseURL: base}, spoofed) {
					t.Fatalf("MatchesBaseOrigin(%q, %q) matched foreign host via userinfo", base, spoofed.String())
				}
			}
		}
	})
}
