// Package profile manages per-agent profiles stored under ~/.jentic/profiles.
// Each profile holds an agent identity (key + registration metadata) and its
// cached access/refresh tokens. All secret material is written with 0600 perms.
package profile

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/internal/config"
	"gopkg.in/yaml.v3"
)

const (
	profileFileName = "profile.yaml"
	tokensFileName  = "tokens.json"
	keyFileName     = "agent.key"
	apiKeyFileName  = "apikey"
)

// Auth modes for a profile. An empty AuthMode is treated as AuthModeDCR for
// backward compatibility with profiles created before API-key support existed.
const (
	// AuthModeDCR uses an Ed25519 key + Dynamic Client Registration, exchanging
	// a signed JWT assertion for cached OAuth tokens.
	AuthModeDCR = "dcr"
	// AuthModeAPIKey uses a long-lived API key (jak_*) generated in the UI,
	// presented directly as the bearer credential.
	AuthModeAPIKey = "api_key"
)

// Meta is the non-secret profile metadata persisted to profile.yaml.
type Meta struct {
	// BaseURL is the control-plane base URL this agent is registered against.
	BaseURL string `yaml:"base_url"`
	// AgentID is the registered client/agent id (agnt_*).
	AgentID string `yaml:"agent_id"`
	// AgentName is the human-friendly client name used at registration.
	AgentName string `yaml:"agent_name"`
	// KID is the key id published in the JWKS and used in assertion headers.
	KID string `yaml:"kid"`
	// RegistrationAccessToken is the RFC 7592 management token from DCR.
	RegistrationAccessToken string `yaml:"registration_access_token,omitempty"`
	// AuthMode selects how this profile authenticates: AuthModeDCR (default,
	// empty) or AuthModeAPIKey. The API key itself lives in a separate 0600 file.
	AuthMode string `yaml:"auth_mode,omitempty"`
}

// IsAPIKey reports whether the profile authenticates with a stored API key.
func (m *Meta) IsAPIKey() bool { return m != nil && m.AuthMode == AuthModeAPIKey }

// Tokens is the cached token pair persisted to tokens.json.
type Tokens struct {
	AccessToken     string    `json:"access_token"`
	RefreshToken    string    `json:"refresh_token"`
	AccessExpiresAt time.Time `json:"access_expires_at"`
}

// Expired reports whether the access token is missing or within skew of expiry.
func (t *Tokens) Expired(skew time.Duration) bool {
	if t == nil || t.AccessToken == "" {
		return true
	}
	if t.AccessExpiresAt.IsZero() {
		return false
	}
	return time.Now().Add(skew).After(t.AccessExpiresAt)
}

// Profile is a handle to a single named profile's on-disk location.
type Profile struct {
	Name string
	dir  string
}

// Open returns a handle to the named profile, creating its directory (0700) if
// needed. An empty name resolves to the default profile.
func Open(paths config.Paths, name string) (*Profile, error) {
	if name == "" {
		name = config.DefaultProfile
	}
	dir := filepath.Join(paths.ProfilesDir(), name)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("create profile dir: %w", err)
	}
	return &Profile{Name: name, dir: dir}, nil
}

// List returns the names of all existing profiles.
func List(paths config.Paths) ([]string, error) {
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

// Dir returns the profile directory path.
func (p *Profile) Dir() string { return p.dir }

// KeyPath returns the path to the Ed25519 private key file.
func (p *Profile) KeyPath() string { return filepath.Join(p.dir, keyFileName) }

// LoadAPIKey reads the stored API key, returning "" if no key file exists.
func (p *Profile) LoadAPIKey() (string, error) {
	data, err := os.ReadFile(filepath.Join(p.dir, apiKeyFileName))
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return "", nil
		}
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}

// SaveAPIKey writes the API key atomically with 0600 perms.
func (p *Profile) SaveAPIKey(key string) error {
	return writeFileAtomic(filepath.Join(p.dir, apiKeyFileName), []byte(key), 0o600)
}

// LoadMeta reads profile.yaml, returning a zero Meta if the file is absent.
func (p *Profile) LoadMeta() (*Meta, error) {
	data, err := os.ReadFile(filepath.Join(p.dir, profileFileName))
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return &Meta{}, nil
		}
		return nil, err
	}
	m := &Meta{}
	if err := yaml.Unmarshal(data, m); err != nil {
		return nil, fmt.Errorf("parse %s: %w", profileFileName, err)
	}
	return m, nil
}

// SaveMeta writes profile.yaml atomically with 0600 perms.
func (p *Profile) SaveMeta(m *Meta) error {
	data, err := yaml.Marshal(m)
	if err != nil {
		return err
	}
	return writeFileAtomic(filepath.Join(p.dir, profileFileName), data, 0o600)
}

// LoadTokens reads tokens.json, returning nil if the file is absent.
func (p *Profile) LoadTokens() (*Tokens, error) {
	data, err := os.ReadFile(filepath.Join(p.dir, tokensFileName))
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return nil, nil
		}
		return nil, err
	}
	t := &Tokens{}
	if err := json.Unmarshal(data, t); err != nil {
		return nil, fmt.Errorf("parse %s: %w", tokensFileName, err)
	}
	return t, nil
}

// SaveTokens writes tokens.json atomically with 0600 perms.
func (p *Profile) SaveTokens(t *Tokens) error {
	data, err := json.MarshalIndent(t, "", "  ") //nolint:gosec // persisting tokens to the 0600 cache is the purpose here.
	if err != nil {
		return err
	}
	return writeFileAtomic(filepath.Join(p.dir, tokensFileName), data, 0o600)
}

// ClearTokens removes the cached token pair, if any.
func (p *Profile) ClearTokens() error {
	err := os.Remove(filepath.Join(p.dir, tokensFileName))
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return nil
}

// Delete removes the profile's entire on-disk directory — its key, tokens,
// metadata, and any API key — irreversibly. A missing directory is not an error
// (the profile is already gone). This is the storage half of a full identity
// teardown; callers are responsible for confirming the destruction first.
func (p *Profile) Delete() error {
	if err := os.RemoveAll(p.dir); err != nil {
		return fmt.Errorf("remove profile %q: %w", p.Name, err)
	}
	return nil
}

// writeFileAtomic writes data to a temp file in the same dir then renames it
// into place, ensuring the destination has the requested perms.
func writeFileAtomic(path string, data []byte, perm os.FileMode) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)

	if err := tmp.Chmod(perm); err != nil {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}
