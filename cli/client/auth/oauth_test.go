package auth

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/config"
)

// registerTestIdentity provisions the key + client-id registration BearerToken
// needs before it can reach the exchange (mirrors the middleware_test setup).
func registerTestIdentity(t *testing.T, identity, env, clientID string) {
	t.Helper()
	ref := IdentityRef{Identity: identity, Environment: env}
	if _, err := GetOrGenerateKey(ref); err != nil {
		t.Fatalf("generate key: %v", err)
	}
	if err := config.MutateConfig(func(c *config.Config) error {
		c.Identities[identity] = config.Identity{
			Type: "agent",
			Environments: map[string]config.EnvRegState{
				env: {ClientID: clientID, Status: "approved"},
			},
		}
		return nil
	}); err != nil {
		t.Fatalf("persist registration: %v", err)
	}
}

// headerStampingTransport models the CLI's attribution TransportHook: it stamps
// a User-Agent and delegates to base (wrap, never displace — SEC-20).
type headerStampingTransport struct {
	base      http.RoundTripper
	userAgent string
}

func (t headerStampingTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	req.Header.Set("User-Agent", t.userAgent)
	return t.base.RoundTrip(req)
}

// TestBearerToken_MintHonorsPinnedCAAndAttribution is the #1205 regression:
// the RFC 7523 token exchange must ride Credentials.HTTPClient — the seam that
// carries the environment's SEC-20 CA-pinned transport and the attribution
// hook — instead of the package-global default client. The token endpoint here
// serves a cert the system roots do NOT trust, so the default-client mint must
// fail while the pinned client's mint succeeds AND carries the attribution
// User-Agent the hook stamps.
func TestBearerToken_MintHonorsPinnedCAAndAttribution(t *testing.T) {
	withConfigDir(t)
	registerTestIdentity(t, "a1", "e1", "client_1")

	var lastUA atomic.Value
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		lastUA.Store(r.Header.Get("User-Agent"))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"access_token":"tok_pinned","expires_in":3600}`))
	}))
	defer srv.Close()

	// Without a credential client the mint rides the package default (system
	// roots) and must fail against the untrusted test CA — this is what proves
	// the pinned success below actually went through Credentials.HTTPClient.
	if _, err := BearerToken(Credentials{BaseURL: srv.URL, IdentityName: "a1", EnvironmentName: "e1"}); err == nil {
		t.Fatal("mint on the default client must fail against a CA the system roots do not trust")
	}

	// The environment's client: CA-pinned transport (SEC-20) with the
	// attribution RoundTripper composed over it, exactly as clictx builds it.
	pool := x509.NewCertPool()
	pool.AddCert(srv.Certificate())
	pinned := &http.Client{
		Transport: headerStampingTransport{
			base:      &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}},
			userAgent: "jentic-mcp/test",
		},
	}
	tok, err := BearerToken(Credentials{
		BaseURL: srv.URL, IdentityName: "a1", EnvironmentName: "e1",
		HTTPClient: pinned,
	})
	if err != nil {
		t.Fatalf("mint through the pinned credential client: %v", err)
	}
	if tok != "tok_pinned" {
		t.Errorf("token = %q, want the pinned mint's token", tok)
	}
	if ua, _ := lastUA.Load().(string); ua != "jentic-mcp/test" {
		t.Errorf("exchange User-Agent = %q, want the attribution hook's value", ua)
	}
}

// TestBearerToken_MintRefusesRedirect is the #1207 auth-leg regression: a
// token endpoint that answers the exchange POST with a redirect must NOT be
// followed — the redirect target never receives a request — and the surfaced
// failure names the redirect instead of a bare "status 302".
func TestBearerToken_MintRefusesRedirect(t *testing.T) {
	withConfigDir(t)
	registerTestIdentity(t, "a2", "e2", "client_2")

	var paths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		if r.URL.Path == "/stolen" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"access_token":"tok_evil","expires_in":3600}`))
			return
		}
		http.Redirect(w, r, "/stolen", http.StatusFound)
	}))
	defer srv.Close()

	_, err := BearerToken(Credentials{BaseURL: srv.URL, IdentityName: "a2", EnvironmentName: "e2"})
	if err == nil {
		t.Fatal("a redirected token exchange must fail, never mint through the redirect target")
	}
	if !strings.Contains(err.Error(), "redirect") || !strings.Contains(err.Error(), "/stolen") {
		t.Errorf("error %q should name the refused redirect and its Location", err)
	}
	if len(paths) != 1 || paths[0] != "/oauth/token" {
		t.Errorf("requested paths = %v, want only /oauth/token (the redirect target must never be fetched)", paths)
	}
}

// back to the package default; a caller client always yields a COPY (the
// shared plane client must never be mutated) that keeps the caller's own
// timeout and transport, gets exchangeTimeout when the caller set none (a
// mint must never ride a zero-timeout client — rules/05), and carries the
// never-follow-redirects posture regardless of what the caller configured
// (#1207).
func TestExchangeClient_Resolution(t *testing.T) {
	if got := exchangeClient(Credentials{}); got != httpClient {
		t.Errorf("nil HTTPClient must resolve to the package default")
	}
	if httpClient.CheckRedirect == nil {
		t.Error("the package default client must refuse redirects (#1207)")
	}

	withTimeout := &http.Client{Timeout: 5 * time.Second}
	got := exchangeClient(Credentials{HTTPClient: withTimeout})
	if got == withTimeout {
		t.Fatal("a caller client must be copied, not returned as-is (the redirect posture is stamped on the copy)")
	}
	if got.Timeout != 5*time.Second {
		t.Errorf("copy timeout = %v, want the caller's own 5s", got.Timeout)
	}
	if got.CheckRedirect == nil {
		t.Error("the exchange copy must refuse redirects (#1207)")
	}
	if err := got.CheckRedirect(nil, nil); !errors.Is(err, http.ErrUseLastResponse) {
		t.Errorf("CheckRedirect returned %v, want http.ErrUseLastResponse", err)
	}
	if withTimeout.CheckRedirect != nil {
		t.Error("the caller's client must not be mutated")
	}

	rt := headerStampingTransport{base: http.DefaultTransport, userAgent: "x"}
	zeroTimeout := &http.Client{Transport: rt}
	got = exchangeClient(Credentials{HTTPClient: zeroTimeout})
	if got == zeroTimeout {
		t.Fatal("a zero-timeout caller client must be copied, not returned as-is")
	}
	if got.Timeout != exchangeTimeout {
		t.Errorf("copy timeout = %v, want %v", got.Timeout, exchangeTimeout)
	}
	if got.Transport != http.RoundTripper(rt) {
		t.Error("the copy must keep the caller's transport (the pinned+hooked one)")
	}
	if zeroTimeout.Timeout != 0 {
		t.Error("the caller's client must not be mutated")
	}
}
