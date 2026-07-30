// Package agentauth ties together a profile, its Ed25519 key, and the auth
// client to register agents and resolve valid access tokens (mint/refresh).
package agentauth

import (
	"context"
	"errors"
	"time"

	"github.com/jentic/jentic-one/cli/internal/agentkey"
	"github.com/jentic/jentic-one/cli/internal/authclient"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/profile"
)

// expirySkew re-mints a token slightly before it actually expires.
const expirySkew = 60 * time.Second

// assertionTTL is the lifetime of a signed JWT-Bearer assertion.
const assertionTTL = 2 * time.Minute

// Session bundles a profile + key + client for a single base URL.
type Session struct {
	Profile *profile.Profile
	Meta    *profile.Meta
	Key     *agentkey.Key
	Client  *authclient.Client
	// APIKey holds the stored API key when Meta.AuthMode is AuthModeAPIKey.
	APIKey string
}

// ErrNotRegistered is returned when the profile has no agent id yet.
var ErrNotRegistered = errors.New("profile has no registered agent; run `jentic register` first")

// ErrNoAPIKey is returned when an API-key profile has no key stored.
var ErrNoAPIKey = errors.New("profile has no API key; run `jentic profile add-key` first")

// Open loads the profile metadata and key for a base URL, generating the key if
// missing. It does not perform registration. For API-key profiles it loads the
// stored key instead of generating an Ed25519 keypair.
func Open(paths config.Paths, profileName, baseURL string) (*Session, error) {
	p, err := profile.Open(paths, profileName)
	if err != nil {
		return nil, err
	}
	meta, err := p.LoadMeta()
	if err != nil {
		return nil, err
	}
	if meta.BaseURL == "" {
		meta.BaseURL = baseURL
	}

	sess := &Session{
		Profile: p,
		Meta:    meta,
		Client:  authclient.New(meta.BaseURL),
	}

	if meta.IsAPIKey() {
		apiKey, keyErr := p.LoadAPIKey()
		if keyErr != nil {
			return nil, keyErr
		}
		sess.APIKey = apiKey
		return sess, nil
	}

	if meta.KID == "" {
		meta.KID = "jentic-cli-" + p.Name
	}
	key, _, err := agentkey.LoadOrGenerate(p.KeyPath(), meta.KID)
	if err != nil {
		return nil, err
	}
	sess.Key = key
	return sess, nil
}

// ResetRegistration clears all DCR registration state from the session's
// profile metadata so a subsequent register call provisions a brand-new agent.
// It clears the agent id, the human-friendly name, and the RFC 7592 management
// token together — clearing only the id would leave stale name/token fields.
func (s *Session) ResetRegistration() {
	s.Meta.AgentID = ""
	s.Meta.AgentName = ""
	s.Meta.RegistrationAccessToken = ""
}

// ErrIdentityMismatch is returned when a freshly minted token resolves (via /me)
// to an agent other than the one this profile is registered as. It means the token
// pair was NOT persisted — acting on it would let the CLI operate as the wrong
// identity, so we refuse rather than silently save a mis-attributed credential.
var ErrIdentityMismatch = errors.New("minted token resolves to a different agent identity; not saving it")

// MintFresh signs an assertion and exchanges it for a new token pair, saving it
// to the profile. Returns *authclient.PendingError while the agent is not active.
//
// Before persisting, the freshly minted access token is checked against /me: if
// the server reports an identity that CONTRADICTS this profile's registered agent
// id, the token is discarded and ErrIdentityMismatch is returned — a confused
// server or a key/profile mix-up must not leave the CLI holding a token minted for
// someone else. The check is deliberately best-effort in the ambiguous cases (the
// /me call fails, or the response carries no identity field we recognise): minting
// must not hard-depend on a second endpoint, so we only fail on a definite mismatch.
func (s *Session) MintFresh(ctx context.Context) (*profile.Tokens, error) {
	if s.Meta.AgentID == "" {
		return nil, ErrNotRegistered
	}
	assertion, err := s.Key.SignAssertion(s.Meta.AgentID, s.Client.Audience(), assertionTTL)
	if err != nil {
		return nil, err
	}
	pair, err := s.Client.MintAgentToken(ctx, assertion)
	if err != nil {
		return nil, err
	}
	if !s.identityMatches(ctx, pair.AccessToken) {
		return nil, ErrIdentityMismatch
	}
	return s.persist(pair)
}

// identityMatches reports whether the access token resolves (via /me) to this
// profile's registered agent id, or whether the answer is indeterminate. It
// returns false ONLY on a definite contradiction — a recognised identity field is
// present, non-empty, and none of them equals AgentID. A failed /me call or a
// response with no recognised identity field returns true (can't assert → don't
// block minting; see MintFresh).
func (s *Session) identityMatches(ctx context.Context, accessToken string) bool {
	me, err := s.Client.Me(ctx, accessToken)
	if err != nil {
		return true // can't reach /me — best-effort, don't block the mint
	}
	var sawIdentity bool
	for _, k := range []string{"sub", "client_id", "id", "agent_id"} {
		if v, ok := me[k].(string); ok && v != "" {
			sawIdentity = true
			if v == s.Meta.AgentID {
				return true
			}
		}
	}
	// A recognised identity field was present but none matched → real mismatch.
	// No recognised field at all → indeterminate, allow.
	return !sawIdentity
}

// ValidToken returns a non-expired access token, refreshing or re-minting as
// needed, and persists any new pair. For API-key profiles it returns the stored
// API key directly (it is the bearer credential — no minting or caching).
func (s *Session) ValidToken(ctx context.Context) (string, error) {
	if s.Meta.IsAPIKey() {
		if s.APIKey == "" {
			return "", ErrNoAPIKey
		}
		return s.APIKey, nil
	}
	if s.Meta.AgentID == "" {
		return "", ErrNotRegistered
	}
	// LoadTokens returns (nil, nil) when no token file exists yet; Expired
	// treats a nil receiver as expired, so an absent cache short-circuits
	// straight to the mint path below. The explicit nil guard before the refresh
	// branch is the safety net that keeps that case from dereferencing nil.
	tokens, err := s.Profile.LoadTokens()
	if err != nil {
		return "", err
	}
	if !tokens.Expired(expirySkew) {
		return tokens.AccessToken, nil
	}

	// Try refresh first when we have a refresh token.
	if tokens != nil && tokens.RefreshToken != "" {
		if pair, refErr := s.Client.Refresh(ctx, tokens.RefreshToken); refErr == nil {
			saved, saveErr := s.persist(pair)
			if saveErr != nil {
				return "", saveErr
			}
			return saved.AccessToken, nil
		}
	}

	// Fall back to minting a fresh pair from a new assertion.
	saved, mintErr := s.MintFresh(ctx)
	if mintErr != nil {
		return "", mintErr
	}
	return saved.AccessToken, nil
}

func (s *Session) persist(pair *authclient.TokenPair) (*profile.Tokens, error) {
	expiresAt := time.Time{}
	if pair.ExpiresIn > 0 {
		expiresAt = time.Now().Add(time.Duration(pair.ExpiresIn) * time.Second)
	}
	tokens := &profile.Tokens{
		AccessToken:     pair.AccessToken,
		RefreshToken:    pair.RefreshToken,
		AccessExpiresAt: expiresAt,
	}
	if err := s.Profile.SaveTokens(tokens); err != nil {
		return nil, err
	}
	return tokens, nil
}
