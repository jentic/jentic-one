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
		// 0. TRANSPORT GUARD (ref F3). Before attaching ANY bearer — injected or
		// minted — refuse a non-HTTPS, non-loopback target. req.URL is the actual
		// outbound destination derived from a possibly attacker-influenced BaseURL;
		// without this, a poisoned base URL turns the middleware into a
		// token-exfiltration primitive. Same invariant tokenEndpoint enforces (F1).
		if err := requireSecureHost(req.URL); err != nil {
			return fmt.Errorf("refusing to attach credentials: %w", err)
		}

		// 1. FILE-LESS OVERRIDE. If the orchestrator injected a token, use it.
		if creds.InjectedBearerToken != "" {
			req.Header.Set("Authorization", "Bearer "+creds.InjectedBearerToken)
			return nil
		}

		// 2. DISK-BASED TOKEN STATE. Treat missing/corrupt/expired uniformly as
		// "needs exchange"; we never dereference tokens unless it is non-nil and
		// unexpired, so a nil TokenSet (returned on any ReadTokens failure) can
		// never panic here.
		tokens, err := ReadTokens(creds.IdentityRef())
		needsExchange := err != nil || tokens == nil || time.Now().After(tokens.ExpiresAt)
		if needsExchange {
			newTokens, xerr := performOAuthExchange(creds)
			if xerr != nil {
				return fmt.Errorf("failed to authenticate: %w", xerr)
			}
			if serr := SaveTokens(creds.IdentityRef(), newTokens); serr != nil {
				return serr
			}
			tokens = newTokens
		}

		req.Header.Set("Authorization", "Bearer "+tokens.AccessToken)
		return nil
	}
}
