package auth

import (
	"crypto/ed25519"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/config"
)

func seedEnvironmentBaseURL(t *testing.T, envName, baseURL string) {
	t.Helper()
	if err := config.MutateConfig(func(c *config.Config) error {
		c.Environments[envName] = config.Env{BaseURL: baseURL}
		return nil
	}); err != nil {
		t.Fatalf("seed environment %s: %v", envName, err)
	}
}

func TestTokens_BoundToOriginAndRejectedWhenRepointed(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "agent1", Environment: "prod"}
	seedEnvironmentBaseURL(t, "prod", "https://origin-a.example.com/v1")

	if err := SaveTokens(ref, &TokenSet{
		AccessToken: "tok_origin_a",
		ExpiresAt:   time.Now().Add(time.Hour),
	}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}

	// Same origin (even with a different path) succeeds.
	seedEnvironmentBaseURL(t, "prod", "https://origin-a.example.com:443/v2")
	got, err := ReadTokens(ref)
	if err != nil {
		t.Fatalf("ReadTokens on same origin: %v", err)
	}
	if got.AccessToken != "tok_origin_a" || got.Origin != "https://origin-a.example.com" {
		t.Fatalf("unexpected TokenSet: %+v", got)
	}

	// Repointing the environment to origin B rejects the cached token from origin A.
	seedEnvironmentBaseURL(t, "prod", "https://origin-b.example.com")
	if _, err := ReadTokens(ref); !errors.Is(err, ErrOriginMismatch) {
		t.Fatalf("ReadTokens after repoint = %v, want ErrOriginMismatch", err)
	}

	// BearerToken also invalidates the cross-origin token rather than returning it.
	_, err = BearerToken(Credentials{
		BaseURL:         "https://origin-b.example.com",
		IdentityName:    "agent1",
		EnvironmentName: "prod",
	})
	if err == nil {
		t.Fatal("BearerToken must not return origin A's cached token for origin B")
	}
	if _, rawErr := readTokensRaw(ref); rawErr == nil {
		t.Fatal("cross-origin cached token should have been invalidated on disk")
	}
}

func TestAPIKey_BoundToOriginAndRejectedWhenRepointed(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "ci", Environment: "prod"}
	seedEnvironmentBaseURL(t, "prod", "https://origin-a.example.com")

	if err := SaveAPIKey(ref, "jak_secret_origin_a"); err != nil {
		t.Fatalf("SaveAPIKey: %v", err)
	}

	// Same origin succeeds.
	got, err := ReadAPIKey(ref)
	if err != nil || got != "jak_secret_origin_a" {
		t.Fatalf("ReadAPIKey = %q, %v; want jak_secret_origin_a", got, err)
	}

	// Repointing the environment to origin B causes ReadAPIKey and AttachAuth to fail closed.
	seedEnvironmentBaseURL(t, "prod", "https://origin-b.example.com")
	if _, err := ReadAPIKey(ref); !errors.Is(err, ErrOriginMismatch) {
		t.Fatalf("ReadAPIKey after repoint = %v, want ErrOriginMismatch", err)
	}

	req, _ := http.NewRequest(http.MethodGet, "https://origin-b.example.com/apis", nil)
	err = AttachAuth(Credentials{
		BaseURL:         "https://origin-b.example.com",
		IdentityName:    "ci",
		EnvironmentName: "prod",
	}, req)
	if !errors.Is(err, ErrOriginMismatch) {
		t.Fatalf("AttachAuth after repoint = %v, want ErrOriginMismatch", err)
	}
	if req.Header.Get("Authorization") != "" {
		t.Fatalf("Authorization header must not be attached on origin mismatch, got %q", req.Header.Get("Authorization"))
	}
}

func TestBearerToken_ExplicitBaseURLIsIndependentOfAmbientEnvironment(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "agent1", Environment: "prod"}
	seedEnvironmentBaseURL(t, "prod", "https://ambient-a.example.com")
	if err := SaveTokens(ref, &TokenSet{
		AccessToken: "tok_explicit_b",
		ExpiresAt:   time.Now().Add(time.Hour),
		Origin:      "https://explicit-b.example.com",
	}); err != nil {
		t.Fatal(err)
	}
	got, err := BearerToken(Credentials{
		BaseURL:         "https://explicit-b.example.com",
		IdentityName:    "agent1",
		EnvironmentName: "prod",
	})
	if err != nil || got != "tok_explicit_b" {
		t.Fatalf("explicit BaseURL credential resolution = %q, %v", got, err)
	}
}

