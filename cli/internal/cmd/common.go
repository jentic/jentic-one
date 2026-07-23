package cmd

import (
	"context"
	"errors"
	"fmt"

	"github.com/jentic/jentic-one/cli/internal/agentauth"
	"github.com/jentic/jentic-one/cli/internal/authclient"
	"github.com/jentic/jentic-one/cli/internal/config"
)

// exitCodeError carries a wrapped child's non-zero exit code up to Execute so
// the CLI mirrors it without printing an "error:" line.
type exitCodeError struct{ code int }

func (e *exitCodeError) Error() string { return fmt.Sprintf("child exited with code %d", e.code) }

// ExitCode satisfies core.ExitCoder so core.Run mirrors a wrapped child's exit
// code verbatim.
func (e *exitCodeError) ExitCode() int { return e.code }

// resolveIdentity loads the CLI config once and resolves the profile name and
// control-plane base URL, honouring explicit flag values over config/defaults.
func (a *App) resolveIdentity(profileFlag, baseURLFlag string) (profileName, baseURL string, err error) {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return "", "", err
	}
	return cfg.ResolvedProfileName(profileFlag), cfg.ResolvedBaseURLOr(baseURLFlag), nil
}

// agentSession resolves the active profile, opens its agent session, and
// returns the resolved control-plane base URL plus a valid access token. It
// fails with an actionable error when the profile has no registered agent or
// no usable token. Callers build their own typed HTTP client from baseURL.
func (a *App) agentSession(ctx context.Context, ident *identityOptions) (baseURL, token string, err error) {
	profileName, base, err := a.resolveIdentity(ident.profile, ident.baseURL)
	if err != nil {
		return "", "", err
	}
	sess, err := agentauth.Open(a.Paths, profileName, base)
	if err != nil {
		return "", "", err
	}
	if !sess.Meta.IsAPIKey() && sess.Meta.AgentID == "" {
		return "", "", fmt.Errorf("profile %q has no registered agent; run `jentic register` first", profileName)
	}
	tok, err := sess.ValidToken(ctx)
	if err != nil {
		return "", "", agentAuthErr(err, profileName)
	}
	return sess.Meta.BaseURL, tok, nil
}

// agentSessionOpen resolves the active profile and opens its agent session,
// returning the session itself (for callers that need to act on it directly,
// e.g. forcing a re-mint). It does not obtain a token. Fails with an actionable
// error when the profile has no registered agent.
func (a *App) agentSessionOpen(ident *identityOptions) (*agentauth.Session, string, error) {
	profileName, base, err := a.resolveIdentity(ident.profile, ident.baseURL)
	if err != nil {
		return nil, "", err
	}
	sess, err := agentauth.Open(a.Paths, profileName, base)
	if err != nil {
		return nil, "", err
	}
	if !sess.Meta.IsAPIKey() && sess.Meta.AgentID == "" {
		return nil, "", fmt.Errorf("profile %q has no registered agent; run `jentic register` first", profileName)
	}
	return sess, profileName, nil
}

// agentAuthErr turns a token-mint failure into an actionable message. The agent
// id is present (checked by the caller) but no usable token could be obtained:
// the agent is awaiting approval, was revoked, or the signing key no longer
// matches what the server registered — all of which `jentic register` resolves.
func agentAuthErr(err error, profileName string) error {
	if errors.Is(err, agentauth.ErrNotRegistered) {
		return fmt.Errorf("profile %q has no registered agent; run `jentic register` first", profileName)
	}
	var pending *authclient.PendingError
	if errors.As(err, &pending) {
		return fmt.Errorf("agent for profile %q is not active yet (%v); wait for approval, "+
			"or re-run `jentic register --profile %s` if you removed it", profileName, err, profileName)
	}
	return fmt.Errorf("could not authenticate profile %q (%w); re-run `jentic register --profile %s`",
		profileName, err, profileName)
}
