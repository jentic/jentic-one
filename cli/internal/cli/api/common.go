package api

import (
	"context"
	"errors"
	"fmt"

	"github.com/jentic/jentic-one/cli/internal/agentauth"
	"github.com/jentic/jentic-one/cli/internal/authclient"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/config"
)

// sessionPaths resolves WHICH profile store the active profile lives in — the
// operator's own ~/.jentic or the shared agent account's home. This is the
// in-process run-as: when the checked-out/active profile is agent-owned, the
// operator (who already has recursive ACL read on the agent home) opens the
// session against the agent store — reading the agent's key/tokens and calling the
// control-plane in-process as itself — with no re-exec or confinement, since these
// are plain authenticated HTTP calls that write nothing to disk. An operator-owned
// (or not-yet-created) profile resolves to the operator's own store as before.
func (a *app) sessionPaths(profileName string) (config.Paths, error) {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return config.Paths{}, err
	}
	ref, found, err := a.findProfileRef(cfg, profileName)
	if err != nil {
		return config.Paths{}, err
	}
	if found {
		return ref.paths, nil
	}
	return a.Paths, nil
}

// notRegisteredErr is the typed form of the single most common agent error
// (AGT-3): the profile has no registered agent. NOT_AUTHENTICATED so the agent
// envelope carries a closed-enum code instead of raw prose; the message keeps
// the exact V1 wording.
func notRegisteredErr(profileName string) *ux.CodedError {
	return &ux.CodedError{
		Code:       ux.CodeNotAuthenticated,
		Msg:        fmt.Sprintf("profile %q has no registered agent; run `jentic register` first", profileName),
		Actionable: "jentic register",
	}
}

// agentSession resolves the active profile, opens its agent session, and
// returns the resolved control-plane base URL plus a valid access token. It
// fails with an actionable error when the profile has no registered agent or
// no usable token. Callers build their own typed HTTP client from baseURL.
func (a *app) agentSession(ctx context.Context, ident *identityOptions) (baseURL, token string, err error) {
	profileName, base, err := a.ResolveIdentity(ident.Profile, ident.BaseURL)
	if err != nil {
		return "", "", err
	}
	paths, err := a.sessionPaths(profileName)
	if err != nil {
		return "", "", err
	}
	sess, err := agentauth.Open(paths, profileName, base)
	if err != nil {
		return "", "", err
	}
	if !sess.Meta.IsAPIKey() && sess.Meta.AgentID == "" {
		return "", "", notRegisteredErr(profileName)
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
func (a *app) agentSessionOpen(ident *identityOptions) (*agentauth.Session, string, error) {
	profileName, base, err := a.ResolveIdentity(ident.Profile, ident.BaseURL)
	if err != nil {
		return nil, "", err
	}
	paths, err := a.sessionPaths(profileName)
	if err != nil {
		return nil, "", err
	}
	sess, err := agentauth.Open(paths, profileName, base)
	if err != nil {
		return nil, "", err
	}
	if !sess.Meta.IsAPIKey() && sess.Meta.AgentID == "" {
		return nil, "", notRegisteredErr(profileName)
	}
	return sess, profileName, nil
}

// agentSessionView is the strictly READ-ONLY sibling of agentSession, for
// `jentic doctor` (UX-1): it resolves the same profile/store but opens the
// session via agentauth.OpenView — never creating the profile directory,
// generating a key, or minting/persisting tokens. Callers inspect the returned
// session's state (Key/AgentID/CachedToken) instead of receiving a hard error,
// because doctor reports partial setups rather than failing on them.
func (a *app) agentSessionView(ident *identityOptions) (*agentauth.Session, string, error) {
	profileName, base, err := a.ResolveIdentity(ident.Profile, ident.BaseURL)
	if err != nil {
		return nil, "", err
	}
	paths, err := a.sessionPaths(profileName)
	if err != nil {
		return nil, "", err
	}
	sess, err := agentauth.OpenView(paths, profileName, base)
	if err != nil {
		return nil, "", err
	}
	return sess, profileName, nil
}

// agentAuthErr turns a token-mint failure into an actionable, CODED message
// (AGT-3/AGT-6). The agent id is present (checked by the caller) but no usable
// token could be obtained: not registered → NOT_AUTHENTICATED; awaiting
// approval → PENDING_APPROVAL; anything else (revoked, key mismatch) →
// NOT_AUTHENTICATED — all of which `jentic register` resolves.
func agentAuthErr(err error, profileName string) error {
	if errors.Is(err, agentauth.ErrNotRegistered) {
		return notRegisteredErr(profileName)
	}
	var pending *authclient.PendingError
	if errors.As(err, &pending) {
		return &ux.CodedError{
			Code: ux.CodePendingApproval,
			Msg: fmt.Sprintf("agent for profile %q is not active yet (%v); wait for approval, "+
				"or re-run `jentic register --profile %s` if you removed it", profileName, err, profileName),
			Actionable: "jentic register --profile " + profileName,
		}
	}
	return &ux.CodedError{
		Code: ux.CodeNotAuthenticated,
		Msg: fmt.Sprintf("could not authenticate profile %q (%v); re-run `jentic register --profile %s`",
			profileName, err, profileName),
		Actionable: "jentic register --profile " + profileName,
	}
}
