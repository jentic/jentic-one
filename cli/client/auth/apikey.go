package auth

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/jentic/jentic-one/cli/client/config"
)

// APIKeyPrefix is the required prefix for a Jentic API key credential. It mirrors
// the shipped V1 constant so migrated keys and freshly-added keys validate the
// same way.
const APIKeyPrefix = "jak_"

// apiKeyRecord is the on-disk JSON envelope for a stored jak_* API key,
// binding the secret to the issuing backend origin.
type apiKeyRecord struct {
	APIKey string `json:"api_key"`
	Origin string `json:"origin,omitempty"`
}

// apiKeyPath returns the on-disk path of the API-key credential for ref, under
// the XDG STATE dir (a secret, not config — kept out of config.yaml exactly like
// tokens). The dir is created 0700; the stem is the same validated
// <identity>_<env> as keys/tokens, so a single ref addresses all three.
func apiKeyPath(ref IdentityRef) (string, error) {
	stateDir, err := config.StateDir()
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		return "", fmt.Errorf("creating state dir: %w", err)
	}
	stem, err := ref.Stem() // validates names — path-traversal guard
	if err != nil {
		return "", err
	}
	return filepath.Join(stateDir, stem+".apikey"), nil
}

// SaveAPIKey persists a jak_* API key credential for ref (0600), recording the
// configured origin of ref.Environment when available. It is the V2
// successor to V1's per-profile `apikey` file: the credential is a first-class
// identity credential (Phase 4 item 4) but stored as a secret under XDG state,
// never in config.yaml (which round-trips through node merges and is not
// secret-safe). Returns an error if the key lacks the required prefix.
func SaveAPIKey(ref IdentityRef, key string) error {
	return SaveAPIKeyForOrigin(ref, key, "")
}

// SaveAPIKeyForOrigin persists a jak_* API key credential for ref bound to
// rawOrigin (falling back to ref.Environment's configured base_url origin when
// rawOrigin is empty).
func SaveAPIKeyForOrigin(ref IdentityRef, key, rawOrigin string) error {
	key = strings.TrimSpace(key)
	if !strings.HasPrefix(key, APIKeyPrefix) {
		return fmt.Errorf("API key must start with %q", APIKeyPrefix)
	}
	path, err := apiKeyPath(ref)
	if err != nil {
		return err
	}
	origin := NormalizeOrigin(rawOrigin)
	if origin == "" {
		origin = configuredEnvOrigin(ref.Environment)
	}
	rec := apiKeyRecord{APIKey: key, Origin: origin}
	data, err := json.Marshal(rec) //nolint:gosec // G117: intentionally persisting the API key to the 0600 state file.
	if err != nil {
		return fmt.Errorf("encoding API key: %w", err)
	}
	if err := writeFileAtomic(path, data); err != nil {
		return fmt.Errorf("writing API key %s: %w", path, err)
	}
	return nil
}

// readAPIKeyRecord loads the stored API key and its recorded origin for ref.
// Accepts both the JSON envelope and a bare jak_* string on disk.
func readAPIKeyRecord(ref IdentityRef) (*apiKeyRecord, error) {
	path, err := apiKeyPath(ref)
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path) //nolint:gosec // path is StateDir()/<validated-stem>.apikey, not user input.
	if err != nil {
		return nil, err
	}
	trimmed := strings.TrimSpace(string(data))
	if trimmed == "" {
		return &apiKeyRecord{}, nil
	}
	if strings.HasPrefix(trimmed, "{") {
		var rec apiKeyRecord
		if err := json.Unmarshal([]byte(trimmed), &rec); err != nil {
			return nil, fmt.Errorf("decoding API key %s: %w", path, err)
		}
		rec.APIKey = strings.TrimSpace(rec.APIKey)
		rec.Origin = NormalizeOrigin(rec.Origin)
		return &rec, nil
	}
	return &apiKeyRecord{APIKey: trimmed}, nil
}

// ReadAPIKey loads the API-key credential for ref, or an error if none exists
// or if its recorded origin disagrees with the environment's configured origin.
func ReadAPIKey(ref IdentityRef) (string, error) {
	rec, err := readAPIKeyRecord(ref)
	if err != nil {
		return "", err
	}
	if rec.Origin != "" {
		if envOrigin := configuredEnvOrigin(ref.Environment); envOrigin != "" && rec.Origin != envOrigin {
			return "", fmt.Errorf("%w: stored API key for %s/%s belongs to %s, not %s",
				ErrOriginMismatch, ref.Identity, ref.Environment, rec.Origin, envOrigin)
		}
	}
	return rec.APIKey, nil
}

// InvalidateAPIKey removes the stored API-key credential for ref. A missing
// file is not an error.
func InvalidateAPIKey(ref IdentityRef) error {
	path, err := apiKeyPath(ref)
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("removing API key %s: %w", path, err)
	}
	return nil
}

// PurgeEnvironmentCredentials removes all cached OAuth tokens and stored
// static API keys for envName across all identities in the XDG state dir.
func PurgeEnvironmentCredentials(envName string) error {
	if !validName(envName) {
		return fmt.Errorf("invalid environment name %q", envName)
	}
	stateDir, err := config.StateDir()
	if err != nil {
		return err
	}
	entries, err := os.ReadDir(stateDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("reading state dir: %w", err)
	}
	tokSuffix := "_" + envName + "_tokens.json"
	akSuffix := "_" + envName + ".apikey"
	var firstErr error
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasSuffix(name, tokSuffix) && !strings.HasSuffix(name, akSuffix) {
			continue
		}
		fullPath := filepath.Join(stateDir, name)
		if err := os.Remove(fullPath); err != nil && !os.IsNotExist(err) && firstErr == nil {
			firstErr = fmt.Errorf("removing credential %s: %w", fullPath, err)
		}
	}
	return firstErr
}
