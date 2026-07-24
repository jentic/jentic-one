package install

import (
	"os"
	"path/filepath"
	"testing"
)

// writeRenderedConfig renders src via Draft.Render and writes it to a temp
// file, returning the path. Simulates an existing on-disk config for reuse.
func writeRenderedConfig(t *testing.T, src *Draft) string {
	t.Helper()
	data, err := src.Render()
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	path := filepath.Join(t.TempDir(), "jentic-one.yaml")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	return path
}

func TestReuseSecretsRoundTripKeepsSecretsIdentical(t *testing.T) {
	// The core repro guard: render a config, reuse it into a fresh draft,
	// re-render, and confirm every secret survives byte-identically. If this
	// regresses, reinstall silently rotates the encryption key.
	src := NewDraft()
	if err := src.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	path := writeRenderedConfig(t, src)

	dst := NewDraft()
	reused, err := ReuseSecrets(dst, path)
	if err != nil {
		t.Fatalf("ReuseSecrets: %v", err)
	}
	if !reused {
		t.Fatalf("expected reused=true from a valid rendered config")
	}
	if err := dst.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets (dst): %v", err)
	}

	out := renderToMap(t, dst)

	srcOut := renderToMap(t, src)
	assertSameSecrets(t, srcOut, out)
}

func assertSameSecrets(t *testing.T, want, got map[string]any) {
	t.Helper()
	wantEnc := want["credentials"].(map[string]any)["encryption"]
	gotEnc := got["credentials"].(map[string]any)["encryption"]
	if !yamlEqual(wantEnc, gotEnc) {
		t.Errorf("encryption keyset drifted:\n want=%v\n got=%v", wantEnc, gotEnc)
	}

	wantAdmin := want["admin"].(map[string]any)
	gotAdmin := got["admin"].(map[string]any)
	if wantAdmin["auth"].(map[string]any)["jwt_secret"] != gotAdmin["auth"].(map[string]any)["jwt_secret"] {
		t.Errorf("admin.auth.jwt_secret drifted")
	}
	if wantAdmin["invite"].(map[string]any)["pepper"] != gotAdmin["invite"].(map[string]any)["pepper"] {
		t.Errorf("admin.invite.pepper drifted")
	}

	wantConnect := want["credentials"].(map[string]any)["connect"].(map[string]any)
	gotConnect := got["credentials"].(map[string]any)["connect"].(map[string]any)
	if wantConnect["state_secret"] != gotConnect["state_secret"] {
		t.Errorf("credentials.connect.state_secret drifted")
	}
}

// yamlEqual compares two decoded YAML values structurally.
func yamlEqual(a, b any) bool {
	switch av := a.(type) {
	case map[string]any:
		bv, ok := b.(map[string]any)
		if !ok || len(av) != len(bv) {
			return false
		}
		for k, v := range av {
			if !yamlEqual(v, bv[k]) {
				return false
			}
		}
		return true
	case []any:
		bv, ok := b.([]any)
		if !ok || len(av) != len(bv) {
			return false
		}
		for i := range av {
			if !yamlEqual(av[i], bv[i]) {
				return false
			}
		}
		return true
	default:
		return a == b
	}
}

func TestReuseSecretsPreservesMultiKeyKeysetVerbatim(t *testing.T) {
	// A hand-rotated keyset (active_id: v2 + v1/v2 entries) must survive
	// reinstall. Flattening it back to a single v1 entry would silently
	// invalidate rows encrypted under the retired v1 key on the next
	// rotation.
	src := NewDraft()
	if err := src.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	src.EncryptionKeyset = &encryptionOut{
		ActiveID: "v2",
		Entries: []encryptionEntryOut{
			{ID: "v1", Material: "retired-key-material"},
			{ID: "v2", Material: "active-key-material"},
		},
	}
	path := writeRenderedConfig(t, src)

	dst := NewDraft()
	if _, err := ReuseSecrets(dst, path); err != nil {
		t.Fatalf("ReuseSecrets: %v", err)
	}
	if dst.EncryptionKeyset == nil {
		t.Fatalf("EncryptionKeyset should be preserved")
	}
	if dst.EncryptionKeyset.ActiveID != "v2" {
		t.Errorf("ActiveID = %q, want v2", dst.EncryptionKeyset.ActiveID)
	}
	if len(dst.EncryptionKeyset.Entries) != 2 {
		t.Fatalf("Entries len = %d, want 2", len(dst.EncryptionKeyset.Entries))
	}
	if dst.EncryptionKeyset.Entries[0].Material != "retired-key-material" {
		t.Errorf("retired v1 material dropped")
	}
	if dst.EncryptionKeyset.Entries[1].Material != "active-key-material" {
		t.Errorf("active v2 material dropped")
	}
}

