package api

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	legacyconfig "github.com/jentic/jentic-one/cli/internal/config"
)

// writeLegacyProfileFiles writes raw V1 profile files (profile.yaml + optional
// friends) under paths, byte-compatible with what the V1 CLI left on disk. The
// V1 writer packages are deleted, so the tests seed the layout directly — which
// also pins the on-disk contract migrate must keep reading.
func writeLegacyProfileFiles(t *testing.T, paths legacyconfig.Paths, name string, meta *legacyMeta, files map[string][]byte) {
	t.Helper()
	dir := filepath.Join(paths.ProfilesDir(), name)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	metaBytes, err := yaml.Marshal(meta)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, legacyProfileFile), metaBytes, 0o600); err != nil {
		t.Fatal(err)
	}
	for fname, data := range files {
		if err := os.WriteFile(filepath.Join(dir, fname), data, 0o600); err != nil {
			t.Fatal(err)
		}
	}
}

// seedLegacyDCRProfile writes a legacy DCR profile (agent.key + profile.yaml +
// tokens.json) under paths. It mirrors what V1 `jentic register` left on disk
// so migrate has real material to copy.
func seedLegacyDCRProfile(t *testing.T, paths legacyconfig.Paths, name, baseURL string) {
	t.Helper()
	// Ed25519 PKCS#8 PEM key, exactly as the V1 agentkey.Save wrote it.
	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	der, _ := x509.MarshalPKCS8PrivateKey(priv)
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})
	toks, err := json.Marshal(&legacyTokens{
		AccessToken:     "at-test-synthetic-" + name,
		RefreshToken:    "rt-test-synthetic-" + name,
		AccessExpiresAt: time.Now().Add(time.Hour).UTC().Round(time.Second),
	})
	if err != nil {
		t.Fatal(err)
	}
	writeLegacyProfileFiles(t, paths, name, &legacyMeta{
		BaseURL: baseURL,
		AgentID: "agnt_" + name,
	}, map[string][]byte{
		legacyKeyFile:    pemBytes,
		legacyTokensFile: toks,
	})
}

