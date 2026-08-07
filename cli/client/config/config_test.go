package config

import (
	"os"
	"path/filepath"
	"testing"

	"gopkg.in/yaml.v3"
)

// withEnvDirs points the XDG config/state dirs at a temp dir and clears the
// file-less env vars, so each test runs against an isolated, empty config root.
func withEnvDirs(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(dir, "config"))
	t.Setenv("XDG_STATE_HOME", filepath.Join(dir, "state"))
	// Clear file-less vars so a stray ambient value can't leak into a disk-path test.
	t.Setenv("JENTIC_BASE_URL", "")
	t.Setenv("JENTIC_BEARER_TOKEN", "")
	t.Setenv("JENTIC_BROKER_URL", "")
	t.Setenv("JENTIC_SESSION_ID", "")
	return dir
}

func TestPaths_XDGLayout(t *testing.T) {
	withEnvDirs(t)
	cfg, err := ConfigDir()
	if err != nil {
		t.Fatal(err)
	}
	if base := filepath.Base(cfg); base != "jentic" {
		t.Errorf("ConfigDir tail = %q, want jentic", base)
	}
	state, err := StateDir()
	if err != nil {
		t.Fatal(err)
	}
	if base := filepath.Base(state); base != "jentic" {
		t.Errorf("StateDir tail = %q, want jentic", base)
	}
	// State must not live under the config dir (they are separate XDG roots).
	if state == cfg {
		t.Errorf("StateDir and ConfigDir must differ, both = %q", cfg)
	}
}

// TestLoadState_FilelessRequiresBothVars is the impl/0.0 §2 item 3 contract: the
// file-less path activates ONLY when both JENTIC_BASE_URL and JENTIC_BEARER_TOKEN
// are set. Token or URL alone must fall through to disk resolution.
func TestLoadState_FilelessRequiresBothVars(t *testing.T) {
	t.Run("both set -> file-less, ignores absent disk config", func(t *testing.T) {
		withEnvDirs(t)
		t.Setenv("JENTIC_BASE_URL", "https://control.example")
		t.Setenv("JENTIC_BEARER_TOKEN", "tok-abc")
		t.Setenv("JENTIC_BROKER_URL", "https://broker.example")
		t.Setenv("JENTIC_SESSION_ID", "sess-1")

		st, err := LoadState("")
		if err != nil {
			t.Fatalf("LoadState: %v", err)
		}
		if st.BaseURL != "https://control.example" || st.InjectedBearerToken != "tok-abc" {
			t.Errorf("file-less state not populated: %+v", st)
		}
		if st.BrokerURL != "https://broker.example" {
			t.Errorf("BrokerURL = %q, want the injected broker URL", st.BrokerURL)
		}
		if st.SessionID != "sess-1" {
			t.Errorf("SessionID = %q, want sess-1", st.SessionID)
		}
		if st.IdentityName != FilelessIdentity || st.EnvironmentName != FilelessEnvironment {
			t.Errorf("file-less sentinels wrong: id=%q env=%q", st.IdentityName, st.EnvironmentName)
		}
		if st.PersistedMode != "agent" {
			t.Errorf("file-less PersistedMode = %q, want agent", st.PersistedMode)
		}
	})

	t.Run("token alone falls through to disk (no config -> error)", func(t *testing.T) {
		withEnvDirs(t)
		t.Setenv("JENTIC_BEARER_TOKEN", "tok-abc") // URL missing
		_, err := LoadState("")
		if err == nil {
			t.Fatal("expected no-configuration error when only the token is set")
		}
	})

	t.Run("url alone falls through to disk (no config -> error)", func(t *testing.T) {
		withEnvDirs(t)
		t.Setenv("JENTIC_BASE_URL", "https://control.example") // token missing
		_, err := LoadState("")
		if err == nil {
			t.Fatal("expected no-configuration error when only the base URL is set")
		}
	})
}

func TestLoadState_DiskContextResolution(t *testing.T) {
	withEnvDirs(t)
	// Seed a config via MutateConfig (exercises the writer too).
	if err := MutateConfig(func(c *Config) error {
		c.ActiveContext = "prod"
		c.Environments["prod-env"] = Env{BaseURL: "https://ctl.prod", BrokerURL: "https://brk.prod"}
		c.Identities["agent-1"] = Identity{Type: "agent"}
		c.Contexts["prod"] = Context{Environment: "prod-env", Identity: "agent-1", Mode: "agent"}
		c.Theme = "dark"
		return nil
	}); err != nil {
		t.Fatalf("seed MutateConfig: %v", err)
	}

	st, err := LoadState("")
	if err != nil {
		t.Fatalf("LoadState: %v", err)
	}
	if st.BaseURL != "https://ctl.prod" || st.BrokerURL != "https://brk.prod" {
		t.Errorf("URLs not resolved: %+v", st)
	}
	if st.IdentityName != "agent-1" || st.EnvironmentName != "prod-env" {
		t.Errorf("identity/env not resolved: %+v", st)
	}
	if st.PersistedMode != "agent" || st.PersistedTheme != "dark" {
		t.Errorf("persisted mode/theme wrong: mode=%q theme=%q", st.PersistedMode, st.PersistedTheme)
	}

	// cmdContextOverride selects a different context.
	if err := MutateConfig(func(c *Config) error {
		c.Environments["dev-env"] = Env{BaseURL: "https://ctl.dev"}
		c.Contexts["dev"] = Context{Environment: "dev-env", Identity: "agent-1", Mode: "human"}
		return nil
	}); err != nil {
		t.Fatalf("add dev context: %v", err)
	}
	st, err = LoadState("dev")
	if err != nil {
		t.Fatalf("LoadState(dev): %v", err)
	}
	if st.BaseURL != "https://ctl.dev" || st.PersistedMode != "human" {
		t.Errorf("override context not honored: %+v", st)
	}
}

