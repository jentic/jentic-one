package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
)

// These tests pin the CONTEXT-FIRST resolution of the data-plane command family
// (agentSession and friends): with an active V2 context, commands like `access
// whoami`/`logout` must authenticate from the XDG store — the environment URL
// and (identity, environment)-scoped credential — and never consult the legacy
// ~/.jentic profile store or its localhost default. This is the split-brain the
// identity-unification work closes: `env add` → `context create --use` →
// `identity register` → data commands, one store end to end.

// setupContext creates env/identity/context in the isolated XDG config and
// makes it active. The identity carries a jak_* API key so commands
// authenticate without a token exchange.
func setupContext(t *testing.T, envURL string) {
	t.Helper()
	if err := runJentic(t, "env", "add", "local", "--url", envURL); err != nil {
		t.Fatalf("env add: %v", err)
	}
	if err := runJentic(t, "identity", "add", "agent1", "--type", "agent",
		"--api-key", "jak_ctx_test_key", "--env", "local"); err != nil {
		t.Fatalf("identity add: %v", err)
	}
	if err := runJentic(t, "context", "create", "c1", "--env", "local", "--identity", "agent1"); err != nil {
		t.Fatalf("context create: %v", err)
	}
	if err := runJentic(t, "context", "use", "c1"); err != nil {
		t.Fatalf("context use: %v", err)
	}
}

func TestDataPlane_ContextFirst_UsesEnvURLAndStoredCredential(t *testing.T) {
	withXDG(t)

	var gotPath, gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath, gotAuth = r.URL.Path, r.Header.Get("Authorization")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"type": "agent", "id": "agent_1", "name": "agent1", "status": "active",
		})
	}))
	defer srv.Close()

	setupContext(t, srv.URL)

	if err := runJentic(t, "access", "whoami"); err != nil {
		t.Fatalf("access whoami via context: %v", err)
	}
	if gotPath != "/me" {
		t.Errorf("request path = %q, want /me on the CONTEXT env URL", gotPath)
	}
	if gotAuth != "Bearer jak_ctx_test_key" {
		t.Errorf("Authorization = %q, want the XDG-stored API key", gotAuth)
	}
}

// TestDataPlane_ProfileFlagPinsLegacyStore: --profile is the explicit V1 escape
// hatch. Even with a V2 context active, it must address the legacy store — and
// with no such profile configured, fail rather than silently answer from the
// context (which would make the flag a lie).
func TestDataPlane_ProfileFlagPinsLegacyStore(t *testing.T) {
	withXDG(t)

	requests := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests++
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	setupContext(t, srv.URL)

	err := runJentic(t, "access", "whoami", "--profile", "no-such-profile")
	if err == nil {
		t.Fatal("expected --profile against an empty legacy store to fail")
	}
	if requests != 0 {
		t.Errorf("context env URL received %d request(s); --profile must pin the legacy store", requests)
	}
}

// TestDataPlane_ContextUnregistered_FailsActionable: a context whose identity
// holds no credential (no API key, never registered) must fail with the V2
// remediation — `jentic identity register` — not fall back to a legacy profile
// or a localhost default.
func TestDataPlane_ContextUnregistered_FailsActionable(t *testing.T) {
	withXDG(t)

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	if err := runJentic(t, "env", "add", "local", "--url", srv.URL); err != nil {
		t.Fatalf("env add: %v", err)
	}
	if err := runJentic(t, "identity", "add", "agent1", "--type", "agent"); err != nil {
		t.Fatalf("identity add: %v", err)
	}
	if err := runJentic(t, "context", "create", "c1", "--env", "local", "--identity", "agent1"); err != nil {
		t.Fatalf("context create: %v", err)
	}
	if err := runJentic(t, "context", "use", "c1"); err != nil {
		t.Fatalf("context use: %v", err)
	}

	err := runJentic(t, "access", "whoami")
	if err == nil {
		t.Fatal("expected whoami to fail for an unregistered context identity")
	}
	if !strings.Contains(err.Error(), "jentic register") {
		t.Errorf("error = %v, want the onboarding remediation (`jentic register`)", err)
	}
}

// TestLogout_ContextClearsXDGTokens: with a V2 context active, logout must
// revoke and drop the XDG-stored token for the active (identity, environment) —
// not touch (or invent) a legacy profile.
func TestLogout_ContextClearsXDGTokens(t *testing.T) {
	withXDG(t)

	var revoked bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/revoke") {
			revoked = true
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	if err := runJentic(t, "env", "add", "local", "--url", srv.URL); err != nil {
		t.Fatalf("env add: %v", err)
	}
	if err := runJentic(t, "identity", "add", "agent1", "--type", "agent"); err != nil {
		t.Fatalf("identity add: %v", err)
	}
	if err := runJentic(t, "context", "create", "c1", "--env", "local", "--identity", "agent1"); err != nil {
		t.Fatalf("context create: %v", err)
	}
	if err := runJentic(t, "context", "use", "c1"); err != nil {
		t.Fatalf("context use: %v", err)
	}

	ref := auth.IdentityRef{Identity: "agent1", Environment: "local"}
	if err := auth.SaveTokens(ref, &auth.TokenSet{
		AccessToken: "tok_ctx", ExpiresAt: time.Now().Add(time.Hour),
	}); err != nil {
		t.Fatalf("seed tokens: %v", err)
	}

	if err := runJentic(t, "logout"); err != nil {
		t.Fatalf("logout via context: %v", err)
	}
	if !revoked {
		t.Error("logout did not attempt server-side revoke against the context env URL")
	}
	if tokens, err := auth.ReadTokens(ref); err == nil && tokens != nil && tokens.AccessToken != "" {
		t.Error("XDG token survived logout")
	}
}