func TestMigrate_CopiesProfilesToXDG(t *testing.T) {
	withXDG(t)
	app := testApp(t)
	legacyPaths := app.Paths

	// Seed two legacy profiles and mark one the default.
	seedLegacyDCRProfile(t, legacyPaths, "work", "https://api.jentic.com")
	seedLegacyDCRProfile(t, legacyPaths, "staging", "https://staging.jentic.com:8443")
	// default_profile is a V1-only field read solely by migrate (via
	// legacy_store.legacyDefaultProfile); write it straight into the legacy
	// config.yaml since the live FileConfig no longer carries it (ARCH-21 B2).
	if err := os.WriteFile(legacyPaths.ConfigPath(), []byte("default_profile: work\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	res, err := runMigrate(app, false)
	if err != nil {
		t.Fatalf("migrate: %v", err)
	}

	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}

	// Contexts, identities, and host-derived environments were created.
	if _, ok := cfg.Contexts["work"]; !ok {
		t.Error("context work not created")
	}
	if _, ok := cfg.Identities["work"]; !ok {
		t.Error("identity work not created")
	}
	if gotID := cfg.Identities["work"].Environments["api-jentic-com"].ClientID; gotID != "agnt_work" {
		t.Errorf("migrated work client_id = %q, want agnt_work", gotID)
	}
	if _, ok := cfg.Environments["api-jentic-com"]; !ok {
		t.Errorf("environment api-jentic-com not created; envs=%v", cfg.Environments)
	}
	// Port is stripped from the derived env name.
	if _, ok := cfg.Environments["staging-jentic-com"]; !ok {
		t.Errorf("environment staging-jentic-com not created; envs=%v", cfg.Environments)
	}
	// The default profile became the active context.
	if cfg.ActiveContext != "work" {
		t.Errorf("active context = %q, want work", cfg.ActiveContext)
	}
	if res.active != "work" {
		t.Errorf("result active = %q, want work", res.active)
	}

	// Key material was COPIED into the XDG layout with the <identity>_<env> stem.
	keyPath, _ := auth.KeyPathForImport(auth.IdentityRef{Identity: "work", Environment: "api-jentic-com"})
	if _, err := os.Stat(keyPath); err != nil {
		t.Errorf("migrated key not found at %s: %v", keyPath, err)
	}
	// Legacy tree is untouched (copy, not move).
	if _, err := os.Stat(filepath.Join(legacyPaths.ProfilesDir(), "work", "agent.key")); err != nil {
		t.Errorf("legacy key should survive a copy-migration: %v", err)
	}
	// A MIGRATED marker was dropped.
	if _, err := os.Stat(filepath.Join(legacyPaths.Root, migratedMarkerName)); err != nil {
		t.Errorf("MIGRATED marker not written: %v", err)
	}
}

func mustJSONBytes(t *testing.T, v any) []byte {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// TestMigrate_PreservesRefreshTokenAndExpiryMapping pins #1384: V1->V2 token
// migration preserves present refresh tokens alongside access_token and the
// access_expires_at -> expires_at mapping, while treating absent, empty, and
// whitespace-only refresh tokens cleanly without emitting empty fields.
func TestMigrate_PreservesRefreshTokenAndExpiryMapping(t *testing.T) {
	exp := time.Date(2026, 9, 14, 13, 26, 0, 0, time.UTC)
	const expStr = "2026-09-14T13:26:00Z"

	cases := []struct {
		name           string
		doc            map[string]any
		omitTokensFile bool
		wantFile       bool
		wantAccess     string
		wantRefresh    string
		wantRefreshKey bool
	}{
		{
			name: "present_refresh_token",
			doc: map[string]any{
				"access_token":      "at_syn_1",
				"refresh_token":     "rt_syn_1",
				"access_expires_at": expStr,
			},
			wantFile:       true,
			wantAccess:     "at_syn_1",
			wantRefresh:    "rt_syn_1",
			wantRefreshKey: true,
		},
		{
			name: "padded_refresh_token_trimmed",
			doc: map[string]any{
				"access_token":      "at_syn_2",
				"refresh_token":     "  rt_syn_2\n",
				"access_expires_at": expStr,
			},
			wantFile:       true,
			wantAccess:     "at_syn_2",
			wantRefresh:    "rt_syn_2",
			wantRefreshKey: true,
		},
		{
			name: "absent_refresh_token",
			doc: map[string]any{
				"access_token":      "at_syn_3",
				"access_expires_at": expStr,
			},
			wantFile:       true,
			wantAccess:     "at_syn_3",
			wantRefresh:    "",
			wantRefreshKey: false,
		},
		{
			name: "empty_refresh_token",
			doc: map[string]any{
				"access_token":      "at_syn_4",
				"refresh_token":     "",
				"access_expires_at": expStr,
			},
			wantFile:       true,
			wantAccess:     "at_syn_4",
			wantRefresh:    "",
			wantRefreshKey: false,
		},
		{
			name: "whitespace_refresh_token",
			doc: map[string]any{
				"access_token":      "at_syn_5",
				"refresh_token":     "   \x09\n ",
				"access_expires_at": expStr,
			},
			wantFile:       true,
			wantAccess:     "at_syn_5",
			wantRefresh:    "",
			wantRefreshKey: false,
		},
		{
			name:           "absent_tokens_file",
			omitTokensFile: true,
			wantFile:       false,
		},
		{
			name: "both_tokens_whitespace_only",
			doc: map[string]any{
				"access_token":      "  ",
				"refresh_token":     "\x09\n",
				"access_expires_at": expStr,
			},
			wantFile: false,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			withXDG(t)
			app := testApp(t)
			_, priv, _ := ed25519.GenerateKey(rand.Reader)
			der, _ := x509.MarshalPKCS8PrivateKey(priv)
			pemBytes := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})
			files := map[string][]byte{legacyKeyFile: pemBytes}
			if !tc.omitTokensFile {
				files[legacyTokensFile] = mustJSONBytes(t, tc.doc)
			}
			writeLegacyProfileFiles(t, app.Paths, "work", &legacyMeta{
				BaseURL: "https://api.jentic.com",
				AgentID: "agnt_work",
			}, files)

			if _, err := runMigrate(app, false); err != nil {
				t.Fatalf("runMigrate: %v", err)
			}

			stateDir, err := sdkconfig.StateDir()
			if err != nil {
				t.Fatal(err)
			}
			tokPath := filepath.Join(stateDir, "work_api-jentic-com_tokens.json")
			if !tc.wantFile {
				if _, serr := os.Stat(tokPath); !os.IsNotExist(serr) {
					t.Fatalf("expected no migrated token file for %s, stat err = %v", tc.name, serr)
				}
				return
			}

			info, serr := os.Stat(tokPath)
			if serr != nil {
				t.Fatalf("stat migrated token file: %v", serr)
			}
			if perm := info.Mode().Perm(); perm != 0o600 {
				t.Errorf("token file perms = %o, want 0600", perm)
			}

			rawDisk, rerr := os.ReadFile(tokPath)
			if rerr != nil {
				t.Fatal(rerr)
			}
			var diskMap map[string]any
			if uerr := json.Unmarshal(rawDisk, &diskMap); uerr != nil {
				t.Fatalf("unmarshal migrated token file: %v", uerr)
			}
			_, hasRefreshKey := diskMap["refresh_token"]
			if hasRefreshKey != tc.wantRefreshKey {
				t.Errorf("disk refresh_token key present = %v, want %v; raw=%s", hasRefreshKey, tc.wantRefreshKey, rawDisk)
			}

			ref := auth.IdentityRef{Identity: "work", Environment: "api-jentic-com"}
			toks, err := auth.ReadTokens(ref)
			if err != nil {
				t.Fatalf("ReadTokens: %v", err)
			}
			if toks.AccessToken != tc.wantAccess {
				t.Errorf("AccessToken = %q, want %q", toks.AccessToken, tc.wantAccess)
			}
			if toks.RefreshToken != tc.wantRefresh {
				t.Errorf("RefreshToken = %q, want %q", toks.RefreshToken, tc.wantRefresh)
			}
			if !toks.ExpiresAt.Equal(exp) {
				t.Errorf("ExpiresAt = %v, want %v", toks.ExpiresAt, exp)
			}
			if toks.Origin != "https://api.jentic.com" {
				t.Errorf("Origin = %q, want https://api.jentic.com", toks.Origin)
			}
		})
	}
}

