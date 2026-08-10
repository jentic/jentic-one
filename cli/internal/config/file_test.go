package config

import (
	"fmt"
	"os"
	"sync"
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

func TestAgentAccountRoundTrip(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	cfg, err := Load(paths)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.HasAgentUser() {
		t.Fatal("fresh config should have no agent user")
	}
	cfg.SetAgentAccount(AgentAccount{
		User:           "alice-local-agent",
		AccountCreated: true,
		Enabled:        true,
		HomeDir:        "/Users/Shared/alice-local-agent",
		ConfigDir:      "/Users/Shared/alice-local-agent/.jentic",
	})
	if !cfg.AddGrantedDir("/Users/Shared/alice-local-agent/work") {
		t.Fatal("expected AddGrantedDir to report a new grant")
	}
	if cfg.AddGrantedDir("/Users/Shared/alice-local-agent/work") {
		t.Fatal("expected duplicate AddGrantedDir to be idempotent")
	}
	if err := cfg.Save(paths); err != nil {
		t.Fatalf("Save: %v", err)
	}

	got, err := Load(paths)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	if !got.HasAgentUser() {
		t.Fatal("expected an enabled agent account after reload")
	}
	acct, ok := got.AgentAccount()
	if !ok {
		t.Fatal("agent account not persisted")
	}
	if acct.User != "alice-local-agent" || acct.ConfigDir == "" {
		t.Errorf("unexpected account: %+v", acct)
	}
	if !acct.AccountCreated || !acct.Enabled {
		t.Error("expected AccountCreated and Enabled to round-trip as true")
	}
	if len(acct.GrantedDirs) != 1 {
		t.Fatalf("granted dirs = %v", acct.GrantedDirs)
	}

	if !got.RemoveGrantedDir("/Users/Shared/alice-local-agent/work") {
		t.Fatal("expected RemoveGrantedDir to report removal")
	}
	if got.RemoveGrantedDir("/nope") {
		t.Fatal("did not expect removal of an absent dir")
	}
	acct, _ = got.AgentAccount()
	if len(acct.GrantedDirs) != 0 {
		t.Errorf("granted dirs after remove = %v", acct.GrantedDirs)
	}
}

// TestMutateReloadsBeforeApplying proves Mutate does a fresh read UNDER the lock,
// so a mutation applied to a STALE in-memory config (one loaded before another
// writer committed) doesn't clobber the committed change. Here a first Mutate adds
// grant A; a second Mutate — driven from a config loaded before A existed — adds
// grant B. Both must survive, because the second reloads (seeing A) before adding
// B rather than saving its stale two-grant-less snapshot.
func TestMutateReloadsBeforeApplying(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	base := &FileConfig{}
	base.SetAgentAccount(AgentAccount{User: "a", AccountCreated: true, Enabled: true})
	if err := base.Save(paths); err != nil {
		t.Fatalf("seed save: %v", err)
	}

	// Writer 1 commits grant A.
	if _, err := Mutate(paths, func(c *FileConfig) error {
		c.AddGrantedDir("/opt/a/A")
		return nil
	}); err != nil {
		t.Fatalf("mutate A: %v", err)
	}

	// Writer 2 adds grant B. Even though it's a fresh Mutate, its fn reloads the
	// on-disk config first, so it sees A and appends B rather than replacing.
	got, err := Mutate(paths, func(c *FileConfig) error {
		c.AddGrantedDir("/opt/a/B")
		return nil
	})
	if err != nil {
		t.Fatalf("mutate B: %v", err)
	}
	acct, _ := got.AgentAccount()
	if len(acct.GrantedDirs) != 2 {
		t.Fatalf("expected both grants to survive, got %v", acct.GrantedDirs)
	}
}

// TestMutateConcurrentContention exercises the advisory lock under REAL
// contention (review gap: the lock had no contention test, and the Windows
// LockFileEx path was never executed beyond smoke). N goroutines — separate
// lock-file descriptors, exactly like N concurrent jentic processes — each
// Mutate a distinct granted dir. The lock serialises the read-modify-write
// cycles, so ALL N grants must survive; any lost update means two writers
// interleaved inside their critical sections. Runs on every platform, so CI's
// Windows job exercises lock_windows.go for real.
func TestMutateConcurrentContention(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	base := &FileConfig{}
	base.SetAgentAccount(AgentAccount{User: "a", AccountCreated: true, Enabled: true})
	if err := base.Save(paths); err != nil {
		t.Fatalf("seed save: %v", err)
	}

	const writers = 16
	var wg sync.WaitGroup
	errs := make(chan error, writers)
	for i := range writers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := Mutate(paths, func(c *FileConfig) error {
				c.AddGrantedDir(fmt.Sprintf("/opt/a/dir-%02d", i))
				return nil
			})
			errs <- err
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatalf("concurrent Mutate: %v", err)
		}
	}

	got, err := Load(paths)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	acct, _ := got.AgentAccount()
	if len(acct.GrantedDirs) != writers {
		t.Fatalf("lost update under contention: %d/%d grants survived: %v",
			len(acct.GrantedDirs), writers, acct.GrantedDirs)
	}
}

// TestMutateErrorLeavesConfigUntouched proves a failing mutation does not write:
// the on-disk config is unchanged when fn returns an error.
func TestMutateErrorLeavesConfigUntouched(t *testing.T) {
	paths := writeConfig(t, "default_profile: keep\n")
	_, err := Mutate(paths, func(c *FileConfig) error {
		c.DefaultProfile = "clobbered"
		return os.ErrInvalid
	})
	if err == nil {
		t.Fatal("expected Mutate to propagate the fn error")
	}
	got, _ := Load(paths)
	if got.DefaultProfile != "keep" {
		t.Errorf("failed Mutate must not persist changes, got %q", got.DefaultProfile)
	}
}

// TestSaveLeavesNoTempFile proves the atomic write cleans up its temp file, so a
// successful Save leaves only config.yaml (plus the lock file if Mutate ran) and
// no stray .config-*.tmp.
func TestSaveLeavesNoTempFile(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	if err := (&FileConfig{DefaultProfile: "x"}).Save(paths); err != nil {
		t.Fatalf("Save: %v", err)
	}
	entries, err := os.ReadDir(paths.Dir())
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if len(e.Name()) >= 8 && e.Name()[:8] == ".config-" {
			t.Errorf("stray temp file left behind: %s", e.Name())
		}
	}
}
