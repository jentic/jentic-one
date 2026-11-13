package auth

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/gofrs/flock"
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

// apiKeyOriginPath returns the sidecar that binds a bare legacy-compatible API
// key file to its issuing origin.
func apiKeyOriginPath(ref IdentityRef) (string, error) {
	path, err := apiKeyPath(ref)
	if err != nil {
		return "", err
	}
	return path + ".origin", nil
}

// apiKeyPendingPath returns the transaction record for a two-file API-key update.
func apiKeyPendingPath(path string) string {
	return path + ".pending"
}

// lockAPIKey serializes readers and writers on a separate lock file so atomic
// renames never replace the file on which the lock is held.
func lockAPIKey(path string) (*flock.Flock, error) {
	lock := flock.New(path + ".lock")
	if err := lock.Lock(); err != nil {
		return nil, fmt.Errorf("locking API key %s: %w", path, err)
	}
	return lock, nil
}

// commitAPIKeyRecord replaces the bare key before its origin binding. If the
// process stops between those writes, the old origin rejects the new key rather
// than allowing the old key to inherit the new origin.
func commitAPIKeyRecord(path, key, origin string) error {
	if err := writeFileAtomic(path, []byte(key)); err != nil {
		return fmt.Errorf("writing API key %s: %w", path, err)
	}
	originPath := path + ".origin"
	if origin != "" {
		if err := writeFileAtomic(originPath, []byte(origin)); err != nil {
			return fmt.Errorf("writing API key origin %s: %w", originPath, err)
		}
		return nil
	}
	if err := os.Remove(originPath); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("removing stale API key origin %s: %w", originPath, err)
	}
	return nil
}

// recoverPendingAPIKey rolls a complete pending record forward. The record is
// durable before either public file changes, and remains until both commits finish.
func recoverPendingAPIKey(path string) error {
	pendingPath := apiKeyPendingPath(path)
	data, err := os.ReadFile(pendingPath) //nolint:gosec // path is derived from the validated API-key path.
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("reading pending API key update %s: %w", pendingPath, err)
	}
	var rec apiKeyRecord
	if err := json.Unmarshal(data, &rec); err != nil {
		return fmt.Errorf("decoding pending API key update %s: %w", pendingPath, err)
	}
	rec.APIKey = strings.TrimSpace(rec.APIKey)
	if rec.APIKey == "" {
		return fmt.Errorf("pending API key update %s has an empty credential", pendingPath)
	}
	rawOrigin := strings.TrimSpace(rec.Origin)
	rec.Origin = NormalizeOrigin(rawOrigin)
	if rawOrigin != "" && rec.Origin == "" {
		return fmt.Errorf("invalid API key origin in pending update %s", pendingPath)
	}
	if err := commitAPIKeyRecord(path, rec.APIKey, rec.Origin); err != nil {
		return fmt.Errorf("recovering pending API key update: %w", err)
	}
	if err := os.Remove(pendingPath); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("clearing pending API key update %s: %w", pendingPath, err)
	}
	return nil
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
// rawOrigin is empty). The credential file stays bare for mixed-version CLI
// compatibility; the origin lives in a separate non-secret sidecar.
func SaveAPIKeyForOrigin(ref IdentityRef, key, rawOrigin string) error {
	key = strings.TrimSpace(key)
	if !strings.HasPrefix(key, APIKeyPrefix) {
		return fmt.Errorf("API key must start with %q", APIKeyPrefix)
	}
	var origin string
	if rawOrigin != "" {
		origin = NormalizeOrigin(rawOrigin)
		if origin == "" {
			return errors.New("invalid API key origin")
		}
	} else {
		var err error
		origin, err = configuredEnvOrigin(ref.Environment)
		if err != nil {
			return err
		}
	}

	path, err := apiKeyPath(ref)
	if err != nil {
		return err
	}
	lock, err := lockAPIKey(path)
	if err != nil {
		return err
	}
	defer func() { _ = lock.Unlock() }()

	pending, err := json.Marshal(apiKeyRecord{APIKey: key, Origin: origin})
	if err != nil {
		return fmt.Errorf("encoding pending API key update: %w", err)
	}
	pendingPath := apiKeyPendingPath(path)
	if err := writeFileAtomic(pendingPath, pending); err != nil {
		return fmt.Errorf("writing pending API key update %s: %w", pendingPath, err)
	}
	if err := commitAPIKeyRecord(path, key, origin); err != nil {
		return err
	}
	if err := os.Remove(pendingPath); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("clearing pending API key update %s: %w", pendingPath, err)
	}
	return nil
}