func TestMigrate_APIKeyProfile(t *testing.T) {
	withXDG(t)
	app := testApp(t)

	writeLegacyProfileFiles(t, app.Paths, "apikeyprof",
		&legacyMeta{BaseURL: "https://api.jentic.com", AuthMode: legacyAuthModeAPIKey},
		map[string][]byte{legacyAPIKeyFile: []byte("jak_migratedkey\n")})

	if _, err := runMigrate(app, false); err != nil {
		t.Fatal(err)
	}

	cfg, _ := sdkconfig.Load()
	if cfg.Identities["apikeyprof"].Type != "user" {
		t.Errorf("api-key identity type = %q, want user", cfg.Identities["apikeyprof"].Type)
	}
	got, err := auth.ReadAPIKey(auth.IdentityRef{Identity: "apikeyprof", Environment: "api-jentic-com"})
	if err != nil {
		t.Fatalf("reading migrated api key: %v", err)
	}
	if got != "jak_migratedkey" {
		t.Errorf("migrated api key = %q", got)
	}
}

func TestMigrate_Idempotent(t *testing.T) {
	withXDG(t)
	app := testApp(t)
	seedLegacyDCRProfile(t, app.Paths, "work", "https://api.jentic.com")

	first, err := runMigrate(app, false)
	if err != nil {
		t.Fatal(err)
	}
	second, err := runMigrate(app, false)
	if err != nil {
		t.Fatalf("second migrate: %v", err)
	}
	if len(first.contexts) != len(second.contexts) {
		t.Errorf("migrate not idempotent: first=%v second=%v", first.contexts, second.contexts)
	}
}

