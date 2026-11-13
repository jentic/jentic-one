package cmdcore

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"net/url"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// registerResumeHint is shown while waiting for approval, pointing back at the
// command the user actually ran so the documented resume path is correct.
const registerResumeHint = "Waiting for approval (Ctrl-C to stop and resume later with `jentic register`)..."

// registrationRequest groups the resolved target and options for one
// register-and-wait run.
type registrationRequest struct {
	identity   string
	envName    string
	baseURL    string
	clientName string
	timeout    time.Duration
	force      bool
}

// registerAndWait is the shared register-and-wait body: mint the env-scoped key
// if absent, perform RFC 7591 DCR (reusing an existing registration so the
// flow is resumable), persist client_id/status, then wait for operator
// approval by attempting the token exchange — exactly the credential every
// data command will use, so success here IS end-to-end proof.
func (a *App) registerAndWait(ctx context.Context, req registrationRequest) error {
	st := theme.StylesFromContext(ctx)
	ref := auth.IdentityRef{Identity: req.identity, Environment: req.envName}

	// A jak_* API-key identity has nothing to register: the key IS the
	// long-lived credential.
	if key, err := auth.ReadAPIKey(ref); err == nil && key != "" {
		a.registerProgress(ctx, st.Infof("Identity %q already authenticates to %q with an API key; nothing to register.", req.identity, req.envName))
		if isMachineCtx(ctx) {
			ux.FromContext(ctx).Render(ux.Result{
				Status: ux.StatusRegistered, Resource: "identity", Name: req.identity,
				Message: "already authenticates with an API key",
			})
		}
		return nil
	}

	priv, err := auth.GetOrGenerateKey(ref)
	if err != nil {
		return err
	}
	pub, ok := priv.Public().(ed25519.PublicKey)
	if !ok {
		return &ux.CodedError{Code: ux.CodeInternalError, Msg: "env-scoped key is not Ed25519"}
	}

	cfg, err := sdkconfig.Load()
	if err != nil {
		return err
	}
	reg := cfg.Identities[req.identity].Environments[req.envName]
	if req.force {
		// Re-register: drop the old registration AND its cached tokens (they
		// were minted as the old client_id and can only confuse from here).
		reg = sdkconfig.EnvRegState{}
		if err := auth.InvalidateTokens(ref); err != nil {
			return err
		}
	}

	// Both Dynamic Client Registration and the approval-wait token exchange
	// ride the environment's SEC-20 CA-pinned client when ca_cert_path is
	// configured, failing closed on a broken bundle (#1205).
	hc, err := clictx.AuthHTTPClient(ctx, cfg.Environments[req.envName].CACertPath)
	if err != nil {
		return err
	}

	clientID := reg.ClientID
	claimToken := ""
	if clientID == "" {
		a.registerProgress(ctx, st.Infof("Registering agent %q with %s ...", req.clientName, req.baseURL))
		r, rerr := auth.RegisterWithClient(hc, req.baseURL, req.clientName, auth.PublicKeyToJWKS(pub))
		if rerr != nil {
			// AGT-21: DCR is the most common failure point (control plane
			// unreachable / TLS / 4xx). Surface it as a coded TRANSPORT_ERROR so
			// an agent gets a closed error_code + actionable step instead of a
			// raw exit-1 string it cannot branch on. The interceptor's
			// decorateCodedErrors renders this through the Audience.
			return &ux.CodedError{
				Code:       ux.CodeTransportError,
				Msg:        "agent registration failed: " + rerr.Error(),
				Actionable: "Check the install URL is reachable (jentic env list) and the control plane is running, then re-run register.",
			}
		}
		clientID = r.ClientID
		// The claim token is minted ONCE, here at registration; a later re-run
		// (existing clientID) never sees it again — matching the backend's
		// "returned once" contract. Empty on the OSS default (no minter).
		claimToken = r.ClaimToken
		status := r.Status
		if status == "" {
			status = "pending"
		}
		if err := saveRegState(req.identity, req.envName, clientID, status); err != nil {
			return err
		}
		a.registerProgress(ctx, st.Successf("Registered: client_id=%s status=%s", clientID, status))
	} else {
		a.registerProgress(ctx, st.Infof("Using existing registration client_id=%s (identity %q, environment %q)", clientID, req.identity, req.envName))
	}

	// If claiming is enabled, guide the HUMAN to take ownership. This is shown
	// once (the token is single-use, short-lived, never persisted) and before
	// the approval wait, since the human is present now. On a claim-enabled
	// backend the console claim page does claim (sets owner_id) AND approve
	// (pending -> active) — both are required before this agent can mint a
	// token. No-op on the OSS default (empty token).
	a.presentClaimAffordance(ctx, req.baseURL, clientID, claimToken)

	creds := auth.Credentials{BaseURL: req.baseURL, IdentityName: req.identity, EnvironmentName: req.envName, HTTPClient: hc}
	if err := a.waitForApproval(ctx, creds, clientID, req.timeout, claimToken != ""); err != nil {
		return err
	}
	if err := saveRegState(req.identity, req.envName, clientID, "approved"); err != nil {
		return err
	}

	a.renderRegistrationSuccess(ctx, req, clientID, claimToken != "")
	return nil
}

