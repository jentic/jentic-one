package api

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
)

// withXDG points the XDG config/state dirs at a fresh temp root and clears the
// file-less env vars, so each command test mutates an isolated config.yaml. It
// also forces human mode so the fenced management commands are not blocked.
func withXDG(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(dir, "config"))
	t.Setenv("XDG_STATE_HOME", filepath.Join(dir, "state"))
	// Isolate the legacy ~/.jentic root too: clictx's legacy-read adapter resolves
	// it via $JENTIC_HOME, so without this a developer's real ~/.jentic would leak
	// into command tests (e.g. resolving a stray default identity/base URL).
	t.Setenv("JENTIC_HOME", filepath.Join(dir, "jentic-home"))
	t.Setenv("JENTIC_BASE_URL", "")
	t.Setenv("JENTIC_BEARER_TOKEN", "")
	t.Setenv("JENTIC_MODE", "human")
	t.Setenv("JENTIC_CONTEXT", "")
	t.Setenv("JENTIC_THEME", "")
}

// runJentic executes the jentic root with args against an isolated App, returning
// any error. Output goes to buffers (Render/ReportError write to os.Stdout/Stderr
// directly, so tests assert on config state, not captured output).
func runJentic(t *testing.T, args ...string) error {
	t.Helper()
	app := testApp(t)
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs(args)
	return root.Execute()
}

func TestEnvAdd_WritesEnvironment(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "env", "add", "prod", "--url", "https://api.example.com", "--broker-url", "https://broker.example.com"); err != nil {
		t.Fatalf("env add: %v", err)
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	e, ok := cfg.Environments["prod"]
	if !ok {
		t.Fatal("environment prod not written")
	}
	if e.BaseURL != "https://api.example.com" || e.BrokerURL != "https://broker.example.com" {
		t.Errorf("env fields = %+v", e)
	}
}

func TestEnvAdd_RejectsInvalidName(t *testing.T) {
	withXDG(t)
	err := runJentic(t, "env", "add", "Bad_Name", "--url", "https://x")
	if err == nil {
		t.Fatal("expected an error for an invalid name")
	}
}

func TestEnvAdd_RefusesOverwriteWithoutForce(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "env", "add", "prod", "--url", "https://a"); err != nil {
		t.Fatal(err)
	}
	if err := runJentic(t, "env", "add", "prod", "--url", "https://b"); err == nil {
		t.Fatal("expected env add to refuse overwriting an existing env")
	}
	// --force replaces.
	if err := runJentic(t, "env", "add", "prod", "--url", "https://b", "--force"); err != nil {
		t.Fatalf("env add --force: %v", err)
	}
	cfg, _ := sdkconfig.Load()
	if cfg.Environments["prod"].BaseURL != "https://b" {
		t.Errorf("--force did not replace: %+v", cfg.Environments["prod"])
	}
}

func TestEnvDelete_RefusesWhenReferenced(t *testing.T) {
	withXDG(t)
	seedContext(t)
	// env "prod" is referenced by context "main"; delete must refuse.
	if err := runJentic(t, "env", "delete", "prod", "--yes"); err == nil {
		t.Fatal("expected env delete to refuse a referenced environment")
	}
}

func TestIdentityAdd_WritesIdentityAndAPIKey(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "env", "add", "prod", "--url", "https://a"); err != nil {
		t.Fatal(err)
	}
	if err := runJentic(t, "identity", "add", "ci", "--type", "user", "--env", "prod", "--api-key", "jak_secret123"); err != nil {
		t.Fatalf("identity add: %v", err)
	}
	cfg, _ := sdkconfig.Load()
	id, ok := cfg.Identities["ci"]
	if !ok || id.Type != "user" {
		t.Fatalf("identity ci = %+v (ok=%v)", id, ok)
	}
	// The secret is stored under XDG state, NOT in config.yaml.
	stateDir, _ := sdkconfig.StateDir()
	if _, err := os.Stat(filepath.Join(stateDir, "ci_prod.apikey")); err != nil {
		t.Errorf("api key credential not written to state: %v", err)
	}
}

