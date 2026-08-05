package agentauth

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/authclient"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/profile"
)

// openSession opens a session against baseURL for a fresh temp profile with a
// registered agent id so token operations are allowed.
func openSession(t *testing.T, baseURL string) *Session {
	t.Helper()
	paths := config.Paths{Root: t.TempDir()}
	sess, err := Open(paths, "default", baseURL)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	sess.Meta.AgentID = "agnt_test"
	if err := sess.Profile.SaveMeta(sess.Meta); err != nil {
		t.Fatalf("SaveMeta: %v", err)
	}
	return sess
}

func TestOpenGeneratesKeyAndDefaults(t *testing.T) {
	paths := config.Paths{Root: t.TempDir()}
	sess, err := Open(paths, "work", "http://base:8000")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	if sess.Meta.BaseURL != "http://base:8000" {
		t.Errorf("BaseURL = %q", sess.Meta.BaseURL)
	}
	if sess.Meta.KID != "jentic-cli-work" {
		t.Errorf("KID = %q", sess.Meta.KID)
	}
	if sess.Key == nil || sess.Key.Priv == nil {
		t.Errorf("expected generated key")
	}
}

func TestMintFreshRequiresRegistration(t *testing.T) {
	paths := config.Paths{Root: t.TempDir()}
	sess, _ := Open(paths, "default", "http://base:8000")
	if _, err := sess.MintFresh(context.Background()); !errors.Is(err, ErrNotRegistered) {
		t.Fatalf("expected ErrNotRegistered, got %v", err)
	}
}

func TestOpenAPIKeyProfileLoadsKey(t *testing.T) {
	paths := config.Paths{Root: t.TempDir()}
	p, err := profile.Open(paths, "keyed")
	if err != nil {
		t.Fatalf("profile.Open: %v", err)
	}
	if err := p.SaveMeta(&profile.Meta{BaseURL: "http://base:8000", AuthMode: profile.AuthModeAPIKey}); err != nil {
		t.Fatalf("SaveMeta: %v", err)
	}
	if err := p.SaveAPIKey("jak_abc123"); err != nil {
		t.Fatalf("SaveAPIKey: %v", err)
	}

	sess, err := Open(paths, "keyed", "http://base:8000")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	if sess.Key != nil {
		t.Errorf("API-key profile should not generate an Ed25519 key")
	}
	if sess.APIKey != "jak_abc123" {
		t.Errorf("APIKey = %q, want jak_abc123", sess.APIKey)
	}

	// ValidToken returns the stored key directly with no network call.
	got, err := sess.ValidToken(context.Background())
	if err != nil {
		t.Fatalf("ValidToken: %v", err)
	}
	if got != "jak_abc123" {
		t.Fatalf("ValidToken = %q, want jak_abc123", got)
	}
}

func TestValidTokenAPIKeyMissing(t *testing.T) {
	paths := config.Paths{Root: t.TempDir()}
	p, err := profile.Open(paths, "empty")
	if err != nil {
		t.Fatalf("profile.Open: %v", err)
	}
	if err := p.SaveMeta(&profile.Meta{BaseURL: "http://base:8000", AuthMode: profile.AuthModeAPIKey}); err != nil {
		t.Fatalf("SaveMeta: %v", err)
	}
	sess, err := Open(paths, "empty", "http://base:8000")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	if _, err := sess.ValidToken(context.Background()); !errors.Is(err, ErrNoAPIKey) {
		t.Fatalf("expected ErrNoAPIKey, got %v", err)
	}
}

func TestValidTokenUsesCachedToken(t *testing.T) {
	sess := openSession(t, "http://unused")
	if err := sess.Profile.SaveTokens(&profile.Tokens{
		AccessToken:     "cached",
		AccessExpiresAt: time.Now().Add(time.Hour),
	}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}
	got, err := sess.ValidToken(context.Background())
	if err != nil {
		t.Fatalf("ValidToken: %v", err)
	}
	if got != "cached" {
		t.Fatalf("token = %q, want cached", got)
	}
}

func TestValidTokenRefreshes(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["grant_type"] != "refresh_token" {
			t.Errorf("expected refresh_token grant, got %q", body["grant_type"])
		}
		_ = json.NewEncoder(w).Encode(authclient.TokenPair{AccessToken: "refreshed", RefreshToken: "rt2", ExpiresIn: 3600})
	}))
	defer srv.Close()

	sess := openSession(t, srv.URL)
	if err := sess.Profile.SaveTokens(&profile.Tokens{
		AccessToken:     "expired",
		RefreshToken:    "rt1",
		AccessExpiresAt: time.Now().Add(-time.Hour),
	}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}

	got, err := sess.ValidToken(context.Background())
	if err != nil {
		t.Fatalf("ValidToken: %v", err)
	}
	if got != "refreshed" {
		t.Fatalf("token = %q, want refreshed", got)
	}
}

