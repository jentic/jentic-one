package auth

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
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

func TestReadAPIKey_RecoversInterruptedReplacement(t *testing.T) {
	const (
		oldKey    = "jak_interrupted_old" // pragma: allowlist secret
		newKey    = "jak_interrupted_new" // pragma: allowlist secret
		oldOrigin = "https://origin-a.example.com"
		newOrigin = "https://origin-b.example.com"
	)
	stages := []string{
		"pending record only",
		"new key committed",
		"new origin committed",
		"both files committed",
	}
	for _, stage := range stages {
		t.Run(stage, func(t *testing.T) {
			withStateDir(t)
			ref := IdentityRef{Identity: "ci", Environment: "prod"}
			if err := SaveAPIKeyForOrigin(ref, oldKey, oldOrigin); err != nil {
				t.Fatal(err)
			}
			path, err := apiKeyPath(ref)
			if err != nil {
				t.Fatal(err)
			}
			pending, err := json.Marshal(apiKeyRecord{APIKey: newKey, Origin: newOrigin})
			if err != nil {
				t.Fatal(err)
			}
			pendingPath := apiKeyPendingPath(path)
			if err := writeFileAtomic(pendingPath, pending); err != nil {
				t.Fatal(err)
			}
			switch stage {
			case "new key committed", "both files committed":
				if err := writeFileAtomic(path, []byte(newKey)); err != nil {
					t.Fatal(err)
				}
			case "new origin committed":
				if err := writeFileAtomic(path+".origin", []byte(newOrigin)); err != nil {
					t.Fatal(err)
				}
			}
			if stage == "both files committed" {
				if err := writeFileAtomic(path+".origin", []byte(newOrigin)); err != nil {
					t.Fatal(err)
				}
			}
			seedEnvironmentBaseURL(t, "prod", newOrigin)

			got, err := ReadAPIKey(ref)
			if err != nil {
				t.Fatalf("ReadAPIKey after interrupted save: %v", err)
			}
			if got != newKey {
				t.Fatal("ReadAPIKey did not recover the pending credential")
			}
			if _, err := os.Stat(pendingPath); !os.IsNotExist(err) {
				t.Fatalf("pending update remains after recovery: %v", err)
			}
			rec, err := readAPIKeyRecord(ref)
			if err != nil {
				t.Fatal(err)
			}
			if rec.Origin != newOrigin {
				t.Fatalf("recovered origin = %q, want %q", rec.Origin, newOrigin)
			}
		})
	}
}

func TestAPIKeyReplacement_ConcurrentReadersNeverSeeMismatchedOrigin(t *testing.T) {
	withStateDir(t)
	ref := IdentityRef{Identity: "ci", Environment: "prod"}
	const (
		keyA    = "jak_concurrent_a" // pragma: allowlist secret
		keyB    = "jak_concurrent_b" // pragma: allowlist secret
		originA = "https://origin-a.example.com"
		originB = "https://origin-b.example.com"
	)
	if err := SaveAPIKeyForOrigin(ref, keyA, originA); err != nil {
		t.Fatal(err)
	}

	const (
		writers    = 2
		readers    = 4
		iterations = 100
	)
	start := make(chan struct{})
	errs := make(chan error, writers+readers)
	var wg sync.WaitGroup
	for i := range writers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			for j := range iterations {
				key, origin := keyA, originA
				if (i+j)%2 == 1 {
					key, origin = keyB, originB
				}
				if err := SaveAPIKeyForOrigin(ref, key, origin); err != nil {
					errs <- err
					return
				}
			}
		}()
	}
	for range readers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			for range iterations {
				rec, err := readAPIKeyRecord(ref)
				if err != nil {
					errs <- err
					return
				}
				matchesA := rec.APIKey == keyA && rec.Origin == originA // pragma: allowlist secret
				matchesB := rec.APIKey == keyB && rec.Origin == originB // pragma: allowlist secret
				if !matchesA && !matchesB {
					errs <- fmt.Errorf("observed an API key with a mismatched origin")
					return
				}
			}
		}()
	}
	close(start)
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
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
