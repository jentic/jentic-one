package auth

import (
	"crypto/ed25519"
	"encoding/base64"
	"net/url"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/go-jose/go-jose/v4"
	"github.com/go-jose/go-jose/v4/jwt"
)

// withConfigDir points the XDG config/state dirs at a temp dir so key/token files
// land in an isolated location.
func withConfigDir(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(dir, "config"))
	t.Setenv("XDG_STATE_HOME", filepath.Join(dir, "state"))
}

func TestPublicKeyToJWKS_Shape(t *testing.T) {
	pub, _, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	jwks := PublicKeyToJWKS(pub)
	if len(jwks.Keys) != 1 {
		t.Fatalf("expected 1 key, got %d", len(jwks.Keys))
	}
	j := jwks.Keys[0]
	if j.Kty != "OKP" || j.Crv != "Ed25519" || j.Alg != "EdDSA" || j.Use != "sig" {
		t.Fatalf("unexpected JWK fields: %+v", j)
	}
	raw, err := base64.RawURLEncoding.DecodeString(j.X)
	if err != nil {
		t.Fatalf("decode x: %v", err)
	}
	if len(raw) != ed25519.PublicKeySize {
		t.Fatalf("public key size = %d, want %d", len(raw), ed25519.PublicKeySize)
	}
}

// TestSignJWTAssertion_Verifiable mirrors the shipped agentkey test: the go-jose
// assertion must verify with the public key, carry alg=EdDSA, and set iss/sub/aud
// + a jti. This proves the library swap preserved the wire contract.
func TestSignJWTAssertion_Verifiable(t *testing.T) {
	pub, priv, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	const (
		clientID = "client_abc"
		aud      = "http://127.0.0.1:8000/oauth/token"
	)
	assertion, err := signJWTAssertion(priv, clientID, aud)
	if err != nil {
		t.Fatalf("signJWTAssertion: %v", err)
	}

	parsed, err := jwt.ParseSigned(assertion, []jose.SignatureAlgorithm{jose.EdDSA})
	if err != nil {
		t.Fatalf("ParseSigned: %v", err)
	}
	var claims jwt.Claims
	if err := parsed.Claims(pub, &claims); err != nil {
		t.Fatalf("verify claims: %v", err)
	}
	if claims.Issuer != clientID || claims.Subject != clientID {
		t.Errorf("iss/sub = %q/%q, want %s", claims.Issuer, claims.Subject, clientID)
	}
	if len(claims.Audience) != 1 || claims.Audience[0] != aud {
		t.Errorf("aud = %v, want [%s]", claims.Audience, aud)
	}
	if claims.ID == "" {
		t.Error("missing jti")
	}
	// TTL must be the 270s cap-safe value, not the old 120s.
	gotTTL := claims.Expiry.Time().Sub(claims.IssuedAt.Time())
	if gotTTL != assertionTTL {
		t.Errorf("assertion TTL = %v, want %v", gotTTL, assertionTTL)
	}
}

func TestGetOrGenerateKey_PersistsAndReloads(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "my-agent", Environment: "prod"}

	k1, err := GetOrGenerateKey(ref)
	if err != nil {
		t.Fatalf("first GetOrGenerateKey: %v", err)
	}

	stem, _ := ref.Stem()
	// Verify perms on the written key file.
	xdg := os.Getenv("XDG_CONFIG_HOME")
	keyPath := filepath.Join(xdg, "jentic", "keys", stem+".key")
	info, err := os.Stat(keyPath)
	if err != nil {
		t.Fatalf("stat key: %v", err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Errorf("key perms = %o, want 600", perm)
	}

	k2, err := GetOrGenerateKey(ref)
	if err != nil {
		t.Fatalf("second GetOrGenerateKey: %v", err)
	}
	if !k1.Equal(k2) {
		t.Error("reloaded key differs from generated key")
	}
}