func TestIdentityAdd_APIKeyRequiresEnv(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "identity", "add", "ci", "--api-key", "jak_x"); err == nil {
		t.Fatal("expected --api-key without --env to error")
	}
}

// TestIdentityAdd_BadAPIKeyPrefixDoesNotPersistIdentity guards F8-24: a key that
// fails the jak_ prefix check must be rejected BEFORE the identity is written, so
// a bad --api-key never leaves a credential-less identity behind.
func TestIdentityAdd_BadAPIKeyPrefixDoesNotPersistIdentity(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "env", "add", "prod", "--url", "https://a"); err != nil {
		t.Fatal(err)
	}
	if err := runJentic(t, "identity", "add", "ci", "--env", "prod", "--api-key", "not-a-jak-key"); err == nil {
		t.Fatal("expected a bad --api-key prefix to error")
	}
	cfg, _ := sdkconfig.Load()
	if _, ok := cfg.Identities["ci"]; ok {
		t.Error("identity was persisted despite an invalid --api-key (orphan credential-less identity)")
	}
}

func TestContextCreate_RequiresExistingEnvAndIdentity(t *testing.T) {
	withXDG(t)
	// Neither env nor identity exists yet.
	if err := runJentic(t, "context", "create", "main", "--env", "prod", "--identity", "ci"); err == nil {
		t.Fatal("expected context create to fail with a missing environment")
	}
}

func TestContextCreateUseDelete_Lifecycle(t *testing.T) {
	withXDG(t)
	seedContext(t)

	cfg, _ := sdkconfig.Load()
	if cfg.ActiveContext != "main" {
		t.Fatalf("active context = %q, want main (created with --use)", cfg.ActiveContext)
	}

	// Cannot delete the active context.
	if err := runJentic(t, "context", "delete", "main", "--yes"); err == nil {
		t.Fatal("expected refusal to delete the active context")
	}

	// Create a second context and switch to it, then delete the first.
	if err := runJentic(t, "context", "create", "other", "--env", "prod", "--identity", "ci"); err != nil {
		t.Fatal(err)
	}
	if err := runJentic(t, "context", "use", "other"); err != nil {
		t.Fatal(err)
	}
	if err := runJentic(t, "context", "delete", "main", "--yes"); err != nil {
		t.Fatalf("context delete: %v", err)
	}
	cfg, _ = sdkconfig.Load()
	if _, ok := cfg.Contexts["main"]; ok {
		t.Error("context main should be deleted")
	}
	if cfg.ActiveContext != "other" {
		t.Errorf("active context = %q, want other", cfg.ActiveContext)
	}
}

func TestContextUse_UnknownErrors(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "context", "use", "nope"); err == nil {
		t.Fatal("expected context use of an unknown context to error")
	}
}

func TestThemeSet_PersistsTheme(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "theme", "light"); err != nil {
		t.Fatalf("theme: %v", err)
	}
	cfg, _ := sdkconfig.Load()
	if cfg.Theme != "light" {
		t.Errorf("theme = %q, want light", cfg.Theme)
	}
	if err := runJentic(t, "theme", "nonsense"); err == nil {
		t.Fatal("expected an unknown theme to error")
	}
}

// seedContext creates env "prod", identity "ci", and an active context "main".
func seedContext(t *testing.T) {
	t.Helper()
	if err := runJentic(t, "env", "add", "prod", "--url", "https://api.example.com"); err != nil {
		t.Fatal(err)
	}
	if err := runJentic(t, "identity", "add", "ci"); err != nil {
		t.Fatal(err)
	}
	if err := runJentic(t, "context", "create", "main", "--env", "prod", "--identity", "ci", "--use"); err != nil {
		t.Fatal(err)
	}
}
