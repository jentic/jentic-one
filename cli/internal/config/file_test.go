package config

import (
	"os"
	"testing"
)

func writeConfig(t *testing.T, body string) Paths {
	t.Helper()
	paths := Paths{Root: t.TempDir()}
	if err := os.WriteFile(paths.ConfigPath(), []byte(body), 0o600); err != nil {
		t.Fatalf("write config: %v", err)
	}
	return paths
}

func TestLoadMissingFile(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	cfg, err := Load(paths)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Loaded {
		t.Errorf("Loaded should be false for missing file")
	}
	if cfg.Path != paths.ConfigPath() {
		t.Errorf("Path = %q, want %q", cfg.Path, paths.ConfigPath())
	}
}

func TestLoadPresentFile(t *testing.T) {
	paths := writeConfig(t, `
base_url: http://example:9000
default_profile: work
broker:
  scheme: http
  host: localhost:4000
`)
	cfg, err := Load(paths)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if !cfg.Loaded {
		t.Fatalf("Loaded should be true")
	}
	if cfg.BaseURL != "http://example:9000" || cfg.DefaultProfile != "work" {
		t.Errorf("unexpected top-level: %+v", cfg)
	}
	if cfg.Broker.Scheme != "http" || cfg.Broker.Host != "localhost:4000" {
		t.Errorf("unexpected broker: %+v", cfg.Broker)
	}
}

func TestLoadParseError(t *testing.T) {
	paths := writeConfig(t, "base_url: [not, a, string\n")
	if _, err := Load(paths); err == nil {
		t.Fatalf("expected parse error")
	}
}

func TestResolvedDefaults(t *testing.T) {
	cfg := &FileConfig{}
	if got := cfg.ResolvedBaseURL(); got != DefaultBaseURL {
		t.Errorf("ResolvedBaseURL = %q, want default", got)
	}
	if got := cfg.ResolvedDefaultProfile(); got != DefaultProfile {
		t.Errorf("ResolvedDefaultProfile = %q, want default", got)
	}
}

// TestResolvedBrokerURL guards against issue #657: broker.host is a bare
// host[:port] and the scheme is carried separately, so assembling
// scheme + "://" + host must yield a single well-formed URL — never a doubled
// scheme.
func TestResolvedBrokerURL(t *testing.T) {
	brokerURL := func(c *FileConfig) string {
		return c.ResolvedBrokerScheme("", false) + "://" + c.ResolvedBrokerHost("", false)
	}

	t.Run("defaults resolve to a single scheme", func(t *testing.T) {
		cfg := &FileConfig{}
		if got, want := brokerURL(cfg), "https://127.0.0.1:8100"; got != want {
			t.Errorf("default broker URL = %q, want %q", got, want)
		}
	})

	t.Run("bare host with scheme:http", func(t *testing.T) {
		cfg := &FileConfig{Broker: BrokerConfig{Scheme: "http", Host: "127.0.0.1:8100"}}
		if got, want := brokerURL(cfg), "http://127.0.0.1:8100"; got != want {
			t.Errorf("broker URL = %q, want %q", got, want)
		}
	})

	t.Run("host with accidental scheme is stripped", func(t *testing.T) {
		cfg := &FileConfig{Broker: BrokerConfig{Scheme: "https", Host: "https://127.0.0.1:8100"}}
		if got, want := brokerURL(cfg), "https://127.0.0.1:8100"; got != want {
			t.Errorf("broker URL = %q, want single scheme %q", got, want)
		}
	})

	t.Run("flag host with accidental scheme is stripped", func(t *testing.T) {
		cfg := &FileConfig{}
		if got := cfg.ResolvedBrokerHost("http://example:9000", true); got != "example:9000" {
			t.Errorf("ResolvedBrokerHost = %q, want scheme stripped", got)
		}
	})
}

func TestResolvedPrecedence(t *testing.T) {
	cfg := &FileConfig{
		Broker: BrokerConfig{Scheme: "http", Host: "cfg-host"},
	}

	if got := cfg.ResolvedBrokerScheme("https", false); got != "http" {
		t.Errorf("broker scheme config should win: got %q", got)
	}
	if got := cfg.ResolvedBrokerHost("flag-host", true); got != "flag-host" {
		t.Errorf("broker host flag should win: got %q", got)
	}

	if got := cfg.ResolvedProfileName("explicit"); got != "explicit" {
		t.Errorf("profile flag should win: got %q", got)
	}
	if got := cfg.ResolvedBaseURLOr(""); got != DefaultBaseURL {
		t.Errorf("base url empty flag -> default: got %q", got)
	}
}

func TestResolvedProfilePrecedence(t *testing.T) {
	cfg := &FileConfig{DefaultProfile: "cfg"}

	t.Run("flag beats env and config", func(t *testing.T) {
		t.Setenv(ProfileEnv, "envprof")
		if got := cfg.ResolvedProfileName("flagprof"); got != "flagprof" {
			t.Errorf("flag should win: got %q", got)
		}
	})

	t.Run("env beats config", func(t *testing.T) {
		t.Setenv(ProfileEnv, "envprof")
		if got := cfg.ResolvedProfileName(""); got != "envprof" {
			t.Errorf("env should win over config: got %q", got)
		}
	})

	t.Run("config beats default when env unset", func(t *testing.T) {
		t.Setenv(ProfileEnv, "")
		if got := cfg.ResolvedProfileName(""); got != "cfg" {
			t.Errorf("config should win: got %q", got)
		}
	})

	t.Run("built-in default when all empty", func(t *testing.T) {
		t.Setenv(ProfileEnv, "")
		empty := &FileConfig{}
		if got := empty.ResolvedProfileName(""); got != DefaultProfile {
			t.Errorf("default should win: got %q", got)
		}
	})
}

func TestSaveRoundTrip(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	cfg := &FileConfig{
		BaseURL:        "http://example:9000",
		DefaultProfile: "work",
		Broker:         BrokerConfig{Scheme: "http", Host: "localhost:4000"},
	}
	if err := cfg.Save(paths); err != nil {
		t.Fatalf("Save: %v", err)
	}

	got, err := Load(paths)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if !got.Loaded {
		t.Fatalf("Loaded should be true after Save")
	}
	if got.BaseURL != cfg.BaseURL || got.DefaultProfile != cfg.DefaultProfile {
		t.Errorf("top-level mismatch: %+v", got)
	}
	if got.Broker != cfg.Broker {
		t.Errorf("nested mismatch: %+v", got)
	}
}

func TestSetDefaultProfile(t *testing.T) {
	paths := writeConfig(t, "base_url: http://example:9000\ndefault_profile: old\n")
	if err := SetDefaultProfile(paths, "new"); err != nil {
		t.Fatalf("SetDefaultProfile: %v", err)
	}
	got, err := Load(paths)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got.DefaultProfile != "new" {
		t.Errorf("DefaultProfile = %q, want new", got.DefaultProfile)
	}
	// Existing fields must survive the rewrite.
	if got.BaseURL != "http://example:9000" {
		t.Errorf("base_url not preserved: %q", got.BaseURL)
	}
}
