package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"gopkg.in/yaml.v3"
)

// Reserved sentinel identity/environment names for the file-less path. They use a
// "jentic." prefix containing a dot precisely so they can NEVER collide with a
// real on-disk identity/environment name (those are validated to a dot-free
// charset by ValidName), keeping the ephemeral path's token-state/key filenames
// disjoint from any disk-mode name (impl/1.2 §3, impl/4.1 §1).
const (
	FilelessIdentity    = "jentic.file-less-agent"
	FilelessEnvironment = "jentic.ephemeral"
)

// ResolvedState is the UX-free, SDK-owned result of config resolution. It carries
// exactly what client.Config needs to talk to the API. The CLI wraps this with
// its presentational Mode/Theme (impl/1.2 §3a); the SDK never interprets the
// Persisted* strings.
type ResolvedState struct {
	IdentityName        string
	EnvironmentName     string
	BaseURL             string
	BrokerURL           string // Data Plane / Broker endpoint (Phase 5); separate from BaseURL
	CACertPath          string // optional custom CA bundle for TLS verification (SEC-3)
	InjectedBearerToken string
	SessionID           string // X-Jentic-Session-Id (telemetry, Phase 5)

	// PersistedMode / PersistedTheme are the raw strings read from config.yaml (or
	// env-derived defaults). The SDK does NOT act on them; the CLI wrapper
	// interprets them. They live here so the CLI need not re-parse the file.
	PersistedMode  string
	PersistedTheme string
}

// LoadState resolves the active configuration, prioritizing environment variables
// (the file-less path) over disk. cmdContextOverride, when non-empty, selects a
// context other than the persisted active one.
func LoadState(cmdContextOverride string) (*ResolvedState, error) {
	state := &ResolvedState{}

	// 1. FILE-LESS OVERRIDE (security first). If an orchestrator injected both the
	// base URL and a bearer token, we run entirely from memory — no disk, no key
	// material. Requiring BOTH (impl/0.0 §2 item 3) means a stray JENTIC_BASE_URL
	// alone falls through to disk resolution rather than half-configuring.
	envBaseURL := os.Getenv("JENTIC_BASE_URL")
	envToken := os.Getenv("JENTIC_BEARER_TOKEN")
	state.SessionID = os.Getenv("JENTIC_SESSION_ID") // optional telemetry tag (Phase 5)

	if envBaseURL != "" && envToken != "" {
		state.BaseURL = envBaseURL
		state.BrokerURL = os.Getenv("JENTIC_BROKER_URL") // required before any broker call (NewBroker errors without it)
		state.InjectedBearerToken = envToken
		state.PersistedMode = "agent"     // file-less defaults to agent
		state.PersistedTheme = "no-color" // file-less defaults to no-color
		state.IdentityName = FilelessIdentity
		state.EnvironmentName = FilelessEnvironment
		return state, nil
	}

	// 2. DISK FALLBACK (human / autonomous-agent lifecycle).
	dir, err := ConfigDir()
	if err != nil {
		return nil, err
	}
	file := filepath.Join(dir, "config.yaml")
	data, err := os.ReadFile(file) //nolint:gosec // path is ConfigDir()/config.yaml, CLI-managed, not user input.
	if err != nil {
		if os.IsNotExist(err) {
			// PRE-ACTIVATION COMPAT (Phase 2): released binaries must not hard-fail
			// for users who haven't migrated. Phase 2's legacy-read adapter (plan.md
			// Phase 2 item 1, 16 §2) will fall back to the legacy ~/.jentic profile
			// store here, read-only, until `jentic migrate`'s end-of-life (14 BC-1).
			// Until that adapter lands, no XDG config means no configuration.
			return nil, errors.New("no configuration found. Run 'jentic register --url <control-plane URL>' to onboard, or set JENTIC_BASE_URL and JENTIC_BEARER_TOKEN")
		}
		return nil, fmt.Errorf("reading %s: %w", file, err)
	}

	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parsing %s: %w", file, err)
	}

	// 3. CONTEXT RESOLUTION.
	activeCtxName := cfg.ActiveContext
	if cmdContextOverride != "" {
		activeCtxName = cmdContextOverride
	}
	if activeCtxName == "" {
		return nil, errors.New("no active context set. Run 'jentic context use <name>'")
	}
	ctx, ok := cfg.Contexts[activeCtxName]
	if !ok {
		return nil, fmt.Errorf("active context %q not found", activeCtxName)
	}
	env, ok := cfg.Environments[ctx.Environment]
	if !ok {
		return nil, fmt.Errorf("environment %q not found for context %q", ctx.Environment, activeCtxName)
	}

	state.IdentityName = ctx.Identity
	state.EnvironmentName = ctx.Environment
	state.BaseURL = env.BaseURL
	state.BrokerURL = env.BrokerURL // explicit; not derived from BaseURL (Phase 5)
	state.CACertPath = env.CACertPath
	state.PersistedMode = ctx.Mode // raw string; CLI interprets it
	state.PersistedTheme = cfg.Theme

	// Fetching a disk-cached bearer token via the identity is Phase 4 (auth); the
	// request editor performs the RFC 7523 exchange lazily, so LoadState leaves
	// InjectedBearerToken empty on the disk path.
	return state, nil
}

// Load reads and parses ~/.config/jentic/config.yaml WITHOUT resolving the active
// context. It is the read-only counterpart to MutateConfig, used by helpers that
// need the raw config (e.g. clientIDFor in Phase 4.2). Returns an empty Config
// (not an error) when the file does not exist yet.
func Load() (*Config, error) {
	dir, err := ConfigDir()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(filepath.Join(dir, "config.yaml")) //nolint:gosec // path is ConfigDir()/config.yaml, CLI-managed, not user input.
	if err != nil {
		if os.IsNotExist(err) {
			return &Config{}, nil
		}
		return nil, fmt.Errorf("reading config.yaml: %w", err)
	}
	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parsing config.yaml: %w", err)
	}
	return &cfg, nil
}