func TestReuseSecretsMissingFileIsNoOp(t *testing.T) {
	// Fresh install (no prior config, no backup): ReuseSecrets is a quiet
	// no-op so FillSecrets can generate everything from scratch.
	dst := NewDraft()
	reused, err := ReuseSecrets(dst, filepath.Join(t.TempDir(), "does-not-exist.yaml"))
	if err != nil {
		t.Fatalf("ReuseSecrets: %v", err)
	}
	if reused {
		t.Errorf("expected reused=false for missing file")
	}
	if dst.EncryptionKey != "" || dst.AdminJWTSecret != "" {
		t.Errorf("draft mutated for missing file")
	}
}

func TestReuseSecretsMalformedYAMLReturnsError(t *testing.T) {
	// A half-written / corrupted prior config must not brick the reinstall:
	// return an error so the caller falls back to fresh secrets; the draft
	// stays untouched.
	path := filepath.Join(t.TempDir(), "jentic-one.yaml")
	if err := os.WriteFile(path, []byte(":\n\tnot yaml\n"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}

	dst := NewDraft()
	dst.EncryptionKey = "sentinel"
	reused, err := ReuseSecrets(dst, path)
	if err == nil {
		t.Fatalf("expected an error for malformed YAML")
	}
	if reused {
		t.Errorf("reused=true for malformed YAML")
	}
	if dst.EncryptionKey != "sentinel" {
		t.Errorf("draft mutated on error: EncryptionKey=%q", dst.EncryptionKey)
	}
	if dst.EncryptionKeyset != nil {
		t.Errorf("EncryptionKeyset set on error")
	}
}

func TestReuseSecretsIgnoresEmptyEncryptionBlock(t *testing.T) {
	// An empty entries list (or entries with blank id/material) must not wipe
	// the fresh default — an aborted prior install could leave one on disk.
	path := filepath.Join(t.TempDir(), "jentic-one.yaml")
	if err := os.WriteFile(path, []byte(`
credentials:
  encryption:
    active_id: v1
    entries: []
`), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}

	dst := NewDraft()
	reused, err := ReuseSecrets(dst, path)
	if err != nil {
		t.Fatalf("ReuseSecrets: %v", err)
	}
	if reused {
		t.Errorf("empty entries should not count as reuse")
	}
	if dst.EncryptionKeyset != nil {
		t.Errorf("empty keyset preserved: %+v", dst.EncryptionKeyset)
	}
}

func TestReuseSecretsCarriesTelemetryInstanceID(t *testing.T) {
	// The stable opaque instance id must survive across reinstalls when the
	// operator re-consents; otherwise every reinstall would produce a new
	// telemetry identity.
	src := NewDraft()
	src.TelemetryEnabled = true
	src.TelemetryInstanceID = "inst-stable-id-abc"
	if err := src.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	path := writeRenderedConfig(t, src)

	dst := NewDraft()
	if _, err := ReuseSecrets(dst, path); err != nil {
		t.Fatalf("ReuseSecrets: %v", err)
	}
	if dst.TelemetryInstanceID != "inst-stable-id-abc" {
		t.Errorf("TelemetryInstanceID = %q, want inst-stable-id-abc", dst.TelemetryInstanceID)
	}
}

func TestReuseSecretsCarriesSSOSigningKey(t *testing.T) {
	// SSO id_signing must survive reinstall so previously-issued ID tokens
	// stay verifiable if the operator re-enables SSO with the same config.
	src := NewDraft()
	src.SSOEnabled = true
	src.SSOClientID = "client"
	src.SSOClientSecret = "secret"
	if err := src.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	origKID := src.IDSigningKID
	origPEM := src.IDSigningKeyPEM
	if origKID == "" || origPEM == "" {
		t.Fatalf("precondition: SSO draft should have signing key")
	}
	path := writeRenderedConfig(t, src)

	dst := NewDraft()
	if _, err := ReuseSecrets(dst, path); err != nil {
		t.Fatalf("ReuseSecrets: %v", err)
	}
	if dst.IDSigningKID != origKID {
		t.Errorf("IDSigningKID = %q, want %q", dst.IDSigningKID, origKID)
	}
	if dst.IDSigningKeyPEM != origPEM {
		t.Errorf("IDSigningKeyPEM drifted")
	}
}