// TestMigrate_ReRunPreservesRotatedV2TokensAndRepairsMissingRefresh pins #1384
// idempotency: re-running migrate (including with --purge-legacy) must never
// overwrite a newer rotated V2 token pair or V2 config state with stale legacy
// credentials, while still repairing an unrotated token file whose refresh
// token is missing.
func TestMigrate_ReRunPreservesRotatedV2TokensAndRepairsMissingRefresh(t *testing.T) {
	t.Run("preserves_rotated_v2_token_pair_and_state", func(t *testing.T) {
		withXDG(t)
		app := testApp(t)
		seedLegacyDCRProfile(t, app.Paths, "work", "https://api.jentic.com")

		if _, err := runMigrate(app, false); err != nil {
			t.Fatalf("first migrate: %v", err)
		}

		ref := auth.IdentityRef{Identity: "work", Environment: "api-jentic-com"}
		rotatedExp := time.Now().Add(2 * time.Hour).UTC().Round(time.Second)
		if err := auth.SaveTokens(ref, &auth.TokenSet{
			AccessToken:  "at-test-rotated-2",
			RefreshToken: "rt-test-rotated-2",
			ExpiresAt:    rotatedExp,
		}); err != nil {
			t.Fatalf("SaveTokens rotated: %v", err)
		}
		if err := sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
			ctx := cfg.Contexts["work"]
			ctx.Mode = "agent"
			cfg.Contexts["work"] = ctx
			ident := cfg.Identities["work"]
			ident.Environments["api-jentic-com"] = sdkconfig.EnvRegState{
				ClientID: "agnt_v2_rotated",
				Status:   "approved",
			}
			cfg.Identities["work"] = ident
			return nil
		}); err != nil {
			t.Fatalf("MutateConfig: %v", err)
		}

		if _, err := runMigrate(app, false); err != nil {
			t.Fatalf("second migrate: %v", err)
		}
		if _, err := runMigrate(app, true); err != nil {
			t.Fatalf("third migrate --purge-legacy: %v", err)
		}

		got, err := auth.ReadTokens(ref)
		if err != nil {
			t.Fatalf("ReadTokens after re-migrate: %v", err)
		}
		if got.AccessToken != "at-test-rotated-2" || got.RefreshToken != "rt-test-rotated-2" || !got.ExpiresAt.Equal(rotatedExp) {
			t.Errorf("re-running migrate clobbered rotated V2 tokens: got %+v", got)
		}

		cfg, err := sdkconfig.Load()
		if err != nil {
			t.Fatal(err)
		}
		if cfg.Contexts["work"].Mode != "agent" {
			t.Errorf("context mode = %q, want agent preserved", cfg.Contexts["work"].Mode)
		}
		if gotID := cfg.Identities["work"].Environments["api-jentic-com"].ClientID; gotID != "agnt_v2_rotated" {
			t.Errorf("client_id = %q, want agnt_v2_rotated preserved", gotID)
		}
	})

	t.Run("preserves_rotated_access_only_v2_token", func(t *testing.T) {
		withXDG(t)
		app := testApp(t)
		seedLegacyDCRProfile(t, app.Paths, "work", "https://api.jentic.com")

		if _, err := runMigrate(app, false); err != nil {
			t.Fatal(err)
		}
		ref := auth.IdentityRef{Identity: "work", Environment: "api-jentic-com"}
		if err := auth.SaveTokens(ref, &auth.TokenSet{
			AccessToken: "at-test-rotated-3",
			ExpiresAt:   time.Now().Add(2 * time.Hour).UTC().Round(time.Second),
		}); err != nil {
			t.Fatal(err)
		}

		if _, err := runMigrate(app, false); err != nil {
			t.Fatalf("re-migrate: %v", err)
		}
		got, err := auth.ReadTokens(ref)
		if err != nil {
			t.Fatal(err)
		}
		if got.AccessToken != "at-test-rotated-3" || got.RefreshToken != "" {
			t.Errorf("access-only V2 token altered by re-migrate: %+v", got)
		}
	})

	t.Run("repairs_unrotated_migrated_token_missing_refresh", func(t *testing.T) {
		withXDG(t)
		app := testApp(t)
		seedLegacyDCRProfile(t, app.Paths, "work", "https://api.jentic.com")
		legToks, err := viewLegacyProfile(app.Paths, "work").loadTokens()
		if err != nil {
			t.Fatal(err)
		}

		ref := auth.IdentityRef{Identity: "work", Environment: "api-jentic-com"}
		if err := auth.SaveTokens(ref, &auth.TokenSet{
			AccessToken: legToks.AccessToken,
			ExpiresAt:   legToks.AccessExpiresAt,
			Origin:      "https://api.jentic.com",
		}); err != nil {
			t.Fatal(err)
		}

		if _, err := runMigrate(app, false); err != nil {
			t.Fatalf("repair migrate: %v", err)
		}
		got, err := auth.ReadTokens(ref)
		if err != nil {
			t.Fatal(err)
		}
		if got.AccessToken != legToks.AccessToken || got.RefreshToken != "rt-test-synthetic-work" {
			t.Errorf("expected missing refresh token to be backfilled onto unrotated token, got %+v", got)
		}
	})
}

