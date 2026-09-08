package auth

import (
	"crypto/ed25519"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestAttachAuth_APIKeyTakesPrecedence proves the Phase 4 item-4 branch: when a
// jak_* credential is stored for the ref, AttachAuth sends it verbatim as
// `Authorization: Bearer jak_...` and does NOT reach the OAuth exchange path.
func TestAttachAuth_APIKeyTakesPrecedence(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "ci", Environment: "prod"}
	const key = "jak_secretkeyvalue"
	if err := SaveAPIKey(ref, key); err != nil {
		t.Fatalf("SaveAPIKey: %v", err)
	}

	creds := Credentials{
		BaseURL:         "https://ctl.example",
		IdentityName:    "ci",
		EnvironmentName: "prod",
	}
	req, _ := http.NewRequest(http.MethodGet, "https://ctl.example/toolkits", nil)
	if err := AttachAuth(creds, req); err != nil {
		t.Fatalf("AttachAuth: %v", err)
	}
	if got := req.Header.Get("Authorization"); got != "Bearer "+key {
		t.Errorf("Authorization = %q, want Bearer %s", got, key)
	}
}

// TestAttachAuth_InjectedBeatsAPIKey: a file-less injected token wins over any
// on-disk API key (the orchestrator's intent is explicit).
func TestAttachAuth_InjectedBeatsAPIKey(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "ci", Environment: "prod"}
	if err := SaveAPIKey(ref, "jak_ondiskkey"); err != nil {
		t.Fatalf("SaveAPIKey: %v", err)
	}
	creds := Credentials{
		BaseURL:             "https://ctl.example",
		IdentityName:        "ci",
		EnvironmentName:     "prod",
		InjectedBearerToken: "at_injected",
	}
	req, _ := http.NewRequest(http.MethodGet, "https://ctl.example/toolkits", nil)
	if err := AttachAuth(creds, req); err != nil {
		t.Fatalf("AttachAuth: %v", err)
	}
	if got := req.Header.Get("Authorization"); got != "Bearer at_injected" {
		t.Errorf("Authorization = %q, want Bearer at_injected", got)
	}
}

// TestAttachAuth_TransportGuard: an insecure, non-loopback target is refused
// before any credential is attached (F3).
func TestAttachAuth_TransportGuard(t *testing.T) {
	withConfigDir(t)
	creds := Credentials{InjectedBearerToken: "at_x"}
	req, _ := http.NewRequest(http.MethodGet, "http://evil.example/x", nil)
	if err := AttachAuth(creds, req); err == nil {
		t.Fatal("expected AttachAuth to refuse an insecure host")
	}
	if req.Header.Get("Authorization") != "" {
		t.Error("credential attached to an insecure host")
	}
}

// TestCanReExchange: only the exchange-backed disk path is re-exchangeable; an
// injected token or an API key is a fixed credential (a 401 is a hard denial).
func TestCanReExchange(t *testing.T) {
	withConfigDir(t)

	disk := Credentials{IdentityName: "a", EnvironmentName: "e"}
	if !CanReExchange(disk) {
		t.Error("disk-path creds should be re-exchangeable")
	}

	injected := Credentials{InjectedBearerToken: "at_x"}
	if CanReExchange(injected) {
		t.Error("injected token must NOT be re-exchangeable")
	}

	ref := IdentityRef{Identity: "k", Environment: "e"}
	if err := SaveAPIKey(ref, "jak_key"); err != nil {
		t.Fatalf("SaveAPIKey: %v", err)
	}
	apiKey := Credentials{IdentityName: "k", EnvironmentName: "e"}
	if CanReExchange(apiKey) {
		t.Error("API-key creds must NOT be re-exchangeable")
	}
}

// TestInvalidateTokens_RemovesAndTolerant: invalidation deletes the cache and is a
// no-op (nil error) when there is nothing to remove.
func TestInvalidateTokens_RemovesAndTolerant(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "a", Environment: "e"}

	if err := InvalidateTokens(ref); err != nil {
		t.Errorf("InvalidateTokens on missing file = %v, want nil", err)
	}

	if err := SaveTokens(ref, &TokenSet{AccessToken: "tok"}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}
	if err := InvalidateTokens(ref); err != nil {
		t.Fatalf("InvalidateTokens: %v", err)
	}
	if _, err := ReadTokens(ref); err == nil {
		t.Error("token still present after InvalidateTokens")
	}
}

// TestRegister_PostsJWKSAndParsesResult drives the RFC 7591 registration against a
// mock /register: it asserts the JSON body shape (client_name + jwks) and that a
// 201 client_id/status is parsed.
func TestRegister_PostsJWKSAndParsesResult(t *testing.T) {
	var gotPath string
	var body struct {
		ClientName string `json:"client_name"`
		Jwks       JWKS   `json:"jwks"`
	}
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&body)
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(map[string]string{"client_id": "client_123", "status": "pending"})
	}))
	defer srv.Close()

	// httptest TLS uses a self-signed cert; point the package client at the
	// server's own client so the handshake verifies.
	old := httpClient
	httpClient = srv.Client()
	defer func() { httpClient = old }()

	pub, _, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	res, err := Register(srv.URL, "my-agent", PublicKeyToJWKS(pub))
	if err != nil {
		t.Fatalf("Register: %v", err)
	}
	if gotPath != "/register" {
		t.Errorf("path = %q, want /register", gotPath)
	}
	if body.ClientName != "my-agent" || len(body.Jwks.Keys) != 1 {
		t.Errorf("unexpected request body: %+v", body)
	}
	if res.ClientID != "client_123" || res.Status != "pending" {
		t.Errorf("result = %+v, want client_123/pending", res)
	}
}

// TestRegister_RefusesInsecureHost: registration publishes state + returns a
// client_id we sign as, so it holds the same TLS guard as the token endpoint.
func TestRegister_RefusesInsecureHost(t *testing.T) {
	pub, _, _ := ed25519.GenerateKey(nil)
	if _, err := Register("http://evil.example", "a", PublicKeyToJWKS(pub)); err == nil {
		t.Error("Register on an insecure host should be refused")
	}
}

// TestRegister_Non201IsError: a non-201 surfaces the problem detail.
func TestRegister_Non201IsError(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(w).Encode(map[string]string{"detail": "already registered"})
	}))
	defer srv.Close()
	old := httpClient
	httpClient = srv.Client()
	defer func() { httpClient = old }()

	pub, _, _ := ed25519.GenerateKey(nil)
	_, err := Register(srv.URL, "a", PublicKeyToJWKS(pub))
	if err == nil {
		t.Fatal("expected an error for a non-201 registration")
	}
}
