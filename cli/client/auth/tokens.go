package auth

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/jentic/jentic-one/cli/client/config"
)

// TokenSet is the cached access token for an identity+environment.
//
// We deliberately do NOT store a refresh token. With the RFC 7523 JWT-bearer
// grant (oauth.go) the CLI can always mint a fresh access token from the
// env-scoped Ed25519 key with no user interaction, so a refresh token would be
// dead weight — and persisting one would be a needless long-lived secret on disk.
// On expiry we simply re-run the assertion exchange. (If the backend ever issues
// a human-interactive grant that genuinely needs refresh, add the field back THEN
// and use it in performOAuthExchange; don't store what you won't use.)
type TokenSet struct {
	AccessToken string    `json:"access_token"`
	ExpiresAt   time.Time `json:"expires_at"`
}

// getTokenPath returns the token-state file path for ref, under the XDG STATE dir
// (never cache — tokens are session state; impl/4.2 §1). The dir is created 0700.
func getTokenPath(ref IdentityRef) (string, error) {
	stateDir, err := config.StateDir()
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		return "", fmt.Errorf("creating state dir: %w", err)
	}
	stem, err := ref.Stem() // validates names — path-traversal guard (impl/4.1 §1)
	if err != nil {
		return "", err
	}
	return filepath.Join(stateDir, stem+"_tokens.json"), nil
}

// ReadTokens loads the cached token for ref. A missing, unreadable, or corrupt
// file yields an error and a nil TokenSet, so callers can treat any failure as
// "no usable token" and re-exchange without risking a nil dereference.
func ReadTokens(ref IdentityRef) (*TokenSet, error) {
	path, err := getTokenPath(ref)
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path) //nolint:gosec // path is StateDir()/<validated-stem>_tokens.json, not user input.
	if err != nil {
		return nil, err
	}
	var tokens TokenSet
	if err := json.Unmarshal(data, &tokens); err != nil {
		return nil, fmt.Errorf("decoding token state %s: %w", path, err)
	}
	return &tokens, nil
}

// InvalidateTokens removes the cached token for ref so the next request forces a
// fresh RFC 7523 exchange. It is used by the response-side 401 policy (impl/4.2 /
// 13 §5): a server-rejected token (revoked, rotated signing key, clock drift the
// backend won't tolerate) looks valid on disk, so we must actively discard it
// before retrying rather than trust our own expiry math. A missing file is not an
// error — there is simply nothing to invalidate.
func InvalidateTokens(ref IdentityRef) error {
	path, err := getTokenPath(ref)
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("invalidating token state %s: %w", path, err)
	}
	return nil
}

// SaveTokens writes the token cache for ref (0600).
//
// Concurrency: no lock. Two processes sharing the same identity+environment that
// exchange concurrently accept last-writer-wins — each exchange yields an
// independently valid token, so a clobbered file only causes a redundant
// exchange, never an invalid state (contrast config.MutateConfig, which locks
// because it mutates stateful status fields).
func SaveTokens(ref IdentityRef, tokens *TokenSet) error {
	path, err := getTokenPath(ref)
	if err != nil {
		return err
	}
	data, err := json.Marshal(tokens) //nolint:gosec // G117: intentionally persisting the access token to the 0600 state file; this IS the token cache.
	if err != nil {
		return fmt.Errorf("encoding token state: %w", err)
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return fmt.Errorf("writing token state %s: %w", path, err)
	}
	return nil
}