// TestMigrate_CLIOutputRedactsSecretsAndAvoidsUnsafePurgeNudge proves that
// `jentic migrate` retains the refresh token on disk without printing secret
// values in human or agent mode, does not push `--purge-legacy` before an
// authenticated call succeeds, and keeps TokenSet fail-closed under ux
// marshaling (#1384).
func TestMigrate_CLIOutputRedactsSecretsAndAvoidsUnsafePurgeNudge(t *testing.T) {
	for _, mode := range []string{"human", "agent"} {
		t.Run(mode, func(t *testing.T) {
			withXDG(t)
			t.Setenv("JENTIC_MODE", mode)
			app := testApp(t)
			t.Setenv("JENTIC_HOME", app.Paths.Root)

			const (
				synAT = "at_syn_omit"
				synRT = "rt_syn_omit"
			)
			_, priv, _ := ed25519.GenerateKey(rand.Reader)
			der, _ := x509.MarshalPKCS8PrivateKey(priv)
			pemBytes := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})
			tokBytes, _ := json.Marshal(&legacyTokens{
				AccessToken:     synAT,
				RefreshToken:    synRT,
				AccessExpiresAt: time.Now().Add(-24 * time.Hour).UTC().Round(time.Second),
			})
			writeLegacyProfileFiles(t, app.Paths, "default", &legacyMeta{
				BaseURL: "https://api.jentic.com",
				AgentID: "agnt_default",
			}, map[string][]byte{
				legacyKeyFile:    pemBytes,
				legacyTokensFile: tokBytes,
			})

			stdout, stderr, runErr := captureCLIStreams(t, func() error {
				root := newAPIRootCmd(app.App)
				root.SetOut(app.Out)
				root.SetErr(app.Err)
				root.SetArgs([]string{"migrate"})
				return root.Execute()
			})
			if runErr != nil {
				t.Fatalf("jentic migrate (%s): %v", mode, runErr)
			}
			combined := stdout + "\n" + stderr + "\n" + app.Out.(*bytes.Buffer).String() + "\n" + app.Err.(*bytes.Buffer).String()
			for _, candidate := range []string{synAT, synRT} {
				if strings.Contains(combined, candidate) {
					t.Fatalf("CLI output leaked token material in %s mode", mode)
				}
			}
			if strings.Contains(combined, "--purge-legacy") {
				t.Errorf("migrate output in %s mode must not recommend --purge-legacy before verifying authentication: %q", mode, combined)
			}
			if !strings.Contains(combined, "jentic whoami") {
				t.Errorf("migrate output in %s mode should advise verifying authentication first: %q", mode, combined)
			}

			ref := auth.IdentityRef{Identity: "default", Environment: "api-jentic-com"}
			loaded, err := auth.ReadTokens(ref)
			if err != nil {
				t.Fatalf("ReadTokens: %v", err)
			}
			if loaded.RefreshToken != synRT {
				t.Errorf("loaded RefreshToken = %q, want synthetic token", loaded.RefreshToken)
			}

			redactedJSON := string(ux.MarshalForFile(loaded))
			if strings.Contains(redactedJSON, synAT) || strings.Contains(redactedJSON, synRT) {
				t.Fatalf("ux.MarshalForFile failed to redact TokenSet: %s", redactedJSON)
			}
			if strings.Count(redactedJSON, "[REDACTED]") != 2 {
				t.Errorf("expected both access_token and refresh_token redacted, got: %s", redactedJSON)
			}
		})
	}
}

