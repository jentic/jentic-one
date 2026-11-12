package auth

import (
	"crypto/ed25519"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
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
