package auth

import (
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