// readAPIKeyRecord loads the stored API key and its recorded origin for ref.
// Accepts bare keys with an origin sidecar and JSON-envelope credential files.
func readAPIKeyRecord(ref IdentityRef) (*apiKeyRecord, error) {
	path, err := apiKeyPath(ref)
	if err != nil {
		return nil, err
	}
	lock, err := lockAPIKey(path)
	if err != nil {
		return nil, err
	}
	defer func() { _ = lock.Unlock() }()
	if err := recoverPendingAPIKey(path); err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path) //nolint:gosec // path is StateDir()/<validated-stem>.apikey, not user input.
	if err != nil {
		return nil, err
	}
	trimmed := strings.TrimSpace(string(data))
	if strings.HasPrefix(trimmed, "{") {
		var rec apiKeyRecord
		if err := json.Unmarshal([]byte(trimmed), &rec); err != nil {
			return nil, fmt.Errorf("decoding API key %s: %w", path, err)
		}
		rec.APIKey = strings.TrimSpace(rec.APIKey)
		if rec.APIKey == "" {
			return nil, fmt.Errorf("stored API key in %s is empty", path)
		}
		rawOrigin := strings.TrimSpace(rec.Origin)
		rec.Origin = NormalizeOrigin(rawOrigin)
		if rawOrigin != "" && rec.Origin == "" {
			return nil, fmt.Errorf("invalid API key origin in %s", path)
		}
		return &rec, nil
	}

	rec := &apiKeyRecord{APIKey: trimmed}
	if rec.APIKey == "" {
		return nil, fmt.Errorf("stored API key in %s is empty", path)
	}
	originPath, err := apiKeyOriginPath(ref)
	if err != nil {
		return nil, err
	}
	originData, err := os.ReadFile(originPath) //nolint:gosec // path is StateDir()/<validated-stem>.apikey.origin, not user input.
	if err != nil {
		if os.IsNotExist(err) {
			return rec, nil
		}
		return nil, fmt.Errorf("reading API key origin %s: %w", originPath, err)
	}
	rawOrigin := strings.TrimSpace(string(originData))
	rec.Origin = NormalizeOrigin(rawOrigin)
	if rawOrigin != "" && rec.Origin == "" {
		return nil, fmt.Errorf("invalid API key origin in %s", originPath)
	}
	return rec, nil
}

// ReadAPIKey loads the API-key credential for ref, or an error if none exists
// or if its recorded origin disagrees with the environment's configured origin.
func ReadAPIKey(ref IdentityRef) (string, error) {
	rec, err := readAPIKeyRecord(ref)
	if err != nil {
		return "", err
	}
	if rec.Origin != "" {
		envOrigin, err := configuredEnvOrigin(ref.Environment)
		if err != nil {
			return "", err
		}
		if envOrigin != "" && rec.Origin != envOrigin {
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
	lock, err := lockAPIKey(path)
	if err != nil {
		return err
	}
	defer func() { _ = lock.Unlock() }()
	pendingPath := apiKeyPendingPath(path)
	if err := os.Remove(pendingPath); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("removing pending API key update %s: %w", pendingPath, err)
	}
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("removing API key %s: %w", path, err)
	}
	originPath, err := apiKeyOriginPath(ref)
	if err != nil {
		return err
	}
	if err := os.Remove(originPath); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("removing API key origin %s: %w", originPath, err)
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
	akOriginSuffix := akSuffix + ".origin"
	akPendingSuffix := akSuffix + ".pending"
	var firstErr error
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasSuffix(name, tokSuffix) && !strings.HasSuffix(name, akSuffix) &&
			!strings.HasSuffix(name, akOriginSuffix) && !strings.HasSuffix(name, akPendingSuffix) {
			continue
		}
		fullPath := filepath.Join(stateDir, name)
		if strings.HasSuffix(name, akOriginSuffix) {
			keyPath := strings.TrimSuffix(fullPath, ".origin")
			if _, err := os.Stat(keyPath); err == nil || !os.IsNotExist(err) {
				// A surviving key keeps its origin binding when its removal failed.
				continue
			}
		}
		if err := os.Remove(fullPath); err != nil && !os.IsNotExist(err) && firstErr == nil {
			firstErr = fmt.Errorf("removing credential %s: %w", fullPath, err)
		}
	}
	return firstErr
}