// TestMigrate_ExpiredLegacyAccessTokenAuthenticatesViaRefreshToken reproduces
// the #1384 end-to-end continuity scenario: a legacy profile whose access_token
// expired days ago but holds a valid refresh_token migrates cleanly and
// completes an authenticated call (`jentic api GET /me`), rotating the stored
// token pair in XDG.
func TestMigrate_ExpiredLegacyAccessTokenAuthenticatesViaRefreshToken(t *testing.T) {
	withXDG(t)

	var sawGrantType, sawRefreshToken, sawMeAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/oauth/token":
			var req struct {
				GrantType    string `json:"grant_type"`
				RefreshToken string `json:"refresh_token"`
			}
			_ = json.NewDecoder(r.Body).Decode(&req)
			sawGrantType = req.GrantType
			sawRefreshToken = req.RefreshToken
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(struct {
				AccessToken  string `json:"access_token"`
				RefreshToken string `json:"refresh_token"`
				TokenType    string `json:"token_type"`
				ExpiresIn    int    `json:"expires_in"`
			}{
				AccessToken:  "at-test-fresh-2",
				RefreshToken: "rt-test-fresh-2",
				TokenType:    "bearer",
				ExpiresIn:    1800,
			})
		case "/me":
			sawMeAuth = r.Header.Get("Authorization")
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"type":"agent","id":"agnt_default","name":"jentic-cli-default","status":"active"}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	app := testApp(t)
	t.Setenv("JENTIC_HOME", app.Paths.Root)
	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	der, _ := x509.MarshalPKCS8PrivateKey(priv)
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})
	tokBytes, _ := json.Marshal(&legacyTokens{
		AccessToken:     "at-test-expired-1",
		RefreshToken:    "rt-test-synthetic-1",
		AccessExpiresAt: time.Now().Add(-48 * time.Hour).UTC().Round(time.Second),
	})
	writeLegacyProfileFiles(t, app.Paths, "default", &legacyMeta{
		BaseURL: srv.URL,
		AgentID: "agnt_default",
	}, map[string][]byte{
		legacyKeyFile:    pemBytes,
		legacyTokensFile: tokBytes,
	})

	if _, err := runMigrate(app, false); err != nil {
		t.Fatalf("runMigrate: %v", err)
	}

	if _, _, err := captureCLIStreams(t, func() error {
		return runJentic(t, "api", "GET", "/me")
	}); err != nil {
		t.Fatalf("authenticated call after migrate failed: %v", err)
	}
	if sawGrantType != "refresh_token" || sawRefreshToken != "rt-test-synthetic-1" {
		t.Errorf("refresh exchange got grant_type=%q refresh_token=%q", sawGrantType, sawRefreshToken)
	}
	if sawMeAuth != "Bearer at-test-fresh-2" {
		t.Errorf("/me Authorization = %q, want Bearer at-test-fresh-2", sawMeAuth)
	}

	ref := auth.IdentityRef{Identity: "default", Environment: envNameFromURL(srv.URL)}
	rotated, err := auth.ReadTokens(ref)
	if err != nil {
		t.Fatalf("ReadTokens after rotation: %v", err)
	}
	if rotated.AccessToken != "at-test-fresh-2" || rotated.RefreshToken != "rt-test-fresh-2" {
		t.Errorf("rotated XDG tokens = %+v, want fresh pair", rotated)
	}
}