func TestLoadState_MissingContextIsError(t *testing.T) {
	withEnvDirs(t)
	if err := MutateConfig(func(c *Config) error {
		c.ActiveContext = "ghost" // points at a context that doesn't exist
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadState(""); err == nil {
		t.Fatal("expected error for missing active context")
	}
}

// TestMutateConfig_PreservesUnknownKeys is the critical correctness property from
// impl/1.3 §2: a mutation must NOT strip keys the typed schema doesn't know
// (mixed-version configs, enterprise overlay extensions). A naive
// Unmarshal+Marshal round-trip would silently delete them.
func TestMutateConfig_PreservesUnknownKeys(t *testing.T) {
	dir := withEnvDirs(t)
	cfgDir := filepath.Join(dir, "config", "jentic")
	if err := os.MkdirAll(cfgDir, 0o700); err != nil {
		t.Fatal(err)
	}
	// Hand-write a config with a top-level key the Config struct has never heard of,
	// plus a nested unknown key under a known section.
	seed := `active_context: prod
enterprise_overlay:
  license_tier: gold
  extra:
    - a
    - b
environments:
  prod-env:
    base_url: https://ctl.prod
    x_unknown_env_field: keep-me
contexts:
  prod:
    environment: prod-env
    identity: agent-1
    mode: agent
identities:
  agent-1:
    type: agent
`
	if err := os.WriteFile(filepath.Join(cfgDir, "config.yaml"), []byte(seed), 0o600); err != nil {
		t.Fatal(err)
	}

	// Mutate a KNOWN field; the unknown top-level key must survive.
	if err := MutateConfig(func(c *Config) error {
		c.Theme = "light"
		return nil
	}); err != nil {
		t.Fatalf("MutateConfig: %v", err)
	}

	out, err := os.ReadFile(filepath.Join(cfgDir, "config.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]any
	if err := yaml.Unmarshal(out, &raw); err != nil {
		t.Fatalf("re-parse: %v", err)
	}
	if _, ok := raw["enterprise_overlay"]; !ok {
		t.Errorf("unknown top-level key enterprise_overlay was stripped:\n%s", out)
	}
	if raw["theme"] != "light" {
		t.Errorf("known mutation not applied; theme=%v", raw["theme"])
	}
	// Nested unknown key under a known section must also survive.
	envs, _ := raw["environments"].(map[string]any)
	prod, _ := envs["prod-env"].(map[string]any)
	if prod == nil || prod["x_unknown_env_field"] != "keep-me" {
		t.Errorf("nested unknown key x_unknown_env_field was stripped:\n%s", out)
	}
}

func TestMutateConfig_CreatesFreshConfig(t *testing.T) {
	withEnvDirs(t)
	if err := MutateConfig(func(c *Config) error {
		c.ActiveContext = "new"
		c.Contexts["new"] = Context{Environment: "e", Identity: "i", Mode: "human"}
		return nil
	}); err != nil {
		t.Fatalf("MutateConfig on fresh config: %v", err)
	}
	got, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if got.ActiveContext != "new" {
		t.Errorf("fresh config not persisted: %+v", got)
	}
}

func TestMutateConfig_PropagatesEntryDeletion(t *testing.T) {
	withEnvDirs(t)
	// Seed two contexts + envs.
	if err := MutateConfig(func(c *Config) error {
		c.Environments["e1"] = Env{BaseURL: "https://one"}
		c.Environments["e2"] = Env{BaseURL: "https://two"}
		c.Contexts["c1"] = Context{Environment: "e1", Identity: "i", Mode: "human"}
		c.Contexts["c2"] = Context{Environment: "e2", Identity: "i", Mode: "human"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	// Delete one context + one env; the removal must reach disk.
	if err := MutateConfig(func(c *Config) error {
		delete(c.Contexts, "c2")
		delete(c.Environments, "e2")
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	got, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := got.Contexts["c2"]; ok {
		t.Errorf("deleted context c2 still present: %+v", got.Contexts)
	}
	if _, ok := got.Environments["e2"]; ok {
		t.Errorf("deleted environment e2 still present: %+v", got.Environments)
	}
	if _, ok := got.Contexts["c1"]; !ok {
		t.Errorf("retained context c1 was lost: %+v", got.Contexts)
	}
}

func TestValidName(t *testing.T) {
	valid := []string{"prod", "a", "my-agent", "env-1", "x0"}
	invalid := []string{"", "-leading", "UPPER", "has_underscore", "has.dot", "jentic.file-less-agent", "../escape", "way-too-long-" + string(make([]byte, 70))}
	for _, n := range valid {
		if !ValidName(n) {
			t.Errorf("ValidName(%q) = false, want true", n)
		}
	}
	for _, n := range invalid {
		if ValidName(n) {
			t.Errorf("ValidName(%q) = true, want false", n)
		}
	}
}
