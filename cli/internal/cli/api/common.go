package api

import (
	"context"
	"errors"
	"fmt"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/internal/agentauth"
	"github.com/jentic/jentic-one/cli/internal/authclient"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
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

// agentSession resolves the caller's identity and returns the control-plane
// base URL plus a valid access token. Resolution is CONTEXT-FIRST (the closing
// piece of the Phase 4 identity-unification work): when a V2 context is active,
// the whole data-plane command family (catalog/search/inspect/access/execute/
// apis) authenticates from the XDG store — the same env URL and env-scoped key
// that `jentic identity register` wrote — so `env add` → `context create --use`
// → `identity register` → data commands works end-to-end. Only when no V2
// config exists (the legacy adapter resolved state), or the caller explicitly
// pinned the V1 store via --profile/--base-url, does it fall back to the legacy
// ~/.jentic profile session. Callers build their own typed HTTP client from
// baseURL.
func (a *app) agentSession(ctx context.Context, ident *identityOptions) (baseURL, token string, err error) {
	if st := a.activeState(ctx, ident); st != nil {
		return a.contextSession(st)
	}
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

// activeState returns the resolved V2 state when THIS invocation must
// authenticate from the XDG context store, or nil when the legacy ~/.jentic
// path applies. nil in exactly three cases:
//   - the caller passed --profile/--base-url, the explicit V1 escape hatch
//     (those flags name entities of the legacy store; honoring them against the
//     XDG store would silently address the wrong identity);
//   - no state was injected (root interceptor did not run — unit-test app
//     wiring), so there is nothing context-shaped to use;
//   - the state came from the legacy-read adapter (EnvironmentName ==
//     clictx.LegacyEnvironment), i.e. the user has no V2 config at all.
//
// A V2 context with a half-configured environment (no base_url) is still
// returned: falling back to the legacy store there would resurrect the exact
// split-brain this bridge removes (context says QA, command talks to
// localhost). contextSession turns it into a coded, actionable error instead.
func (a *app) activeState(ctx context.Context, ident *identityOptions) *clictx.ActiveState {
	if ident != nil && (ident.Profile != "" || ident.BaseURL != "") {
		return nil
	}
	st := clictx.FromContext(ctx)
	if st == nil || st.ResolvedState == nil {
		return nil
	}
	if st.EnvironmentName == "" || st.EnvironmentName == clictx.LegacyEnvironment {
		return nil
	}
	return st
}

// contextSession obtains (baseURL, bearer) for an active V2 context via the
// SDK's credential-resolution order (injected token > jak_* API key > cached/
// exchanged token) — byte-for-byte the credential the SDK request editor would
// attach, so hand-rolled clients and generated clients can never disagree.
func (a *app) contextSession(st *clictx.ActiveState) (baseURL, token string, err error) {
	if st.BaseURL == "" {
		return "", "", &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("environment %q has no base_url", st.EnvironmentName),
			Actionable: "Set it with `jentic env add` / edit the environment.",
		}
	}
	tok, err := auth.BearerToken(credsFromState(st))
	if err != nil {
		return "", "", asCoded(err)
	}
	return st.BaseURL, tok, nil
}

// credsFromState maps the resolved context onto the SDK's UX-free credential
// input — the same mapping the SDK constructors apply to client.Config.
func credsFromState(st *clictx.ActiveState) auth.Credentials {
	return auth.Credentials{
		BaseURL:             st.BaseURL,
		IdentityName:        st.IdentityName,
		EnvironmentName:     st.EnvironmentName,
		InjectedBearerToken: st.InjectedBearerToken,
	}
}

// agentSessionOpen resolves the active LEGACY profile and opens its agent
// session, returning the session itself (for callers that need to act on it
// directly, e.g. forcing a re-mint). It does not obtain a token. Fails with an
// actionable error when the profile has no registered agent. Callers branch on
// activeState FIRST (context-first policy) — this is only ever reached on the
// legacy ~/.jentic fallback or an explicit --profile/--base-url override.
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