func captureCLIStreams(t *testing.T, fn func() error) (stdout, stderr string, err error) {
	t.Helper()
	oldOut, oldErr := os.Stdout, os.Stderr
	rOut, wOut, perr := os.Pipe()
	if perr != nil {
		t.Fatal(perr)
	}
	rErr, wErr, perr := os.Pipe()
	if perr != nil {
		t.Fatal(perr)
	}
	os.Stdout, os.Stderr = wOut, wErr
	defer func() {
		os.Stdout, os.Stderr = oldOut, oldErr
	}()

	err = fn()
	_ = wOut.Close()
	_ = wErr.Close()

	var bOut, bErr bytes.Buffer
	_, _ = io.Copy(&bOut, rOut)
	_, _ = io.Copy(&bErr, rErr)
	return bOut.String(), bErr.String(), err
}

func TestMigrate_NoProfilesIsNoOp(t *testing.T) {
	withXDG(t)
	app := testApp(t)
	res, err := runMigrate(app, false)
	if err != nil {
		t.Fatalf("migrate with no profiles should not error: %v", err)
	}
	if len(res.contexts) != 0 {
		t.Errorf("expected zero migrated contexts, got %v", res.contexts)
	}
	// Marker is still dropped so the legacy-read adapter stops firing.
	if _, err := os.Stat(filepath.Join(app.Paths.Root, migratedMarkerName)); err != nil {
		t.Errorf("marker should be written even with nothing to migrate: %v", err)
	}
}

func TestMigrate_PurgeLegacy(t *testing.T) {
	withXDG(t)
	app := testApp(t)
	seedLegacyDCRProfile(t, app.Paths, "work", "https://api.jentic.com")

	res, err := runMigrate(app, true)
	if err != nil {
		t.Fatalf("migrate --purge-legacy: %v", err)
	}
	if !res.purged {
		t.Error("result should report purged=true")
	}
	if _, err := os.Stat(app.Paths.Root); !os.IsNotExist(err) {
		t.Errorf("legacy tree should be removed after --purge-legacy (err=%v)", err)
	}
}

func TestSanitizeName(t *testing.T) {
	cases := map[string]string{
		"work":           "work",
		"My_Profile":     "my-profile",
		"api.jentic.com": "api-jentic-com",
		"_leading":       "leading",
		"trailing_":      "trailing",
		"":               "default",
		"UPPER":          "upper",
		"123":            "123",
	}
	for in, want := range cases {
		if got := sanitizeName(in); got != want {
			t.Errorf("sanitizeName(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestEnvNameFromURL(t *testing.T) {
	cases := map[string]string{
		"https://api.jentic.com":             "api-jentic-com",
		"http://127.0.0.1:8000":              "127-0-0-1",
		"https://staging.example.com:8443/x": "staging-example-com",
		"":                                   "default",
	}
	for in, want := range cases {
		if got := envNameFromURL(in); got != want {
			t.Errorf("envNameFromURL(%q) = %q, want %q", in, got, want)
		}
	}
}
