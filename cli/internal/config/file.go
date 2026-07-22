package config

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
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
	// DefaultProfile selects which profile commands act on when none is given.
	DefaultProfile string `yaml:"default_profile"`
	// Broker is the would-be forward target (logged only in this POC).
	Broker BrokerConfig `yaml:"broker"`
	// Telemetry holds the user's telemetry consent decision. HasConsented
	// records whether the user has been asked; Enabled records their answer.
	// Both are written together so the CLI never re-prompts after a decision.
	Telemetry TelemetryConfig `yaml:"telemetry"`

	// Path records where the config was loaded from (empty if no file existed).
	Path string `yaml:"-"`
	// Loaded reports whether a config file was actually found and parsed.
	Loaded bool `yaml:"-"`
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

// ResolvedDefaultProfile returns the configured default profile or the default.
func (c *FileConfig) ResolvedDefaultProfile() string {
	if c.DefaultProfile != "" {
		return c.DefaultProfile
	}
	return DefaultProfile
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

// ResolvedProfileName resolves the profile to act on, in precedence order: the
// flag if non-empty, else the JENTIC_PROFILE env var, else the configured
// default profile (or the built-in default).
func (c *FileConfig) ResolvedProfileName(flag string) string {
	if flag != "" {
		return flag
	}
	if env := os.Getenv(ProfileEnv); env != "" {
		return env
	}
	return c.ResolvedDefaultProfile()
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
func (c *FileConfig) Save(paths Paths) error {
	if _, err := paths.Ensure(paths.Dir()); err != nil {
		return err
	}
	data, err := yaml.Marshal(c)
	if err != nil {
		return err
	}
	path := paths.ConfigPath()
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return nil
}

// SetDefaultProfile loads config.yaml, sets default_profile to name, and saves.
// It is the persisting half of `jentic profile use`.
func SetDefaultProfile(paths Paths, name string) error {
	cfg, err := Load(paths)
	if err != nil {
		return err
	}
	cfg.DefaultProfile = name
	return cfg.Save(paths)
}