func TestOriginBinding_ConfigErrorsFailClosed(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "agent1", Environment: "prod"}
	seedEnvironmentBaseURL(t, "prod", "https://origin-a.example.com")
	if err := SaveAPIKey(ref, "jak_config_error_test"); err != nil {
		t.Fatal(err)
	}
	if err := SaveTokens(ref, &TokenSet{AccessToken: "tok_config_error", ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatal(err)
	}
	configDir, err := config.ConfigDir()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "config.yaml"), []byte("environments: ["), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadAPIKey(ref); err == nil {
		t.Fatal("bound API key must not be read when its configured origin cannot be loaded")
	}
	if _, err := ReadTokens(ref); err == nil {
		t.Fatal("bound token must not be read when its configured origin cannot be loaded")
	}
	if err := SaveAPIKey(IdentityRef{Identity: "other", Environment: "prod"}, "jak_unbound_test"); err == nil {
		t.Fatal("SaveAPIKey must not write an origin-less key when config loading fails")
	}
	if err := SaveTokens(IdentityRef{Identity: "other", Environment: "prod"}, &TokenSet{AccessToken: "tok_unbound"}); err == nil {
		t.Fatal("SaveTokens must not write an origin-less token when config loading fails")
	}
}

func TestOriginBinding_MalformedStoredOriginsFailClosed(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "agent1", Environment: "prod"}
	seedEnvironmentBaseURL(t, "prod", "https://origin-b.example.com")

	keyPath, err := apiKeyPath(ref)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(keyPath, []byte(`{"api_key":"jak_malformed_origin_test","origin":"://bad"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := BearerToken(Credentials{
		BaseURL:         "https://origin-b.example.com",
		IdentityName:    ref.Identity,
		EnvironmentName: ref.Environment,
	}); err == nil || !strings.Contains(err.Error(), "invalid API key origin") {
		t.Fatalf("malformed API-key origin fell through to another credential: %v", err)
	}

	tokenPath, err := getTokenPath(ref)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(tokenPath, []byte(`{"access_token":"tok_malformed_origin","expires_at":"2099-01-01T00:00:00Z","origin":"://bad"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadTokens(ref); err == nil || !strings.Contains(err.Error(), "invalid token origin") {
		t.Fatalf("malformed token origin was treated as an unbound token: %v", err)
	}
}

func TestBearerToken_APIKeyReadFailureDoesNotFallBackToOAuth(t *testing.T) {
	tests := []struct {
		name    string
		content string
		wantErr string
	}{
		{name: "corrupt envelope", content: `{"api_key":`, wantErr: "decoding API key"},
		{name: "empty bare key", content: "", wantErr: "is empty"},
		{name: "empty envelope key", content: `{}`, wantErr: "is empty"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			withConfigDir(t)
			ref := IdentityRef{Identity: "agent1", Environment: "prod"}
			seedEnvironmentBaseURL(t, "prod", "https://origin-b.example.com")
			keyPath, err := apiKeyPath(ref)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(keyPath, []byte(tt.content), 0o600); err != nil {
				t.Fatal(err)
			}
			creds := Credentials{
				BaseURL:         "https://origin-b.example.com",
				IdentityName:    ref.Identity,
				EnvironmentName: ref.Environment,
			}
			if _, err := BearerToken(creds); err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Fatalf("invalid API-key state must surface its read error, got %v", err)
			}
			if _, err := ReadAPIKey(ref); err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Fatalf("direct API-key read must surface invalid state, got %v", err)
			}
			if CanReExchange(creds) {
				t.Fatal("invalid API-key state must not permit an OAuth exchange retry")
			}
		})
	}
}

