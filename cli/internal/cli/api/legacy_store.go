package api

// legacy_store.go is the LAST reader of the V1 ~/.jentic/profiles layout.
// After the activation release removed the profile-based runtime (commands are
// context-only, enforced by the migrate gate), the only code allowed to touch
// the legacy tree is `jentic migrate` — and it must keep working even though
// the old internal/profile package is gone. This file carries the minimal
// READ-ONLY subset migrate needs: enumerate profile dirs and read their
// profile.yaml / tokens.json / apikey / agent.key. It never creates or writes
// anything under the legacy root (migration is copy-out, and the gate marker
// is written by migrate itself).

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/jentic/jentic-one/cli/internal/config"
)

// Legacy on-disk names, frozen as shipped by the V1 CLI.
const (
	legacyProfileFile = "profile.yaml"
	legacyTokensFile  = "tokens.json"
	legacyKeyFile     = "agent.key"
	legacyAPIKeyFile  = "apikey"

	// legacyAuthModeAPIKey marks a profile that authenticated with a jak_* key
	// instead of DCR; empty auth_mode means DCR (pre-API-key profiles).
	legacyAuthModeAPIKey = "api_key"
)

// legacyMeta mirrors the V1 profile.yaml shape (the fields migrate consumes).
type legacyMeta struct {
	BaseURL  string `yaml:"base_url"`
	AgentID  string `yaml:"agent_id"`
	AuthMode string `yaml:"auth_mode"`
}

func (m *legacyMeta) isAPIKey() bool { return m != nil && m.AuthMode == legacyAuthModeAPIKey }

// legacyTokens mirrors the V1 tokens.json shape. The refresh token is read but
// deliberately never migrated (BC-6).
type legacyTokens struct {
	AccessToken     string    `json:"access_token"`
	RefreshToken    string    `json:"refresh_token"`
	AccessExpiresAt time.Time `json:"access_expires_at"`
}

// legacyProfile is a read-only handle on one legacy profile directory.
type legacyProfile struct {
	name string
	dir  string
}

// listLegacyProfiles returns the names of the profile directories under the
// legacy root. A missing profiles dir is an empty store, not an error.
func listLegacyProfiles(paths config.Paths) ([]string, error) {
	entries, err := os.ReadDir(paths.ProfilesDir())
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return nil, nil
		}
		return nil, err
	}
	var names []string
	for _, e := range entries {
		if e.IsDir() {
			names = append(names, e.Name())
		}
	}
	return names, nil
}

// viewLegacyProfile returns a read-only handle without creating anything —
// migrate must leave the legacy tree byte-for-byte as it found it.
func viewLegacyProfile(paths config.Paths, name string) *legacyProfile {
	return &legacyProfile{name: name, dir: filepath.Join(paths.ProfilesDir(), name)}
}

// keyPath is the legacy PKCS#8 PEM Ed25519 key location.
func (p *legacyProfile) keyPath() string { return filepath.Join(p.dir, legacyKeyFile) }

// loadMeta reads profile.yaml; an absent file yields a zero Meta (an
// unregistered profile is still migratable as a bare context).
func (p *legacyProfile) loadMeta() (*legacyMeta, error) {
	data, err := os.ReadFile(filepath.Join(p.dir, legacyProfileFile))
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return &legacyMeta{}, nil
		}
		return nil, err
	}
	m := &legacyMeta{}
	if err := yaml.Unmarshal(data, m); err != nil {
		return nil, fmt.Errorf("parse %s: %w", legacyProfileFile, err)
	}
	return m, nil
}

// loadTokens reads tokens.json; absent file → nil (no cached token).
func (p *legacyProfile) loadTokens() (*legacyTokens, error) {
	data, err := os.ReadFile(filepath.Join(p.dir, legacyTokensFile))
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return nil, nil
		}
		return nil, err
	}
	t := &legacyTokens{}
	if err := json.Unmarshal(data, t); err != nil {
		return nil, fmt.Errorf("parse %s: %w", legacyTokensFile, err)
	}
	return t, nil
}

// loadAPIKey reads the stored jak_* key; absent file → "".
func (p *legacyProfile) loadAPIKey() (string, error) {
	data, err := os.ReadFile(filepath.Join(p.dir, legacyAPIKeyFile))
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return "", nil
		}
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}
