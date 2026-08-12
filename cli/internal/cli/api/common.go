package api

import (
	"context"
	"errors"
	"fmt"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// notRegisteredErr is the typed form of the single most common agent error
// (AGT-3): the active context's identity has no registration with its
// environment. NOT_AUTHENTICATED so the agent envelope carries a closed-enum
// code instead of raw prose.
func notRegisteredErr(identity, env string) *ux.CodedError {
	return &ux.CodedError{
		Code:       ux.CodeNotAuthenticated,
		Msg:        fmt.Sprintf("identity %q is not registered with environment %q; run `jentic register` first", identity, env),
		Actionable: "jentic register",
	}
}

// agentSession resolves the caller's identity and returns the control-plane
// base URL plus a valid access token. Resolution is CONTEXT-ONLY (activation
// release): the data-plane command family (catalog/search/inspect/access/
// execute/apis) authenticates from the XDG store — the same env URL and
// env-scoped credential that `jentic register` wrote. There is no legacy
// ~/.jentic fallback anymore; an unmigrated machine is stopped up front by the
// migrate gate (cmdcore.installInterceptor), so reaching here without a
// context is a plain "no context" resolve error. Callers build their own typed
// HTTP client from baseURL.
func (a *app) agentSession(ctx context.Context) (baseURL, token string, err error) {
	st, err := a.requireState(ctx)
	if err != nil {
		return "", "", err
	}
	return a.contextSession(st)
}

// requireState returns the active V2 state or the canonical "no active
// context" coded error. It is the single entry every data-plane command goes
// through, so the remediation string cannot drift between commands.
func (a *app) requireState(ctx context.Context) (*clictx.ActiveState, error) {
	if st := clictx.ActiveV2(ctx); st != nil {
		return st, nil
	}
	return nil, noContextErr()
}

// noContextErr is the canonical RESOLVE_FAILED error for "nothing to act as".
func noContextErr() *ux.CodedError {
	return &ux.CodedError{
		Code:       ux.CodeResolveFailed,
		Msg:        "no active context",
		Actionable: "Run `jentic register --url <install URL>` to onboard, or `jentic context use <name>` to select an existing context.",
	}
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
		return "", "", contextAuthErr(err, st)
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

// contextAuthErr turns a credential-resolution failure into an actionable,
// CODED message (AGT-3/AGT-6): not registered → NOT_AUTHENTICATED; awaiting
// approval → PENDING_APPROVAL; anything else (revoked, key mismatch, server
// misconfiguration) → NOT_AUTHENTICATED — all of which `jentic register`
// resolves or diagnoses.
func contextAuthErr(err error, st *clictx.ActiveState) error {
	if errors.Is(err, auth.ErrNotRegistered) {
		return notRegisteredErr(st.IdentityName, st.EnvironmentName)
	}
	var pending *auth.PendingError
	if errors.As(err, &pending) {
		return &ux.CodedError{
			Code: ux.CodePendingApproval,
			Msg: fmt.Sprintf("identity %q is not active yet on %q (%v); wait for approval, then retry",
				st.IdentityName, st.EnvironmentName, err),
			Actionable: "have an operator approve the agent, then re-run the command (`jentic register` resumes the wait)",
		}
	}
	return &ux.CodedError{
		Code: ux.CodeNotAuthenticated,
		Msg: fmt.Sprintf("could not authenticate identity %q with environment %q: %v",
			st.IdentityName, st.EnvironmentName, err),
		Actionable: "jentic register",
	}
}