func TestSaveTokens_RejectsInvalidExplicitOrigin(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "agent1", Environment: "prod"}
	seedEnvironmentBaseURL(t, "prod", "https://origin-a.example.com")
	if err := SaveTokens(ref, &TokenSet{
		AccessToken: "tok_invalid_origin",
		ExpiresAt:   time.Now().Add(time.Hour),
		Origin:      "://bad",
	}); err == nil {
		t.Fatal("an invalid explicit token origin must not fall back to the configured environment")
	}
}

func TestSaveTokens_NilReturnsError(t *testing.T) {
	withConfigDir(t)
	if err := SaveTokens(IdentityRef{Identity: "agent1", Environment: "prod"}, nil); err == nil {
		t.Fatal("SaveTokens(nil) must return an error")
	}
}

func TestRegisterAndRevokeWithClient_HonorsCustomCAAndFailsClosedWithoutIt(t *testing.T) {
	var sawRegisterUA, sawRevokeUA, sawRevokeBearer, sawRevokeToken string
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/register":
			sawRegisterUA = r.Header.Get("User-Agent")
			w.WriteHeader(http.StatusCreated)
			_ = json.NewEncoder(w).Encode(map[string]string{
				"client_id": "client_tls_ca",
				"status":    "approved",
			})
		case "/oauth/revoke":
			sawRevokeUA = r.Header.Get("User-Agent")
			sawRevokeBearer = r.Header.Get("Authorization")
			var body struct {
				Token string `json:"token"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			sawRevokeToken = body.Token
			w.WriteHeader(http.StatusOK)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	pub, _, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	jwks := PublicKeyToJWKS(pub)

	// 1. Default client (untrusted CA) must fail closed for both RegisterWithClient and RevokeTokenWithClient.
	if _, err := RegisterWithClient(nil, srv.URL, "agent-tls", jwks); err == nil {
		t.Fatal("RegisterWithClient with nil client must fail against untrusted custom CA")
	}
	if err := RevokeTokenWithClient(nil, srv.URL, "tok_access", "tok_access"); err == nil {
		t.Fatal("RevokeTokenWithClient with nil client must fail against untrusted custom CA")
	}

	// Wrong custom CA pool must also fail closed.
	emptyPool := x509.NewCertPool()
	untrustedClient := &http.Client{
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{RootCAs: emptyPool, MinVersion: tls.VersionTLS12},
		},
	}
	if _, err := RegisterWithClient(untrustedClient, srv.URL, "agent-tls", jwks); err == nil {
		t.Fatal("RegisterWithClient with untrusted CA pool must fail closed")
	}
	if err := RevokeTokenWithClient(untrustedClient, srv.URL, "tok_access", "tok_access"); err == nil {
		t.Fatal("RevokeTokenWithClient with untrusted CA pool must fail closed")
	}

	// 2. Custom-CA client (plus attribution transport) succeeds for both DCR and token revocation.
	pool := x509.NewCertPool()
	pool.AddCert(srv.Certificate())
	trustedClient := &http.Client{
		Transport: headerStampingTransport{
			base:      &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}},
			userAgent: "jentic-cli/custom-ca",
		},
	}

	reg, err := RegisterWithClient(trustedClient, srv.URL, "agent-tls", jwks)
	if err != nil {
		t.Fatalf("RegisterWithClient with custom CA: %v", err)
	}
	if reg.ClientID != "client_tls_ca" || reg.Status != "approved" {
		t.Errorf("registration result = %+v, want client_tls_ca/approved", reg)
	}
	if sawRegisterUA != "jentic-cli/custom-ca" {
		t.Errorf("register User-Agent = %q, want jentic-cli/custom-ca", sawRegisterUA)
	}

	if err := RevokeTokenWithClient(trustedClient, srv.URL, "tok_access", "tok_access"); err != nil {
		t.Fatalf("RevokeTokenWithClient with custom CA: %v", err)
	}
	if sawRevokeUA != "jentic-cli/custom-ca" {
		t.Errorf("revoke User-Agent = %q, want jentic-cli/custom-ca", sawRevokeUA)
	}
	if sawRevokeBearer != "Bearer tok_access" || sawRevokeToken != "tok_access" {
		t.Errorf("revoke auth=%q token=%q, want Bearer tok_access / tok_access", sawRevokeBearer, sawRevokeToken)
	}
}
