package config

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

// ConfigName is the CLI's own settings file under ~/.jentic.
const ConfigName = "config.yaml"

// BrokerConfig is the broker target section of config.yaml. Scheme and Host are
// kept separate: Scheme is "http" or "https"; Host is a bare host[:port] with no
// scheme (the URL is assembled as scheme + "://" + host).
type BrokerConfig struct {
	Scheme string `yaml:"scheme"`
	Host   string `yaml:"host"`
}

// FileConfig is the on-disk ~/.jentic/config.yaml schema. All fields are
// optional; unset fields fall back to defaults / command-line flags.
type FileConfig struct {
	// BaseURL is the Jentic control-plane (auth surface) base URL used for agent
	// registration and token minting.
	BaseURL string `yaml:"base_url"`
	// Broker is the would-be forward target (logged only in this POC).
	Broker BrokerConfig `yaml:"broker"`
	// Telemetry holds the user's telemetry consent decision. HasConsented
	// records whether the user has been asked; Enabled records their answer.
	// Both are written together so the CLI never re-prompts after a decision.
	Telemetry TelemetryConfig `yaml:"telemetry"`
	// Agent is the LEGACY location of the shared agent-account record. Current
	// releases keep it in the XDG agent state (~/.config/jentic/agent-account.yaml
	// — see AgentState); this field remains only so records written by older
	// releases can be read (LoadAgentState's fallback) and adopted (the first
	// MutateAgentState moves them across and clears this). Nothing writes it.
	Agent *AgentAccount `yaml:"agent_account,omitempty"`
	// SameUserNoticeSeen is the LEGACY location of the one-time same-user launch
	// notice flag. Like Agent it is read-only fallback state; the live flag is in
	// the XDG agent state.
	SameUserNoticeSeen bool `yaml:"same_user_notice_seen,omitempty"`

	// Path records where the config was loaded from (empty if no file existed).
	Path string `yaml:"-"`
	// Loaded reports whether a config file was actually found and parsed.
	Loaded bool `yaml:"-"`
}

// AgentAccount is the one dedicated Unix account all of this operator's local
// coding agents share — the true credential boundary between the agents and the
// operator's secrets. A human provisions it once; every `jentic run`, whatever
// the agent binary, goes through it. Nothing here is secret (the agents' keys/
// tokens live in the account's own home), so it sits safely in the operator's
// XDG agent state (~/.config/jentic/agent-account.yaml — see AgentState), which
// is also the only place the grant list may live: ~/.config/jentic is a
// hard-banned grant target and sandbox-denied, so the agents can't edit their
// own access.
type AgentAccount struct {
	// User is the OS account name `jentic run` sudo's to (<operator>-local-agent).
	User string `yaml:"user"`
	// AccountCreated records whether jentic provisioned the dedicated Unix account
	// (the true boundary), as opposed to the operator declining and running the
	// agent same-user. The whole isolated posture keys off it: false means the CLI
	// behaves exactly as it does for a user with no agent account.
	AccountCreated bool `yaml:"account_created,omitempty"`
	// Enabled records whether the agent account is currently in use. It is set
	// true when the account is created and flipped false when a reset deletes the
	// account. There is no command to toggle it today — it is reserved as the
	// single place a future soft-pause would read from; behaviour currently keys
	// off AccountCreated.
	Enabled bool `yaml:"enabled,omitempty"`
	// HomeDir is the account's home directory (the always-accessible working
	// space; a session opens here unless a directory is granted).
	HomeDir string `yaml:"home_dir,omitempty"`
	// ConfigDir is a LEGACY reference to the account's own ~/.jentic (typically
	// <HomeDir>/.jentic) — the V1 home of the agents' registered identities and
	// profiles. New accounts no longer record it (the agent's identity is
	// exported into its home's own XDG store instead); it survives here so
	// `jentic migrate` can rescue profiles from an old agent home and so reset
	// keeps validating records written by older releases. Empty for accounts
	// created by current releases.
	ConfigDir string `yaml:"config_dir,omitempty"`
	// GrantedDirs is the consolidated inventory of directories the account has
	// been granted read/write access to (durable ACLs on disk). The ACLs are
	// physically one set — same uid — so this is one list regardless of which
	// agent binary made the grant. The on-disk ACL is the source of truth.
	GrantedDirs []string `yaml:"granted_dirs,omitempty"`
	// CreatedAt is when the account was first recorded (RFC3339).
	CreatedAt string `yaml:"created_at,omitempty"`
}

// TelemetryConfig is the telemetry consent section of config.yaml.
type TelemetryConfig struct {
	// HasConsented records whether the user has been presented with and
	// responded to the telemetry consent prompt. When false the wizard
	// will ask; when true it respects the Enabled answer.
	HasConsented bool `yaml:"has_consented"`
	// Enabled is the user's consent answer: true means telemetry is on.
	Enabled bool `yaml:"enabled"`
}

// ResolvedBaseURL returns the configured base URL or the default.
func (c *FileConfig) ResolvedBaseURL() string {
	if c.BaseURL != "" {
		return c.BaseURL
	}
	return DefaultBaseURL
}

// The Resolved* helpers below implement the precedence defaults < config.yaml <
// flag. flagChanged reports whether the caller's flag was explicitly set; when
// true the flag wins outright, otherwise the file value is used if present,
// falling back to the built-in default.