func TestValidTokenMintsWhenRefreshFails(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		switch body["grant_type"] {
		case "refresh_token":
			calls.Add(1)
			w.WriteHeader(http.StatusBadRequest) // refresh rejected
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid_grant"})
		default: // jwt-bearer mint
			_ = json.NewEncoder(w).Encode(authclient.TokenPair{AccessToken: "minted", ExpiresIn: 3600})
		}
	}))
	defer srv.Close()

	sess := openSession(t, srv.URL)
	if err := sess.Profile.SaveTokens(&profile.Tokens{
		AccessToken:     "expired",
		RefreshToken:    "rt-bad",
		AccessExpiresAt: time.Now().Add(-time.Hour),
	}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}

	got, err := sess.ValidToken(context.Background())
	if err != nil {
		t.Fatalf("ValidToken: %v", err)
	}
	if got != "minted" {
		t.Fatalf("token = %q, want minted", got)
	}
	if calls.Load() == 0 {
		t.Fatalf("expected a refresh attempt before minting")
	}
}

// mintAndMeServer returns a test server that mints a token on /oauth/token and
// answers /me with the given identity body (nil = 500, so /me is "unreachable").
func mintAndMeServer(t *testing.T, meBody map[string]any) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/me" {
			if meBody == nil {
				w.WriteHeader(http.StatusInternalServerError)
				return
			}
			_ = json.NewEncoder(w).Encode(meBody)
			return
		}
		_ = json.NewEncoder(w).Encode(authclient.TokenPair{AccessToken: "minted", RefreshToken: "rt", ExpiresIn: 3600})
	}))
}

// TestMintFreshRejectsIdentityMismatch proves a token whose /me resolves to a
// DIFFERENT agent id is discarded (ErrIdentityMismatch) and never persisted.
func TestMintFreshRejectsIdentityMismatch(t *testing.T) {
	srv := mintAndMeServer(t, map[string]any{"sub": "agnt_someone_else"})
	defer srv.Close()

	sess := openSession(t, srv.URL) // AgentID = agnt_test
	_, err := sess.MintFresh(context.Background())
	if !errors.Is(err, ErrIdentityMismatch) {
		t.Fatalf("expected ErrIdentityMismatch, got %v", err)
	}
	// The mismatched token must NOT have been saved.
	if tok, _ := sess.Profile.LoadTokens(); tok != nil && tok.AccessToken != "" {
		t.Errorf("mismatched token was persisted: %q", tok.AccessToken)
	}
}

// TestMintFreshAcceptsMatchingIdentity proves a token whose /me resolves to our
// own agent id is persisted normally.
func TestMintFreshAcceptsMatchingIdentity(t *testing.T) {
	srv := mintAndMeServer(t, map[string]any{"sub": "agnt_test"})
	defer srv.Close()

	sess := openSession(t, srv.URL)
	tok, err := sess.MintFresh(context.Background())
	if err != nil {
		t.Fatalf("MintFresh: %v", err)
	}
	if tok.AccessToken != "minted" {
		t.Fatalf("token = %q, want minted", tok.AccessToken)
	}
}

// TestMintFreshAllowsWhenMeUnreachable proves the identity check is best-effort:
// a failing /me does not block minting (the token is still persisted).
func TestMintFreshAllowsWhenMeUnreachable(t *testing.T) {
	srv := mintAndMeServer(t, nil) // /me returns 500
	defer srv.Close()

	sess := openSession(t, srv.URL)
	tok, err := sess.MintFresh(context.Background())
	if err != nil {
		t.Fatalf("MintFresh: %v", err)
	}
	if tok.AccessToken != "minted" {
		t.Fatalf("token = %q, want minted", tok.AccessToken)
	}
}

func TestMintFreshPersistsAndReportsPending(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid_grant", "error_description": "pending"})
	}))
	defer srv.Close()

	sess := openSession(t, srv.URL)
	_, err := sess.MintFresh(context.Background())
	var pending *authclient.PendingError
	if !errors.As(err, &pending) {
		t.Fatalf("expected *PendingError, got %v", err)
	}
}
