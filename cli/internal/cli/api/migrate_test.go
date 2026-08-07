package api

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/pem"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	legacyconfig "github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/profile"
)

// seedLegacyProfile writes a legacy DCR profile (agent.key + profile.yaml +
// tokens.json) under paths, returning nothing. It mirrors what `jentic register`
// leaves on disk so migrate has real material to copy.
func seedLegacyDCRProfile(t *testing.T, paths legacyconfig.Paths, name, baseURL string) {
	t.Helper()
	p, err := profile.Open(paths, name)
	if err != nil {
		t.Fatal(err)
	}
	if err := p.SaveMeta(&profile.Meta{
		BaseURL: baseURL,
		AgentID: "agnt_" + name,
		KID:     "jentic-cli-" + name,
	}); err != nil {
		t.Fatal(err)
	}
	// Ed25519 PKCS#8 PEM key, exactly as agentkey.Save writes it.
	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	der, _ := x509.MarshalPKCS8PrivateKey(priv)
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})
	if err := os.WriteFile(p.KeyPath(), pemBytes, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := p.SaveTokens(&profile.Tokens{
		AccessToken:     "access-" + name,
		RefreshToken:    "refresh-should-be-dropped",
		AccessExpiresAt: time.Now().Add(time.Hour),
	}); err != nil {
		t.Fatal(err)
	}
}

func TestMigrate_CopiesProfilesToXDG(t *testing.T) {
	withXDG(t)
	app := testApp(t)
	legacyPaths := app.Paths

	// Seed two legacy profiles and mark one the default.
	seedLegacyDCRProfile(t, legacyPaths, "work", "https://api.jentic.com")
	seedLegacyDCRProfile(t, legacyPaths, "staging", "https://staging.jentic.com:8443")
	if err := legacyconfig.SetDefaultProfile(legacyPaths, "work"); err != nil {
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

func TestMigrate_DropsRefreshToken(t *testing.T) {
	withXDG(t)
	app := testApp(t)
	seedLegacyDCRProfile(t, app.Paths, "work", "https://api.jentic.com")

	if _, err := runMigrate(app, false); err != nil {
		t.Fatal(err)
	}

	ref := auth.IdentityRef{Identity: "work", Environment: "api-jentic-com"}
	toks, err := auth.ReadTokens(ref)
	if err != nil {
		t.Fatalf("reading migrated tokens: %v", err)
	}
	if toks.AccessToken != "access-work" {
		t.Errorf("access token = %q, want access-work", toks.AccessToken)
	}
	// The V2 TokenSet has no refresh field at all — the refresh token is dropped
	// by construction (BC-6). Assert the persisted file carries no refresh value.
	stateDir, _ := sdkconfig.StateDir()
	data, _ := os.ReadFile(filepath.Join(stateDir, "work_api-jentic-com_tokens.json"))
	if containsRefresh(string(data)) {
		t.Errorf("migrated token state must not contain a refresh token: %s", data)
	}
}

func TestMigrate_APIKeyProfile(t *testing.T) {
	withXDG(t)
	app := testApp(t)

	p, err := profile.Open(app.Paths, "apikeyprof")
	if err != nil {
		t.Fatal(err)
	}
	if err := p.SaveMeta(&profile.Meta{BaseURL: "https://api.jentic.com", AuthMode: profile.AuthModeAPIKey}); err != nil {
		t.Fatal(err)
	}
	if err := p.SaveAPIKey("jak_migratedkey"); err != nil {
		t.Fatal(err)
	}

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

// containsRefresh reports whether s mentions a refresh token key or value.
func containsRefresh(s string) bool {
	return strings.Contains(s, "refresh_token") || strings.Contains(s, "refresh-should-be-dropped")
}
