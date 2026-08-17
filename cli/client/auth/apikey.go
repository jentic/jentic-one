package auth

import (
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

// SaveAPIKey persists a jak_* API key credential for ref (0600). It is the V2
// successor to V1's per-profile `apikey` file: the credential is a first-class
// identity credential (Phase 4 item 4) but stored as a secret under XDG state,
// never in config.yaml (which round-trips through node merges and is not
// secret-safe). Returns an error if the key lacks the required prefix.
func SaveAPIKey(ref IdentityRef, key string) error {
	key = strings.TrimSpace(key)
	if !strings.HasPrefix(key, APIKeyPrefix) {
		return fmt.Errorf("API key must start with %q", APIKeyPrefix)
	}
	path, err := apiKeyPath(ref)
	if err != nil {
		return err
	}
	if err := writeFileAtomic(path, []byte(key)); err != nil {
		return fmt.Errorf("writing API key %s: %w", path, err)
	}
	return nil
}

// ReadAPIKey loads the API-key credential for ref, or an error if none exists.
func ReadAPIKey(ref IdentityRef) (string, error) {
	path, err := apiKeyPath(ref)
	if err != nil {
		return "", err
	}
	data, err := os.ReadFile(path) //nolint:gosec // path is StateDir()/<validated-stem>.apikey, not user input.
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}
