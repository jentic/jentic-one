package auth

import (
	"net/http"
	"net/http/httptest"
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