// TestStem_PathTraversalGuard: names with separators/dots/traversal must be
// rejected fail-closed, since config.yaml is user-editable.
func TestStem_PathTraversalGuard(t *testing.T) {
	bad := []IdentityRef{
		{Identity: "../../etc", Environment: "prod"},
		{Identity: "ok", Environment: "../escape"},
		{Identity: "has_underscore", Environment: "prod"},
		{Identity: "has.dot", Environment: "prod"},
		{Identity: "jentic.file-less-agent", Environment: "jentic.ephemeral"},
		{Identity: "", Environment: "prod"},
	}
	for _, ref := range bad {
		if _, err := ref.Stem(); err == nil {
			t.Errorf("Stem(%+v) = nil error, want rejection", ref)
		}
	}
	good := IdentityRef{Identity: "my-agent", Environment: "prod-1"}
	stem, err := good.Stem()
	if err != nil || stem != "my-agent_prod-1" {
		t.Errorf("Stem(%+v) = %q, %v; want my-agent_prod-1, nil", good, stem, err)
	}
}

func TestReadTokens_MissingAndRoundTrip(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "a", Environment: "e"}

	if _, err := ReadTokens(ref); err == nil {
		t.Error("ReadTokens on missing file should error (treated as no token)")
	}

	want := &TokenSet{AccessToken: "tok-1", ExpiresAt: time.Now().Add(time.Hour).Round(time.Second)}
	if err := SaveTokens(ref, want); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}
	got, err := ReadTokens(ref)
	if err != nil {
		t.Fatalf("ReadTokens: %v", err)
	}
	if got.AccessToken != want.AccessToken || !got.ExpiresAt.Equal(want.ExpiresAt) {
		t.Errorf("round-trip mismatch: got %+v want %+v", got, want)
	}
}

// TestRenewalDeadline_ClampsSkew guards against the exchange-storm failure (F4):
// short/zero/negative lifetimes must still yield a deadline in the future.
func TestRenewalDeadline_ClampsSkew(t *testing.T) {
	now := time.Now()
	cases := []struct {
		name      string
		expiresIn int
	}{
		{"normal 3600s", 3600},
		{"short 10s", 10},
		{"tiny 1s", 1},
		{"zero", 0},
		{"negative", -5},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := renewalDeadline(now, tc.expiresIn)
			if !got.After(now) {
				t.Errorf("deadline %v is not after now %v (would trigger exchange storm)", got, now)
			}
		})
	}
	// A normal token renews ~30s early.
	d := renewalDeadline(now, 3600)
	if lifetime := d.Sub(now); lifetime < 3500*time.Second || lifetime > 3600*time.Second {
		t.Errorf("3600s token deadline %v out of expected renew-early band", lifetime)
	}
}

func TestRequireSecureHost(t *testing.T) {
	ok := []string{"https://api.example.com", "http://localhost:8000", "http://127.0.0.1:9000", "http://[::1]:8080"}
	bad := []string{"http://api.example.com", "http://evil.example", "ftp://x"}
	for _, s := range ok {
		u, _ := url.Parse(s)
		if err := requireSecureHost(u); err != nil {
			t.Errorf("requireSecureHost(%q) = %v, want nil", s, err)
		}
	}
	for _, s := range bad {
		u, _ := url.Parse(s)
		if err := requireSecureHost(u); err == nil {
			t.Errorf("requireSecureHost(%q) = nil, want rejection", s)
		}
	}
}

func TestRequireSecureURL(t *testing.T) {
	ok := []string{"https://api.example.com/agents", "http://localhost:8000/x", "http://127.0.0.1:8100/broker"}
	bad := []string{"http://api.example.com/x", "http://10.0.0.5:8100/y", "://malformed"}
	for _, s := range ok {
		if err := RequireSecureURL(s); err != nil {
			t.Errorf("RequireSecureURL(%q) = %v, want nil", s, err)
		}
	}
	for _, s := range bad {
		if err := RequireSecureURL(s); err == nil {
			t.Errorf("RequireSecureURL(%q) = nil, want rejection", s)
		}
	}
}

func TestTokenEndpoint(t *testing.T) {
	got, err := tokenEndpoint("https://ctl.example/")
	if err != nil {
		t.Fatal(err)
	}
	if got != "https://ctl.example/oauth/token" {
		t.Errorf("tokenEndpoint = %q", got)
	}
	if _, err := tokenEndpoint("http://evil.example"); err == nil {
		t.Error("tokenEndpoint on insecure host should error")
	}
}
