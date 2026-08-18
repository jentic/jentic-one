package cmdcore

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// registerResumeHint is shown while waiting for approval, pointing back at the
// command the user actually ran so the documented resume path is correct.
const registerResumeHint = "Waiting for approval (Ctrl-C to stop and resume later with `jentic register`)..."

// registerAndWait is the shared register-and-wait body: mint the env-scoped key
// if absent, perform RFC 7591 DCR (reusing an existing registration so the
// flow is resumable), persist client_id/status, then wait for operator
// approval by attempting the token exchange — exactly the credential every
// data command will use, so success here IS end-to-end proof.
func (a *App) registerAndWait(ctx context.Context, identity, envName, baseURL, clientName string, timeout time.Duration, force bool) error {
	st := theme.StylesFromContext(ctx)
	ref := auth.IdentityRef{Identity: identity, Environment: envName}

	// A jak_* API-key identity has nothing to register: the key IS the
	// long-lived credential.
	if key, err := auth.ReadAPIKey(ref); err == nil && key != "" {
		a.registerProgress(ctx, st.Infof("Identity %q already authenticates to %q with an API key; nothing to register.", identity, envName))
		if isMachineCtx(ctx) {
			ux.FromContext(ctx).Render(ux.Result{
				Status: ux.StatusRegistered, Resource: "identity", Name: identity,
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
	reg := cfg.Identities[identity].Environments[envName]
	if force {
		// Re-register: drop the old registration AND its cached tokens (they
		// were minted as the old client_id and can only confuse from here).
		reg = sdkconfig.EnvRegState{}
		if err := auth.InvalidateTokens(ref); err != nil {
			return err
		}
	}

	clientID := reg.ClientID
	claimToken := ""
	if clientID == "" {
		a.registerProgress(ctx, st.Infof("Registering agent %q with %s ...", clientName, baseURL))
		r, rerr := auth.Register(baseURL, clientName, auth.PublicKeyToJWKS(pub))
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
		if err := saveRegState(identity, envName, clientID, status); err != nil {
			return err
		}
		a.registerProgress(ctx, st.Successf("Registered: client_id=%s status=%s", clientID, status))
	} else {
		a.registerProgress(ctx, st.Infof("Using existing registration client_id=%s (identity %q, environment %q)", clientID, identity, envName))
	}

	// If claiming is enabled, guide the HUMAN to take ownership. This is shown
	// once (the token is single-use, short-lived, never persisted) and before
	// the approval wait, since the human is present now. On a claim-enabled
	// backend the console claim page does claim (sets owner_id) AND approve
	// (pending -> active) — both are required before this agent can mint a
	// token. No-op on the OSS default (empty token).
	a.presentClaimAffordance(ctx, baseURL, clientID, claimToken)

	creds := auth.Credentials{BaseURL: baseURL, IdentityName: identity, EnvironmentName: envName}
	if err := a.waitForApproval(ctx, creds, clientID, timeout, claimToken != ""); err != nil {
		return err
	}
	if err := saveRegState(identity, envName, clientID, "approved"); err != nil {
		return err
	}

	if isMachineCtx(ctx) {
		// Machine mode: one JSON Result on stdout is the terminal success signal,
		// replacing the human "Token minted" / "Ready:" prose (which stays
		// suppressed by registerProgress). If claiming is enabled, surface the
		// fact machine-readably (never the token in prose) so an automated caller
		// can route a human to complete the claim — an agent cannot claim itself.
		res := ux.Result{
			Status: ux.StatusRegistered, Resource: "identity", Name: identity, ID: clientID,
			Message: "approved; token minted",
		}
		if claimToken != "" {
			// A claim needs a HUMAN (an agent cannot claim itself), so the machine
			// signal is the fact + where a human goes — not the raw token, which
			// the key-based redactor would mask anyway and which the agent cannot
			// action. The human-readable token is shown once via presentClaimAffordance.
			res.Fields = map[string]any{
				"claim_required": true,
				"claim_url":      agentClaimURL(baseURL, clientID, ""),
				"claim_command":  fmt.Sprintf("jentic identity claim %s --token <claim_token>", clientID),
			}
		}
		ux.FromContext(ctx).Render(res)
		return nil
	}
	fmt.Fprintln(a.Out, st.Successf("Token minted for %s.", identity))
	// Make the active identity unambiguous and switching obvious: register may
	// have created a NEW per-identity context (env "-" identity), so spell out
	// who you now are and how to move between agents. This is the same name
	// RegisterSetup activated.
	contextName := sdkconfig.SanitizeName(envName + "-" + identity)
	fmt.Fprintf(a.Out, "\n%s\n", st.Dimf("You are now %q on %q (context %q).", identity, envName, contextName))
	fmt.Fprintf(a.Out, "%s %s\n", st.Dim.Render("Switch agents:"), st.Command.Render("jentic context use <name>"))
	fmt.Fprintf(a.Out, "%s %s\n", st.Dim.Render("See all:      "), st.Command.Render("jentic context list"))
	// Human-context nudge (UX6): `register` mints tokens only — it silently skips
	// the agent skill + isolation that `setup` adds. A person who reached here
	// by hand may have wanted the full setup, so point them at it. This whole
	// success block is human-only (machine mode returned above), so no TTY guard
	// is needed.
	fmt.Fprintf(a.Out, "\n%s %s%s\n",
		st.Dim.Render("Tip:"),
		st.Command.Render("jentic setup"),
		st.Dim.Render(" also installs the agent skill and can isolate the agent — run it if you're setting up a coding agent."))
	// Multi-agent case: registering a SECOND agent into an env creates its own
	// per-identity context and silently makes it active (RegisterSetup). Spell
	// out WHY a new context appeared and how to get back, so a user who now sees
	// an unfamiliar active context isn't left wondering what happened to their
	// first agent (UX5).
	if prev := siblingContextInEnv(envName, contextName); prev != "" {
		fmt.Fprintf(a.Out, "\n%s\n", st.Dimf(
			"Created a new context %q (another agent in env %q); your previous context %q is unchanged.",
			contextName, envName, prev))
		fmt.Fprintf(a.Out, "%s %s\n", st.Dim.Render("Switch back:  "), st.Command.Render("jentic context use "+prev))
	}
	a.printNextSteps(st)
	return nil
}

// siblingContextInEnv returns the name of an existing context bound to the same
// environment as (but a different context than) the one just registered — the
// signal that this register minted a SECOND agent into an env that already had
// one. Returns "" when there is no such sibling (the common single-agent case)
// or the config can't be read. Deterministic (lowest name) so the "switch back"
// pointer is stable.
func siblingContextInEnv(envName, currentContext string) string {
	cfg, err := sdkconfig.Load()
	if err != nil {
		return ""
	}
	prev := ""
	for name, c := range cfg.Contexts {
		if name == currentContext || c.Environment != envName {
			continue
		}
		if prev == "" || name < prev {
			prev = name
		}
	}
	return prev
}

// printNextSteps teaches the core discover -> inspect -> execute workflow after a
// successful register. Bare `jentic register` on an already-configured machine
// used to (in V1) reopen onboarding; now it just re-mints, so this block is what
// makes "what do I do now?" obvious — a few copy-pasteable examples plus the
// pointer to full help, in place of a bare "Ready:" line.
func (a *App) printNextSteps(st theme.Styles) {
	fmt.Fprintf(a.Out, "\n%s\n", st.Heading.Render("Next steps"))
	steps := []struct{ desc, cmd string }{
		{"Browse the API catalog", "jentic catalog"},
		{"Find an operation (each result prints a ready-to-paste inspect/execute target)", "jentic search \"send a slack message\""},
		{"See what you can run right now", "jentic access whoami"},
		{"A fresh agent is bound to no APIs — request access to one you found", "jentic access request --toolkit <vendor/name> --wait"},
		{"Inspect that operation (paste the target search printed)", "jentic inspect <METHOD:url from search>"},
		{"Run it (same target)", "jentic execute <METHOD:url from search> -d '{\"key\":\"value\"}'"},
	}
	for _, s := range steps {
		fmt.Fprintf(a.Out, "  %s\n    %s\n", st.Dim.Render(s.desc), st.Command.Render(s.cmd))
	}
	fmt.Fprintf(a.Out, "\n%s %s\n", st.Dim.Render("See all commands:"), st.Command.Render("jentic --help"))
	// Advertise doctor as the first thing to run when stuck (UX9): it is
	// read-only and each of its warnings already carries the exact remediation,
	// but it lives under "Local agent client" and isn't surfaced at the moments
	// of confusion.
	fmt.Fprintf(a.Out, "%s %s\n", st.Dim.Render("Stuck? Check your setup:"), st.Command.Render("jentic doctor"))
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
// still claim + approve in the console before the agent can mint. In that state
// the backend rejects the token exchange with an ambiguous 400 invalid_grant
// "Assertion is invalid" — the SAME string it uses for a real audience mismatch
// (the approval-status gate is checked before signature/audience). So while a
// claim is outstanding we treat that as PENDING (keep waiting / exit clean)
// rather than the hard audience-mismatch failure, which would abort the flow the
// moment it started.
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
		fmt.Fprintf(a.Out, "    %s\n\n", st.Dim.Render(fmt.Sprintf("(or POST %s/agents/%s:approve — requires agents:write)", creds.BaseURL, clientID)))
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
		} else if pending, cerr := classify(err); !pending {
			return cerr
		}
	}
}
