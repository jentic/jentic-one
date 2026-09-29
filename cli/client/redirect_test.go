package client

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
)

// twoOrigins starts a "home" server (the credential's base origin) and an
// "other" server on a different port — a different origin. home redirects
// /away to other and /here to itself; other counts hits and records any
// Authorization header it sees.
func twoOrigins(t *testing.T) (home, other *httptest.Server, otherHits *atomic.Int32, otherAuth *atomic.Value) {
	t.Helper()
	otherHits = &atomic.Int32{}
	otherAuth = &atomic.Value{}
	other = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		otherHits.Add(1)
		otherAuth.Store(r.Header.Get("Authorization"))
		w.WriteHeader(http.StatusUnauthorized)
	}))
	t.Cleanup(other.Close)
	home = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/away":
			http.Redirect(w, r, other.URL+"/landing", http.StatusFound)
		case "/here":
			http.Redirect(w, r, "/final", http.StatusFound)
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	t.Cleanup(home.Close)
	return home, other, otherHits, otherAuth
}

func getVia(t *testing.T, hc *http.Client, url string) *http.Response {
	t.Helper()
	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, url, nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer at_home")
	r, err := hc.Do(req)
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	return r
}

// TestPlaneClient_DoesNotFollowCrossOriginRedirect: the authenticated plane
// client surfaces a redirect to another origin instead of following it, so
// the other origin never sees the request or a (re-)attached credential.
func TestPlaneClient_DoesNotFollowCrossOriginRedirect(t *testing.T) {
	shrinkRetryKnobs(t)
	withClientConfigDir(t)
	home, _, otherHits, _ := twoOrigins(t)

	// Disk-token creds: re-exchangeable, so a 401 from the other origin would
	// previously have triggered a re-stamp of a freshly minted bearer.
	ref := auth.IdentityRef{Identity: "a", Environment: "e"}
	if err := auth.SaveTokens(ref, &auth.TokenSet{AccessToken: "at_home", ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}
	hc := Config{ControlBaseURL: home.URL, IdentityName: "a", EnvironmentName: "e"}.httpClient()

	r := getVia(t, hc, home.URL+"/away")
	defer closeResp(r)
	if r.StatusCode != http.StatusFound {
		t.Errorf("status = %d, want the 302 surfaced", r.StatusCode)
	}
	if n := otherHits.Load(); n != 0 {
		t.Errorf("other origin contacted %d times, want 0", n)
	}
	if _, err := auth.ReadTokens(ref); err != nil {
		t.Errorf("stored token should be untouched, ReadTokens: %v", err)
	}
}

// TestPlaneClient_FollowsSameOriginRedirect: redirects that stay on the
// origin keep working.
func TestPlaneClient_FollowsSameOriginRedirect(t *testing.T) {
	home, _, _, _ := twoOrigins(t)
	hc := Config{ControlBaseURL: home.URL, InjectedBearerToken: "at_home"}.httpClient()

	r := getVia(t, hc, home.URL+"/here")
	defer closeResp(r)
	if r.StatusCode != http.StatusOK || r.Request.URL.Path != "/final" {
		t.Errorf("status = %d at %s, want 200 at /final", r.StatusCode, r.Request.URL.Path)
	}
}

// TestPlaneClient_PreservesCallerRedirectPolicy: a caller's CheckRedirect
// still governs the same-origin redirects we allow.
func TestPlaneClient_PreservesCallerRedirectPolicy(t *testing.T) {
	home, _, _, _ := twoOrigins(t)
	var called atomic.Bool
	base := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error {
		called.Store(true)
		return http.ErrUseLastResponse
	}}
	hc := Config{ControlBaseURL: home.URL, InjectedBearerToken: "at_home", HTTPClient: base}.httpClient()

	r := getVia(t, hc, home.URL+"/here")
	defer closeResp(r)
	if !called.Load() || r.StatusCode != http.StatusFound {
		t.Errorf("caller policy called=%v, status=%d; want true, 302", called.Load(), r.StatusCode)
	}
}

// TestBrokerTransport_DoesNotFollowCrossOriginRedirect: execute's own bearer
// never leaves the broker origin on a redirect either.
func TestBrokerTransport_DoesNotFollowCrossOriginRedirect(t *testing.T) {
	home, _, otherHits, otherAuth := twoOrigins(t)
	hc := BrokerTransport(Config{})

	r := getVia(t, hc, home.URL+"/away")
	defer closeResp(r)
	if r.StatusCode != http.StatusFound || otherHits.Load() != 0 {
		t.Errorf("status = %d, other hits = %d (auth %v); want 302 and 0", r.StatusCode, otherHits.Load(), otherAuth.Load())
	}
}

// TestRetry_401FromOtherOriginIsNotReExchanged: the 401 arm only re-mints
// for a request on the credential's origin; a 401 elsewhere surfaces as-is
// with the stored token untouched and no bearer re-attached.
func TestRetry_401FromOtherOriginIsNotReExchanged(t *testing.T) {
	shrinkRetryKnobs(t)
	withClientConfigDir(t)
	_, other, otherHits, otherAuth := twoOrigins(t)

	ref := auth.IdentityRef{Identity: "a", Environment: "e"}
	if err := auth.SaveTokens(ref, &auth.TokenSet{AccessToken: "at_home", ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}
	rt := newRetryTransport(nil, auth.Credentials{
		BaseURL: "http://127.0.0.1:1", IdentityName: "a", EnvironmentName: "e",
	})
	r, err := rt.RoundTrip(newReq(t, http.MethodGet, other.URL+"/landing", ""))
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	defer closeResp(r)
	if r.StatusCode != http.StatusUnauthorized || otherHits.Load() != 1 {
		t.Errorf("status = %d, hits = %d; want 401 after exactly one attempt", r.StatusCode, otherHits.Load())
	}
	if got, _ := otherAuth.Load().(string); got != "" {
		t.Errorf("other origin received Authorization %q, want none", got)
	}
	if _, err := auth.ReadTokens(ref); err != nil {
		t.Errorf("stored token should be untouched, ReadTokens: %v", err)
	}
}

// TestPlaneClient_CapsSameOriginRedirects: with no caller policy, the
// default cap of 10 still applies to redirects that stay on the origin.
func TestPlaneClient_CapsSameOriginRedirects(t *testing.T) {
	var hits atomic.Int32
	loop := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		http.Redirect(w, r, "/loop", http.StatusFound)
	}))
	t.Cleanup(loop.Close)
	hc := Config{ControlBaseURL: loop.URL, InjectedBearerToken: "at_home"}.httpClient()

	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, loop.URL+"/loop", nil)
	if err != nil {
		t.Fatal(err)
	}
	r, err := hc.Do(req)
	if err == nil {
		closeResp(r)
		t.Fatal("Do: want a redirect-cap error, got none")
	}
	if n := hits.Load(); n != maxRedirects {
		t.Errorf("server hit %d times, want %d", n, maxRedirects)
	}
}