// ResolvedBrokerScheme resolves the (logged) broker target scheme.
func (c *FileConfig) ResolvedBrokerScheme(flag string, flagChanged bool) string {
	if flagChanged {
		return flag
	}
	if c.Broker.Scheme != "" {
		return c.Broker.Scheme
	}
	return DefaultBrokerScheme
}

// ResolvedBrokerHost resolves the (logged) broker target host. The returned
// value is always a bare host[:port] with no scheme: callers assemble the URL
// as scheme + "://" + host (see ResolvedBrokerScheme). For tolerance, a leading
// scheme in a hand-written config (or flag) is stripped so a value like
// "https://127.0.0.1:8100" still yields a single well-formed URL rather than a
// doubled scheme.
func (c *FileConfig) ResolvedBrokerHost(flag string, flagChanged bool) string {
	if flagChanged {
		return stripScheme(flag)
	}
	if c.Broker.Host != "" {
		return stripScheme(c.Broker.Host)
	}
	return DefaultBrokerHost
}

// stripScheme removes a leading "scheme://" prefix from a host value so the
// broker.host field is tolerant of an accidentally-included scheme. The scheme
// is carried separately in broker.scheme; keeping host bare avoids emitting a
// doubled scheme (e.g. https://https://…) when the URL is assembled.
func stripScheme(host string) string {
	if i := strings.Index(host, "://"); i != -1 {
		return host[i+len("://"):]
	}
	return host
}

// ResolvedBaseURLOr resolves the control-plane base URL: the flag if non-empty,
// otherwise the configured base URL (or the built-in default).
func (c *FileConfig) ResolvedBaseURLOr(flag string) string {
	if flag != "" {
		return flag
	}
	return c.ResolvedBaseURL()
}

// Load reads <paths>/config.yaml. A missing file is not an error: it returns a
// zero-value config with Loaded=false.
func Load(paths Paths) (*FileConfig, error) {
	path := paths.ConfigPath()

	data, err := os.ReadFile(path) //nolint:gosec // path is derived from the CLI's own JENTIC_HOME, not user input.
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return &FileConfig{Path: path}, nil
		}
		return nil, fmt.Errorf("read %s: %w", path, err)
	}

	cfg := &FileConfig{}
	if err := yaml.Unmarshal(data, cfg); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	cfg.Path = path
	cfg.Loaded = true
	return cfg, nil
}

// Save writes the config to <paths>/config.yaml (0600). It marshals the known
// fields, so any hand-added comments in an existing file are not preserved —
// this is the CLI's own settings file, written by commands like `jentic profile
// use`.
//
// The write is ATOMIC: it lands in a temp file in the same directory, fsyncs it,
// then renames it over config.yaml (rename is atomic on POSIX). A crash mid-write
// therefore leaves either the old file or the new one, never a half-written config
// that would fail to parse. Concurrent writers should hold the lock via Mutate;
// the atomic rename here bounds the damage when two writes race regardless.
func (c *FileConfig) Save(paths Paths) error {
	if _, err := paths.Ensure(paths.Dir()); err != nil {
		return err
	}
	data, err := yaml.Marshal(c)
	if err != nil {
		return err
	}
	return writeFileAtomic(paths.ConfigPath(), data, 0o600)
}

// writeFileAtomic writes data to path via a same-directory temp file that is
// fsynced and then renamed over path, so a reader (or a crash) never observes a
// partial write. The temp file is created with perm and removed on any error
// before the rename.
func writeFileAtomic(path string, data []byte, perm os.FileMode) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".config-*.tmp")
	if err != nil {
		return fmt.Errorf("create temp config in %s: %w", dir, err)
	}
	tmpName := tmp.Name()
	// Clean up the temp file on any failure path before the successful rename.
	defer func() {
		if tmpName != "" {
			_ = os.Remove(tmpName)
		}
	}()
	if err := tmp.Chmod(perm); err != nil {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		return fmt.Errorf("rename temp config over %s: %w", path, err)
	}
	tmpName = "" // renamed successfully — nothing to clean up
	return nil
}

// Mutate performs a race-safe read-modify-write of config.yaml: it takes an
// exclusive file lock, reloads the config from disk UNDER that lock (so it sees
// any change a concurrent writer committed since the caller last loaded), applies
// fn, and saves atomically before releasing the lock. fn receives the
// freshly-reloaded config and mutates it in place; the reloaded config is also
// returned so callers can observe the committed result. (The agent-account
// record that motivated this has moved to the XDG agent state — see
// MutateAgentState, which follows the same discipline.)
func Mutate(paths Paths, fn func(*FileConfig) error) (*FileConfig, error) {
	if _, err := paths.Ensure(paths.Dir()); err != nil {
		return nil, err
	}
	unlock, err := lockConfig(paths)
	if err != nil {
		return nil, err
	}
	defer unlock()

	cfg, err := Load(paths)
	if err != nil {
		return nil, err
	}
	if err := fn(cfg); err != nil {
		return nil, err
	}
	if err := cfg.Save(paths); err != nil {
		return nil, err
	}
	return cfg, nil
}

// AgentAccount returns the LEGACY agent account recorded in this config and
// whether one is present. Live reads go through AgentState (LoadAgentState);
// this getter remains for the legacy fallback and for `jentic migrate`, which
// rescues profiles from an old agent home via the recorded ConfigDir.
func (c *FileConfig) AgentAccount() (AgentAccount, bool) {
	if c.Agent == nil {
		return AgentAccount{}, false
	}
	return *c.Agent, true
}
