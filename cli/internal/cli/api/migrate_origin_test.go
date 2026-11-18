package api

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
)

func TestMigrate_ReRunPreservesUnusableV2TokenCache(t *testing.T) {
	for _, tc := range []struct {
		name string
		raw  string
	}{
		{
			name: "origin-less",
			raw:  `{"access_token":"at-v2-new-family","refresh_token":"rt-v2-new-family","expires_at":"2099-01-01T00:00:00Z"}`,
		},
		{
			name: "origin-mismatched",
			raw:  `{"access_token":"at-v2-new-family","refresh_token":"rt-v2-new-family","expires_at":"2099-01-01T00:00:00Z","origin":"https://other.example.com"}`,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			withXDG(t)
			app := testApp(t)
			seedLegacyDCRProfile(t, app.Paths, "work", "https://api.jentic.com")
			if _, err := runMigrate(app, false); err != nil {
				t.Fatalf("first migrate: %v", err)
			}

			stateDir, err := sdkconfig.StateDir()
			if err != nil {
				t.Fatal(err)
			}
			tokenPath := filepath.Join(stateDir, "work_api-jentic-com_tokens.json")
			raw := []byte(tc.raw)
			if err := os.WriteFile(tokenPath, raw, 0o600); err != nil {
				t.Fatal(err)
			}

			if _, err := runMigrate(app, false); err != nil {
				t.Fatalf("re-migrate with an existing unusable V2 cache: %v", err)
			}
			gotRaw, err := os.ReadFile(tokenPath)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(gotRaw, raw) {
				t.Fatalf("re-migration changed the V2 token cache\ngot:  %s\nwant: %s", gotRaw, raw)
			}
			var tokens auth.TokenSet
			if err := json.Unmarshal(gotRaw, &tokens); err != nil {
				t.Fatal(err)
			}
			if tokens.AccessToken != "at-v2-new-family" || tokens.RefreshToken != "rt-v2-new-family" {
				t.Errorf("V2 token family changed to %+v", tokens)
			}
		})
	}
}

func TestMigrate_ReRunPreservesReadableV2TokenFamilyAfterRepoint(t *testing.T) {
	withXDG(t)
	app := testApp(t)
	const (
		legacyOrigin  = "https://origin-a.example.com"
		currentOrigin = "https://origin-b.example.com"
	)
	seedLegacyDCRProfile(t, app.Paths, "work", legacyOrigin)
	if _, err := runMigrate(app, false); err != nil {
		t.Fatalf("first migrate: %v", err)
	}

	ref := auth.IdentityRef{Identity: "work", Environment: envNameFromURL(legacyOrigin)}
	if err := sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
		environment := cfg.Environments[ref.Environment]
		environment.BaseURL = currentOrigin
		cfg.Environments[ref.Environment] = environment
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	newer := &auth.TokenSet{
		AccessToken:  "at-v2-current-b",
		RefreshToken: "rt-v2-current-b",
		ExpiresAt:    time.Date(2099, time.January, 1, 0, 0, 0, 0, time.UTC),
		Origin:       currentOrigin,
	}
	if err := auth.SaveTokens(ref, newer); err != nil {
		t.Fatalf("saving current-origin V2 tokens: %v", err)
	}
	if got, err := auth.ReadTokens(ref); err != nil || *got != *newer {
		t.Fatalf("current-origin V2 token precondition = %+v, %v", got, err)
	}

	if _, err := runMigrate(app, false); err != nil {
		t.Fatalf("re-migrate after environment repoint: %v", err)
	}
	got, err := auth.ReadTokens(ref)
	if err != nil {
		t.Fatalf("reading current-origin V2 tokens after re-migration: %v", err)
	}
	if *got != *newer {
		t.Errorf("re-migration changed the current-origin V2 token family\ngot:  %+v\nwant: %+v", got, newer)
	}
}

func TestMigrate_ReRunReturnsExistingTokenReadErrors(t *testing.T) {
	withXDG(t)
	app := testApp(t)
	seedLegacyDCRProfile(t, app.Paths, "work", "https://api.jentic.com")
	if _, err := runMigrate(app, false); err != nil {
		t.Fatalf("first migrate: %v", err)
	}

	stateDir, err := sdkconfig.StateDir()
	if err != nil {
		t.Fatal(err)
	}
	tokenPath := filepath.Join(stateDir, "work_api-jentic-com_tokens.json")
	raw := []byte(`{"access_token":`)
	if err := os.WriteFile(tokenPath, raw, 0o600); err != nil {
		t.Fatal(err)
	}

	_, migrateErr := runMigrate(app, false)
	if migrateErr == nil {
		t.Error("re-migration with an unreadable V2 token cache succeeded")
	} else {
		if !strings.Contains(migrateErr.Error(), "reading existing V2 tokens") {
			t.Errorf("migration error lacks existing-token read context: %v", migrateErr)
		}
		if !strings.Contains(migrateErr.Error(), "decoding token state") {
			t.Errorf("migration error lost the underlying read failure: %v", migrateErr)
		}
	}
	gotRaw, err := os.ReadFile(tokenPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(gotRaw, raw) {
		t.Errorf("failed re-migration changed the V2 token cache to %s", gotRaw)
	}
}