// saveRegState persists the (identity, environment) registration record.
func saveRegState(identity, envName, clientID, status string) error {
	return sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
		ident := cfg.Identities[identity]
		if ident.Environments == nil {
			ident.Environments = make(map[string]sdkconfig.EnvRegState)
		}
		ident.Environments[envName] = sdkconfig.EnvRegState{ClientID: clientID, Status: status}
		cfg.Identities[identity] = ident
		return nil
	})
}

// waitForApproval proves approval by forcing a fresh token exchange (the exact
// credential path every data command uses). A pending registration prints the
// operator console link and polls on the shared cadence; the timeout returns
// TIMEOUT_PENDING (exit 3) because re-running after approval resumes cleanly
// (AGT-4 semantics).
//
// claimPending is true when registration returned a claim token: the human must
// still claim + approve in the console before the agent can mint. On backends
// where the approval-status gate runs before signature verification, that state
// comes back as an ambiguous 400 invalid_grant "Assertion is invalid" — the
// SAME string as a real audience mismatch. So while a claim is outstanding we
// treat that as PENDING (keep waiting / exit clean) rather than the hard
// audience-mismatch failure, which would abort the flow the moment it started.
// (The shipped backend now returns a distinct pending detail after verifying
// the signature, which classifies as pending on its own; the claim-context
// tolerance remains for claim-enabled backends that keep the ambiguous string.)
func (a *App) waitForApproval(ctx context.Context, creds auth.Credentials, clientID string, timeout time.Duration, claimPending bool) error {
	st := theme.StylesFromContext(ctx)
	// Force a FRESH mint even if a (stale-scoped or old-client) token is
	// cached: register's contract is "when this returns, the server accepts
	// this identity as of NOW".
	classify := func(err error) (pending bool, out error) {
		// QA-9: an assertion-validation failure (usually an audience mismatch) is
		// NOT pending — polling would hang forever. Stop with an actionable code
		// so the operator fixes the URL/backend rather than waiting. EXCEPTION:
		// when a claim is still outstanding, the backend returns this same string
		// for a not-yet-claimed/approved agent, so treat it as pending instead of
		// aborting (the human is being pointed at the claim console right now).
		// The claim-vs-audience disambiguation lives in client/auth
		// (ClassifyTokenExchange) so this path and the data-plane session path
		// share one rule.
		switch auth.ClassifyTokenExchange(err, auth.ClaimContext{ClaimOutstanding: claimPending}) {
		case auth.OutcomePending:
			return true, nil
		case auth.OutcomeAssertionInvalid:
			var ai *auth.AssertionInvalidError
			_ = errors.As(err, &ai)
			return false, &ux.CodedError{
				Code: ux.CodeNotAuthenticated,
				Msg:  "the backend rejected the signed assertion: " + ai.Error(),
				Actionable: "This is almost always an audience mismatch: the URL you registered with must exactly match " +
					"the backend's canonical_base_url. For a local backend use http://127.0.0.1:8000 (not localhost), " +
					"or align the backend's auth.canonical_base_url to the URL you used.",
			}
		default:
			return false, fmt.Errorf("mint token: %w", err)
		}
	}

	_, err := auth.RefreshBearerToken(creds)
	if err == nil {
		return nil // already approved — nothing to wait for
	}
	if pending, cerr := classify(err); !pending {
		return cerr
	}

	// Pending: point the human at the right console action, or (machine) surface
	// the URL as a stderr diagnostic — never on stdout, which is reserved for the
	// terminal JSON Result. When a claim is outstanding the claim affordance was
	// already printed above (it claims AND approves), so we don't repeat the
	// approve-console block; we just note we're waiting. Otherwise point at the
	// approve console. The TIMEOUT_PENDING envelope also carries the URL in
	// details, so an agent that times out still gets it machine-readably
	// (AGT-21/AGT-4).
	switch {
	case isMachineCtx(ctx) && claimPending:
		fmt.Fprintf(a.Err, "waiting for claim + approval: %s\n", agentClaimURL(creds.BaseURL, clientID, ""))
	case isMachineCtx(ctx):
		fmt.Fprintf(a.Err, "waiting for approval: %s\n", agentConsoleURL(creds.BaseURL, clientID))
	case claimPending:
		fmt.Fprintln(a.Out, "\n"+st.Dim.Render("Waiting for you to claim + approve this agent in the console (see the link above)..."))
		fmt.Fprintln(a.Out, st.Dim.Render(registerResumeHint))
	default:
		fmt.Fprintln(a.Out, "\n"+st.Heading.Render("Approve this agent in the Jentic console:"))
		fmt.Fprintf(a.Out, "    %s\n", st.Command.Render(agentConsoleURL(creds.BaseURL, clientID)))
		fmt.Fprintf(a.Out, "    %s\n\n", st.Dim.Render(fmt.Sprintf("(or POST %s/agents/%s:approve — requires org:admin, or the agent's owner with agents:write)", creds.BaseURL, clientID)))
		fmt.Fprintln(a.Out, st.Dim.Render(registerResumeHint))
	}

	deadline := time.Now().Add(timeout)
	pollInitial, pollMax, pollStep := a.PollCadence()
	delay := pollInitial
	for {
		if time.Now().After(deadline) {
			msg := fmt.Sprintf("timed out after %s waiting for approval; re-run once approved", timeout)
			actionable := "have an operator approve the agent, then re-run the same command"
			details := map[string]any{
				"agent_id":    clientID,
				"approve_url": agentConsoleURL(creds.BaseURL, clientID),
			}
			if claimPending {
				msg = fmt.Sprintf("timed out after %s waiting for the claim + approval; re-run once you've claimed and approved it", timeout)
				actionable = "open the claim link printed above (it claims and approves this agent), then re-run the same command"
				details["claim_url"] = agentClaimURL(creds.BaseURL, clientID, "")
			}
			return &ux.CodedError{
				Code:       ux.CodeTimeoutPending,
				Msg:        msg,
				Actionable: actionable,
				Details:    details,
			}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(delay):
		}
		if delay < pollMax {
			delay += pollStep
		}

		if _, err := auth.BearerToken(creds); err == nil {
			a.registerProgress(ctx, st.Success.Render("Agent approved."))
			return nil
		} else if transientTransport(err) {
			// A dropped connection mid-wait (an idle keep-alive the server
			// closed, a reset, a restart) says nothing about the approval:
			// ask again on the next tick, until the deadline.
			continue
		} else if pending, cerr := classify(err); !pending {
			return cerr
		}
	}
}

// transientTransport reports a transport-level failure of the token exchange —
// the request never got an HTTP answer — as opposed to an answer that refuses.
func transientTransport(err error) bool {
	var urlErr *url.Error
	return errors.As(err, &urlErr)
}
