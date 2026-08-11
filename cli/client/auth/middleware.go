package auth

import (
	"context"
	"fmt"
	"net/http"
	"time"
)

// Credentials is the minimal, UX-free input the auth middleware needs. The
// top-level client.Config is mapped into this by the SDK constructors, keeping
// client/auth free of Cobra/UX concepts.
type Credentials struct {
	BaseURL             string
	IdentityName        string
	EnvironmentName     string
	InjectedBearerToken string // file-less / bring-your-own-token override
}

// IdentityRef extracts the (identity, environment) pair keys/tokens are stored
// under, keeping the storage layer ignorant of the wider Credentials shape.
func (c Credentials) IdentityRef() IdentityRef {
	return IdentityRef{Identity: c.IdentityName, Environment: c.EnvironmentName}
}

// RequestEditor returns a function that mutates outbound http.Requests to attach
// the bearer token, fetching/refreshing it as needed.
func RequestEditor(creds Credentials) func(ctx context.Context, req *http.Request) error {
	return func(_ context.Context, req *http.Request) error {
		return AttachAuth(creds, req)
	}
}

// AttachAuth stamps the appropriate Authorization header on req for creds. It is
// the shared attach path used by both the request-editor middleware and the
// response-side 401 retry (transport.go), so the two can never disagree on which
// credential to present. Order: transport guard -> injected token -> API-key
// credential -> disk token (exchanged if missing/expired).
func AttachAuth(creds Credentials, req *http.Request) error {
	// 0. TRANSPORT GUARD (ref F3). Before attaching ANY bearer — injected or
	// minted — refuse a non-HTTPS, non-loopback target. req.URL is the actual
	// outbound destination derived from a possibly attacker-influenced BaseURL;
	// without this, a poisoned base URL turns the middleware into a
	// token-exfiltration primitive. Same invariant tokenEndpoint enforces (F1).
	if err := requireSecureHost(req.URL); err != nil {
		return fmt.Errorf("refusing to attach credentials: %w", err)
	}
	token, err := BearerToken(creds)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	return nil
}

// BearerToken resolves the Authorization bearer value for creds: the injected
// token, the stored jak_* API key, or the cached disk token — exchanged (and
// persisted) when missing/expired. It is the credential-resolution half of
// AttachAuth, exported so callers that assemble their own requests (rather
// than going through the SDK's request editor) present exactly the same
// credential the SDK would. Callers own the transport-security guard AttachAuth
// applies (the SDK never sends a bearer to a non-HTTPS, non-loopback host —
// F3); the exchange itself is protected regardless by tokenEndpoint (F1).
func BearerToken(creds Credentials) (string, error) {
	// 1. FILE-LESS OVERRIDE. If the orchestrator injected a token, use it.
	if creds.InjectedBearerToken != "" {
		return creds.InjectedBearerToken, nil
	}

	// 2. API-KEY CREDENTIAL (Phase 4 item 4). A jak_* agent API key is a
	// first-class, long-lived credential that operators and CI use. Unlike the
	// Ed25519/JWT-bearer flow it is NOT exchanged or minted — the backend
	// dispatches on the key prefix, so the raw key IS the bearer value
	// (parity with V1: internal/agentauth Session.ValidToken returns the stored
	// key verbatim, sent by httpx as `Authorization: Bearer <key>`). Presence of
	// the on-disk credential is the signal; we only treat a read error as
	// "no API key, fall through to OAuth" — never as a hard failure, so an
	// identity without a stored key still reaches the exchange path below.
	if key, err := ReadAPIKey(creds.IdentityRef()); err == nil && key != "" {
		return key, nil
	}

	// 3. DISK-BASED TOKEN STATE. Treat missing/corrupt/expired uniformly as
	// "needs exchange"; we never dereference tokens unless it is non-nil and
	// unexpired, so a nil TokenSet (returned on any ReadTokens failure) can
	// never panic here.
	tokens, err := ReadTokens(creds.IdentityRef())
	needsExchange := err != nil || tokens == nil || time.Now().After(tokens.ExpiresAt)
	if needsExchange {
		newTokens, xerr := performOAuthExchange(creds)
		if xerr != nil {
			return "", fmt.Errorf("failed to authenticate: %w", xerr)
		}
		if serr := SaveTokens(creds.IdentityRef(), newTokens); serr != nil {
			return "", serr
		}
		tokens = newTokens
	}
	return tokens.AccessToken, nil
}

// RefreshBearerToken drops any cached token and forces a fresh assertion
// exchange, returning the new bearer value. This is how a caller picks up
// server-side grant changes that are baked into the token at mint time (scope
// grants — `jentic access refresh`): a refresh-token rotation would carry the
// old scopes forward unchanged, a fresh exchange re-reads them. Static
// credentials (injected token, jak_* API key) have nothing to re-mint; they are
// returned as-is, matching BearerToken's resolution order.
func RefreshBearerToken(creds Credentials) (string, error) {
	if creds.InjectedBearerToken != "" {
		return creds.InjectedBearerToken, nil
	}
	if key, err := ReadAPIKey(creds.IdentityRef()); err == nil && key != "" {
		return key, nil
	}
	if err := InvalidateTokens(creds.IdentityRef()); err != nil {
		return "", err
	}
	return BearerToken(creds)
}

// CanReExchange reports whether creds can mint a NEW token on a 401 (i.e. the
// exchange-backed disk path). Injected tokens and API keys are fixed credentials:
// a 401 on those is a hard denial, not something a re-exchange can fix, so the
// transport must not loop on them.
func CanReExchange(creds Credentials) bool {
	if creds.InjectedBearerToken != "" {
		return false
	}
	if key, err := ReadAPIKey(creds.IdentityRef()); err == nil && key != "" {
		return false
	}
	return true
}
