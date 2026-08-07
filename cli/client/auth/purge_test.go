package auth

import (
	"os"
	"testing"
)

// TestPurgeMaterial_RemovesAllThreeFiles verifies that PurgeMaterial deletes the
// key, tokens, and apikey files for a ref (F8-34: identity/context delete must not
// orphan on-disk secrets).
func TestPurgeMaterial_RemovesAllThreeFiles(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "agent", Environment: "prod"}

	// Materialize all three secret files.
	if _, err := GetOrGenerateKey(ref); err != nil {
		t.Fatalf("GetOrGenerateKey: %v", err)
	}
	if err := SaveAPIKey(ref, APIKeyPrefix+"deadbeef"); err != nil {
		t.Fatalf("SaveAPIKey: %v", err)
	}
	if err := SaveTokens(ref, &TokenSet{AccessToken: "at"}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}

	keyPath, _ := KeyPathForImport(ref)
	tokPath, _ := getTokenPath(ref)
	akPath, _ := apiKeyPath(ref)
	for _, p := range []string{keyPath, tokPath, akPath} {
		if _, err := os.Stat(p); err != nil {
			t.Fatalf("precondition: expected %s to exist: %v", p, err)
		}
	}

	if err := PurgeMaterial(ref); err != nil {
		t.Fatalf("PurgeMaterial: %v", err)
	}

	for _, p := range []string{keyPath, tokPath, akPath} {
		if _, err := os.Stat(p); !os.IsNotExist(err) {
			t.Errorf("expected %s to be removed, stat err = %v", p, err)
		}
	}
}

// TestPurgeMaterial_MissingFilesIsNotError verifies that purging a ref with no
// on-disk material succeeds (a missing file is nothing to remove).
func TestPurgeMaterial_MissingFilesIsNotError(t *testing.T) {
	withConfigDir(t)
	if err := PurgeMaterial(IdentityRef{Identity: "ghost", Environment: "dev"}); err != nil {
		t.Errorf("PurgeMaterial on absent material should be a no-op, got: %v", err)
	}
}

// TestPurgeMaterial_InvalidStemErrors verifies the path-traversal guard: an
// invalid name is rejected before any filesystem op.
func TestPurgeMaterial_InvalidStemErrors(t *testing.T) {
	withConfigDir(t)
	if err := PurgeMaterial(IdentityRef{Identity: "../escape", Environment: "prod"}); err == nil {
		t.Error("expected an error for an invalid identity name, got nil")
	}
}
