package install

import (
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"testing"
)

func TestFillSecretsPopulatesUniqueValues(t *testing.T) {
	d := NewDraft()
	if err := d.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}

	secrets := []string{d.EncryptionKey, d.AdminJWTSecret, d.AdminInvitePepper, d.ConnectStateSecret}
	seen := map[string]bool{}
	for _, s := range secrets {
		if s == "" {
			t.Fatalf("secret not populated")
		}
		raw, err := base64.StdEncoding.DecodeString(s)
		if err != nil {
			t.Fatalf("secret %q not base64: %v", s, err)
		}
		if len(raw) != 32 {
			t.Errorf("secret length = %d bytes, want 32", len(raw))
		}
		if seen[s] {
			t.Errorf("secrets should be unique; %q repeated", s)
		}
		seen[s] = true
	}
}

func TestFillSecretsPreservesPreSeededValues(t *testing.T) {
	// Reuse (see reuse.go) pre-seeds the draft with the existing config's
	// secrets before FillSecrets runs. Those fields must survive so a
	// reinstall over live data doesn't rotate the encryption key underneath
	// stored ciphertexts.
	d := NewDraft()
	d.EncryptionKey = "preserved-encryption-key"
	d.AdminJWTSecret = "preserved-jwt-secret"
	// AdminInvitePepper deliberately left blank — it must still get filled.

	if err := d.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}

	if d.EncryptionKey != "preserved-encryption-key" {
		t.Errorf("EncryptionKey rotated: got %q", d.EncryptionKey)
	}
	if d.AdminJWTSecret != "preserved-jwt-secret" {
		t.Errorf("AdminJWTSecret rotated: got %q", d.AdminJWTSecret)
	}
	if d.AdminInvitePepper == "" {
		t.Errorf("AdminInvitePepper was blank and should have been filled")
	}
	if d.ConnectStateSecret == "" {
		t.Errorf("ConnectStateSecret was blank and should have been filled")
	}
}

func TestFillSecretsGeneratesManagedPGPassword(t *testing.T) {
	// Docker + Postgres: the managed container's password is machine-managed,
	// so a blank one gets a generated random credential (#992 — the old
	// "postgres" default was guessable on a database that could be exposed).
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres
	if err := d.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	if len(d.PGPassword) != 48 {
		t.Errorf("PGPassword length = %d, want 48 (hex of 24 bytes)", len(d.PGPassword))
	}
	for _, c := range d.PGPassword {
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
			t.Errorf("PGPassword not hex (must survive YAML/DSN/env unquoted): %q", d.PGPassword)
			break
		}
	}
}

func TestFillSecretsPreservesProvidedPGPassword(t *testing.T) {
	// A wizard/answers/reuse-provided password must survive: on a reinstall
	// over an existing db volume POSTGRES_PASSWORD is initdb-only, so
	// regenerating would lock the stack out of its own database.
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres
	d.PGPassword = "carried-over"
	if err := d.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	if d.PGPassword != "carried-over" {
		t.Errorf("PGPassword rotated: got %q", d.PGPassword)
	}
}

func TestFillSecretsLeavesLocalPGPasswordAlone(t *testing.T) {
	// Local path: the user's own Postgres has whatever password it has — a
	// blank one is legitimate under trust auth and must not be replaced.
	d := NewDraft()
	d.RuntimePath = RuntimeSource
	d.DBBackend = BackendPostgres
	if err := d.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	if d.PGPassword != "" {
		t.Errorf("local-path PGPassword should stay blank, got %q", d.PGPassword)
	}
}

func TestFillSecretsNoSSOKeyWithoutSSO(t *testing.T) {
	d := NewDraft()
	if err := d.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	if d.IDSigningKeyPEM != "" {
		t.Errorf("id signing key should be empty when SSO disabled")
	}
}

func TestFillSecretsGeneratesES256ForSSO(t *testing.T) {
	d := NewDraft()
	d.SSOEnabled = true
	if err := d.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	if d.IDSigningKID == "" {
		t.Errorf("expected a default id signing kid")
	}
	block, _ := pem.Decode([]byte(d.IDSigningKeyPEM))
	if block == nil {
		t.Fatalf("id signing key is not PEM")
		return // unreachable; satisfies SA5011 when noreturn facts are cold
	}
	if _, err := x509.ParsePKCS8PrivateKey(block.Bytes); err != nil {
		t.Fatalf("id signing key not a valid PKCS8 key: %v", err)
	}
}
