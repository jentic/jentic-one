package auth

import (
	"os"
	"path/filepath"
	"testing"
)

func withStateDir(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(dir, "config"))
	t.Setenv("XDG_STATE_HOME", filepath.Join(dir, "state"))
}

func TestSaveReadAPIKey_RoundTrip(t *testing.T) {
	withStateDir(t)
	ref := IdentityRef{Identity: "ci", Environment: "prod"}
	if err := SaveAPIKey(ref, "jak_abc123"); err != nil {
		t.Fatalf("SaveAPIKey: %v", err)
	}
	got, err := ReadAPIKey(ref)
	if err != nil {
		t.Fatalf("ReadAPIKey: %v", err)
	}
	if got != "jak_abc123" {
		t.Errorf("api key = %q, want jak_abc123", got)
	}
}

func TestSaveAPIKey_LegacyFileRemainsBareWithOriginSidecar(t *testing.T) {
	withStateDir(t)
	seedEnvironmentBaseURL(t, "prod", "https://origin-a.example.com")
	ref := IdentityRef{Identity: "ci", Environment: "prod"}
	const key = "jak_mixed_version_test" // pragma: allowlist secret
	if err := SaveAPIKey(ref, key); err != nil {
		t.Fatal(err)
	}

	path, err := apiKeyPath(ref)
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != key {
		t.Fatalf("legacy API-key reader would receive %q instead of the bare key", data)
	}
	originPath, err := apiKeyOriginPath(ref)
	if err != nil {
		t.Fatal(err)
	}
	originData, err := os.ReadFile(originPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(originData) != "https://origin-a.example.com" {
		t.Fatalf("origin sidecar = %q", originData)
	}
	rec, err := readAPIKeyRecord(ref)
	if err != nil || rec.APIKey != key || rec.Origin != "https://origin-a.example.com" { // pragma: allowlist secret
		t.Fatalf("origin-bound read = %+v, %v", rec, err)
	}
}

func TestSaveAPIKeyForOrigin_RejectsInvalidExplicitOrigin(t *testing.T) {
	withStateDir(t)
	seedEnvironmentBaseURL(t, "prod", "https://origin-a.example.com")
	ref := IdentityRef{Identity: "ci", Environment: "prod"}
	if err := SaveAPIKeyForOrigin(ref, "jak_invalid_origin_test", "://not-a-url"); err == nil {
		t.Fatal("an invalid explicit origin must not fall back to the configured environment")
	}
	path, err := apiKeyPath(ref)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("invalid origin wrote a credential: %v", err)
	}
}

func TestSaveAPIKey_RejectsBadPrefix(t *testing.T) {
	withStateDir(t)
	ref := IdentityRef{Identity: "ci", Environment: "prod"}
	if err := SaveAPIKey(ref, "not-a-jentic-key"); err == nil {
		t.Fatal("expected an error for a key without the jak_ prefix")
	}
}

func TestKeyPathForImport_UsesStem(t *testing.T) {
	withStateDir(t)
	path, err := KeyPathForImport(IdentityRef{Identity: "ci", Environment: "prod"})
	if err != nil {
		t.Fatal(err)
	}
	if base := filepath.Base(path); base != "ci_prod.key" {
		t.Errorf("import key path base = %q, want ci_prod.key", base)
	}
	// Path traversal in a name must be rejected fail-closed.
	if _, err := KeyPathForImport(IdentityRef{Identity: "../evil", Environment: "prod"}); err == nil {
		t.Error("expected KeyPathForImport to reject a path-traversal name")
	}
}
