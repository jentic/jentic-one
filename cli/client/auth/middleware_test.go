package auth

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/config"
)

// TestBearerToken_SingleflightsConcurrentMints pins the local-MCP §3.7.1
// efficiency guard: N concurrent BearerToken callers hitting one expiry
// instant share ONE token exchange (mint-fresh was already concurrency-safe —
// last-writer-wins on independently valid tokens — so this asserts the dedup,
// not correctness). The token endpoint stalls long enough that every waiter
// joins the first caller's flight before it completes.
func TestBearerToken_SingleflightsConcurrentMints(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "a1", Environment: "e1"}
	if _, err := GetOrGenerateKey(ref); err != nil {
		t.Fatalf("generate key: %v", err)
	}
	if err := config.MutateConfig(func(c *config.Config) error {
		c.Identities["a1"] = config.Identity{
			Type: "agent",
			Environments: map[string]config.EnvRegState{
				"e1": {ClientID: "client_1", Status: "approved"},
			},
		}
		return nil
	}); err != nil {
		t.Fatalf("persist registration: %v", err)
	}

	var exchanges atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		exchanges.Add(1)
		// Hold the flight open so every concurrent caller joins it.
		time.Sleep(250 * time.Millisecond)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"access_token":"tok_minted","expires_in":3600}`))
	}))
	defer srv.Close()

	creds := Credentials{BaseURL: srv.URL, IdentityName: "a1", EnvironmentName: "e1"}
	const callers = 8
	tokens := make([]string, callers)
	errs := make([]error, callers)
	start := make(chan struct{})
	var wg sync.WaitGroup
	for i := range callers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			tokens[i], errs[i] = BearerToken(creds)
		}()
	}
	close(start)
	wg.Wait()

	for i := range callers {
		if errs[i] != nil {
			t.Fatalf("caller %d: %v", i, errs[i])
		}
		if tokens[i] != "tok_minted" {
			t.Errorf("caller %d got %q, want the shared minted token", i, tokens[i])
		}
	}
	if got := exchanges.Load(); got != 1 {
		t.Errorf("token exchanges = %d, want 1 (singleflight must dedupe concurrent mints)", got)
	}
}

// TestBearerToken_SingleflightIsolatesIdentities pins the OTHER half of the
// singleflight contract: the key scopes the flight to ONE logical credential
// (base URL, identity, environment), so two identities minting concurrently
// must run two separate exchanges and each receive its OWN token — sharing a
// flight across identities is the exact failure the key exists to prevent.
func TestBearerToken_SingleflightIsolatesIdentities(t *testing.T) {
	withConfigDir(t)
	for i, id := range []string{"a1", "a2"} {
		ref := IdentityRef{Identity: id, Environment: "e1"}
		if _, err := GetOrGenerateKey(ref); err != nil {
			t.Fatalf("generate key for %s: %v", id, err)
		}
		clientID := fmt.Sprintf("client_%d", i+1)
		if err := config.MutateConfig(func(c *config.Config) error {
			c.Identities[id] = config.Identity{
				Type: "agent",
				Environments: map[string]config.EnvRegState{
					"e1": {ClientID: clientID, Status: "approved"},
				},
			}
			return nil
		}); err != nil {
			t.Fatalf("persist registration for %s: %v", id, err)
		}
	}

	var exchanges atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		exchanges.Add(1)
		// Hold the flight open long enough that a wrongly-shared key would
		// fold the second identity's mint into the first one's flight.
		time.Sleep(250 * time.Millisecond)
		w.Header().Set("Content-Type", "application/json")
		// Key the minted token to the asserted client so a cross-identity
		// token leak is visible in the assertion below.
		_, _ = fmt.Fprintf(w, `{"access_token":"tok_%s","expires_in":3600}`, assertionIssuer(t, r))
	}))
	defer srv.Close()

	var wg sync.WaitGroup
	tokens := make([]string, 2)
	errs := make([]error, 2)
	start := make(chan struct{})
	for i, id := range []string{"a1", "a2"} {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			tokens[i], errs[i] = BearerToken(Credentials{BaseURL: srv.URL, IdentityName: id, EnvironmentName: "e1"})
		}()
	}
	close(start)
	wg.Wait()

	for i, want := range []string{"tok_client_1", "tok_client_2"} {
		if errs[i] != nil {
			t.Fatalf("identity %d: %v", i, errs[i])
		}
		if tokens[i] != want {
			t.Errorf("identity %d got %q, want its own minted token %q", i, tokens[i], want)
		}
	}
	if got := exchanges.Load(); got != 2 {
		t.Errorf("token exchanges = %d, want 2 (two identities must never share a flight)", got)
	}
}

// assertionIssuer extracts the `iss` claim (the registered client id) from the
// JWT-bearer assertion in an exchange request, without verifying the
// signature — the test only needs to know WHICH identity is minting.
func assertionIssuer(t *testing.T, r *http.Request) string {
	t.Helper()
	var body struct {
		Assertion string `json:"assertion"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		t.Errorf("decode exchange body: %v", err)
		return ""
	}
	parts := strings.Split(body.Assertion, ".")
	if len(parts) != 3 {
		t.Errorf("assertion is not a JWT: %q", body.Assertion)
		return ""
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		t.Errorf("decode assertion payload: %v", err)
		return ""
	}
	var claims struct {
		Iss string `json:"iss"`
	}
	if err := json.Unmarshal(payload, &claims); err != nil {
		t.Errorf("unmarshal assertion claims: %v", err)
		return ""
	}
	return claims.Iss
}
