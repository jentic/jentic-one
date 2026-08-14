package api

import (
	"context"
	"errors"
	"fmt"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/client/generated/control"
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
		Msg:        fmt.Sprintf("identity %q is not registered with environment %q; run `jentic register` first (or `jentic doctor` to check your setup)", identity, env),
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

// requireState returns the active state or the canonical "no active
// context" coded error. It is the single entry every data-plane command goes
// through, so the remediation string cannot drift between commands.
func (a *app) requireState(ctx context.Context) (*clictx.ActiveState, error) {
	if st := clictx.ActiveContext(ctx); st != nil {
		return st, nil
	}
	return nil, noContextErr()
}

// controlClient resolves the generated control client for the active context,
// surfacing the canonical CODED credential error first. GetControlClient defers
// auth to the SDK transport, whose raw ErrNotRegistered/pending errors would
// bypass the AGT-3/AGT-6 coded remediation (`jentic register`, right exit code)
// the data-plane commands promise. So we pre-flight contextSession, which maps
// a credential-resolution failure to that coded error, then hand back the SDK
// client (which re-resolves the now-cached credential). This is the single
// entry point the migrated data-plane commands use (ARCH-21).
func (a *app) controlClient(ctx context.Context) (*control.ClientWithResponses, error) {
	st, err := a.requireState(ctx)
	if err != nil {
		return nil, err
	}
	if _, _, err := a.contextSession(st); err != nil {
		return nil, err
	}
	return clictx.GetControlClient(ctx)
}

// noContextErr is the canonical RESOLVE_FAILED error for "nothing to act as".
func noContextErr() *ux.CodedError {
	return &ux.CodedError{
		Code:       ux.CodeResolveFailed,
		Msg:        "no active context",
		Actionable: "Run `jentic register --url <install URL>` to onboard, or `jentic context use <name>` to select an existing context.",
	}
}

// contextSession obtains (baseURL, bearer) for an active context via the
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
	// A data-plane command never has a claim outstanding, so the claim-vs-audience
	// ambiguity resolves the same way ClassifyTokenExchange does for the register
	// wait loop (QA-9/QA-24): one shared rule in client/auth, two remediations.
	switch auth.ClassifyTokenExchange(err, auth.ClaimContext{}) {
	case auth.OutcomePending:
		return &ux.CodedError{
			Code: ux.CodePendingApproval,
			Msg: fmt.Sprintf("identity %q is not active yet on %q (%v); wait for approval, then retry",
				st.IdentityName, st.EnvironmentName, err),
			Actionable: "have an operator approve the agent, then re-run the command (`jentic register` resumes the wait)",
		}
	case auth.OutcomeAssertionInvalid:
		// QA-24: an assertion-validation failure on a data-plane command is the same
		// audience-mismatch papercut the register poll path (QA-9) already special-
		// cases — surface the URL/canonical_base_url hint here too, rather than the
		// generic "run register" below (correct exit code, but a weaker remediation
		// that sends the agent in a loop).
		return &ux.CodedError{
			Code: ux.CodeNotAuthenticated,
			Msg: fmt.Sprintf("the backend rejected the signed assertion for identity %q on %q: %v",
				st.IdentityName, st.EnvironmentName, err),
			Actionable: "This is almost always an audience mismatch: the environment's URL must exactly match " +
				"the backend's canonical_base_url. For a local backend use http://127.0.0.1:8000 (not localhost), " +
				"or align the backend's auth.canonical_base_url to the URL you used.",
		}
	}
	return &ux.CodedError{
		Code: ux.CodeNotAuthenticated,
		Msg: fmt.Sprintf("could not authenticate identity %q with environment %q: %v (run `jentic doctor` to check your setup)",
			st.IdentityName, st.EnvironmentName, err),
		Actionable: "jentic register",
	}
}
