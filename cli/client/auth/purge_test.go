package auth

import (
	"os"
	"path/filepath"
	"testing"
)

// TestPurgeMaterial_RemovesAllMaterialFiles verifies that PurgeMaterial deletes the
// key, tokens, API key, and origin sidecar for a ref (F8-34: identity/context delete
// must not orphan on-disk secrets).
func TestPurgeMaterial_RemovesAllMaterialFiles(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "agent", Environment: "prod"}
	seedEnvironmentBaseURL(t, "prod", "https://origin-a.example.com")

	// Materialize all credential files.
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
	akOriginPath, _ := apiKeyOriginPath(ref)
	for _, p := range []string{keyPath, tokPath, akPath, akOriginPath} {
		if _, err := os.Stat(p); err != nil {
			t.Fatalf("precondition: expected %s to exist: %v", p, err)
		}
	}

	if err := PurgeMaterial(ref); err != nil {
		t.Fatalf("PurgeMaterial: %v", err)
	}

	for _, p := range []string{keyPath, tokPath, akPath, akOriginPath} {
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

func TestPurgeEnvironmentCredentials_RemovesAtomicTempFiles(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "agent", Environment: "prod"}
	keyPath, err := apiKeyPath(ref)
	if err != nil {
		t.Fatal(err)
	}
	tokenPath, err := getTokenPath(ref)
	if err != nil {
		t.Fatal(err)
	}
	originPath, err := apiKeyOriginPath(ref)
	if err != nil {
		t.Fatal(err)
	}
	pendingPath := apiKeyPendingPath(keyPath)
	stateDir := filepath.Dir(keyPath)

	createTemp := func(finalPath string) string {
		t.Helper()
		tmp, err := os.CreateTemp(stateDir, "."+filepath.Base(finalPath)+".tmp-*")
		if err != nil {
			t.Fatal(err)
		}
		name := tmp.Name()
		if _, err := tmp.Write([]byte("crash-left credential")); err != nil {
			_ = tmp.Close()
			t.Fatal(err)
		}
		if err := tmp.Close(); err != nil {
			t.Fatal(err)
		}
		return name
	}

	tempPaths := make([]string, 0, 4)
	tempPaths = append(tempPaths, createTemp(keyPath), createTemp(tokenPath))
	for _, finalPath := range []string{originPath, pendingPath} {
		tempPath := filepath.Join(stateDir, "."+filepath.Base(finalPath)+".tmp-424242")
		if err := os.WriteFile(tempPath, []byte("crash-left credential"), 0o600); err != nil {
			t.Fatal(err)
		}
		tempPaths = append(tempPaths, tempPath)
	}

	decoys := []string{
		filepath.Join(stateDir, "."+filepath.Base(keyPath)+".tmp-"),
		filepath.Join(stateDir, "."+filepath.Base(keyPath)+".tmp-12ab"),
		filepath.Join(stateDir, "."+filepath.Base(keyPath)+".tmp-123.extra"),
		filepath.Join(stateDir, filepath.Base(keyPath)+".tmp-123"),
		filepath.Join(stateDir, "."+filepath.Base(tokenPath)+".tmp--123"),
		filepath.Join(stateDir, ".agent_dev.apikey.tmp-123"),
		filepath.Join(stateDir, "unrelated.txt"),
	}
	for _, path := range decoys {
		if err := os.WriteFile(path, []byte("preserve"), 0o600); err != nil {
			t.Fatal(err)
		}
	}

	if err := PurgeEnvironmentCredentials("prod"); err != nil {
		t.Fatalf("PurgeEnvironmentCredentials: %v", err)
	}
	for _, path := range tempPaths {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Errorf("atomic credential temp %s survives purge: %v", filepath.Base(path), err)
		}
	}
	for _, path := range decoys {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Errorf("near-miss or unrelated file %s was removed: %v", filepath.Base(path), err)
			continue
		}
		if string(data) != "preserve" {
			t.Errorf("near-miss or unrelated file %s changed to %q", filepath.Base(path), data)
		}
	}
}
