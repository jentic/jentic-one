package auth

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/client/config"
)

// TokenSet is the cached OAuth token state for an identity+environment (#1384).
//
// Both token fields carry `redact:"true"` so the CLI's typed redaction layer
// scrubs them fail-closed if a TokenSet ever reaches an Audience render or file
// marshal path, while SaveTokens persists the raw values to the 0600 state file.
type TokenSet struct {
	AccessToken  string    `json:"access_token" redact:"true"`
	RefreshToken string    `json:"refresh_token,omitempty" redact:"true"`
	ExpiresAt    time.Time `json:"expires_at"`
	Origin       string    `json:"origin,omitempty"`
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

// readTokensRaw loads the cached token file for ref without checking the
// configured environment origin.
func readTokensRaw(ref IdentityRef) (*TokenSet, error) {
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
	tokens.AccessToken = strings.TrimSpace(tokens.AccessToken)
	tokens.RefreshToken = strings.TrimSpace(tokens.RefreshToken)
	rawOrigin := strings.TrimSpace(tokens.Origin)
	tokens.Origin = NormalizeOrigin(rawOrigin)
	if rawOrigin != "" && tokens.Origin == "" {
		return nil, fmt.Errorf("invalid token origin in %s", path)
	}
	return &tokens, nil
}

// ReadTokens loads the cached token for ref. A missing, unreadable, corrupt,
// or cross-origin file yields an error and a nil TokenSet, so callers can
// treat any failure as "no usable token" and re-exchange without risking a nil
// dereference.
func ReadTokens(ref IdentityRef) (*TokenSet, error) {
	tokens, err := readTokensRaw(ref)
	if err != nil {
		return nil, err
	}
	envOrigin, err := configuredEnvOrigin(ref.Environment)
	if err != nil {
		return nil, err
	}
	if envOrigin != "" && tokens.Origin == "" {
		return nil, fmt.Errorf("%w: cached token for %s/%s has no origin binding",
			ErrOriginMismatch, ref.Identity, ref.Environment)
	}
	if envOrigin != "" && tokens.Origin != envOrigin {
		return nil, fmt.Errorf("%w: cached token for %s/%s belongs to %s, not %s",
			ErrOriginMismatch, ref.Identity, ref.Environment, tokens.Origin, envOrigin)
	}
	return tokens, nil
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
	if tokens == nil {
		return errors.New("tokens must not be nil")
	}
	path, err := getTokenPath(ref)
	if err != nil {
		return err
	}
	toSave := *tokens
	toSave.AccessToken = strings.TrimSpace(toSave.AccessToken)
	toSave.RefreshToken = strings.TrimSpace(toSave.RefreshToken)
	rawOrigin := strings.TrimSpace(toSave.Origin)
	toSave.Origin = NormalizeOrigin(rawOrigin)
	if rawOrigin != "" && toSave.Origin == "" {
		return errors.New("invalid token origin")
	}
	if toSave.Origin == "" {
		envOrigin, err := configuredEnvOrigin(ref.Environment)
		if err != nil {
			return err
		}
		toSave.Origin = envOrigin
	}
	tokens.AccessToken = toSave.AccessToken
	tokens.RefreshToken = toSave.RefreshToken
	tokens.Origin = toSave.Origin
	data, err := json.Marshal(&toSave) //nolint:gosec // G117: intentionally persisting the access token to the 0600 state file; this IS the token cache.
	if err != nil {
		return fmt.Errorf("encoding token state: %w", err)
	}
	if err := writeFileAtomic(path, data); err != nil {
		return fmt.Errorf("writing token state %s: %w", path, err)
	}
	return nil
}
