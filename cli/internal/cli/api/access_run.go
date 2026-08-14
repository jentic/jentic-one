package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// Access-request lifecycle status values. The generated SDK models item/request
// status as a plain string, so the CLI keeps its own named constants (moved off
// the deleted internal/accessclient in ARCH-21 A3).
const (
	statusPending           = "pending"
	statusApproved          = "approved"
	statusPartiallyApproved = "partially_approved"
	statusDenied            = "denied"
	statusWithdrawn         = "withdrawn"
	statusExpired           = "expired"
)

var errAccessWaitTimeout = errors.New("timed out waiting for a decision")

// requestIsTerminal reports whether an access request has left the pending
// state (replaces the deleted accessclient.Request.IsTerminal()).
func requestIsTerminal(r *control.AccessRequestResponse) bool {
	return r.Status != statusPending
}

// staleScopes returns the scopes the agent has been granted that the presented
// token does not yet carry — grants that landed after the token was minted and
// won't take effect until it is refreshed (`jentic access refresh`, issue #673).
// A nil token-scope list means the server did not report token scopes at all
// (staleness is then unknowable), so we report none rather than nagging; an
// explicitly empty list is honored. Replaces accessclient.Me.StaleScopes().
func staleScopes(scopes, tokenScopes []string) []string {
	if tokenScopes == nil {
		return nil
	}
	inToken := make(map[string]struct{}, len(tokenScopes))
	for _, s := range tokenScopes {
		inToken[s] = struct{}{}
	}
	var stale []string
	for _, s := range scopes {
		if _, ok := inToken[s]; !ok {
			stale = append(stale, s)
		}
	}
	return stale
}

func (a *app) accessWhoamiE(cmd *cobra.Command, jsonFlag bool) error {
	ctx := cmd.Context()
	me, err := a.getMe(ctx)
	if err != nil {
		return err
	}
	if cmdcore.JSONOrPretty(cmd, jsonFlag) {
		return cmdcore.WriteJSON(a.Out, me)
	}
	a.printMe(me)
	return nil
}

// getMe fetches the caller's identity via GET /me and returns the AGENT variant.
//
// GET /me returns a discriminated union (MeUser | MeAgent | MeServiceAccount)
// keyed on `type`. The generated AsMeAgent() does NOT validate the discriminator
// — it would happily decode a user/service-account body into an agent-shaped
// value with empty bindings, which reads as an approved agent bound to nothing.
// So we probe the raw body's `type` first and reject a non-agent token, matching
// the guard the deleted accessclient.Me() enforced.
func (a *app) getMe(ctx context.Context) (*control.MeAgent, error) {
	client, err := a.controlClient(ctx)
	if err != nil {
		return nil, err
	}
	resp, err := client.GetMeWithResponse(ctx)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	// Decode straight from the raw body rather than resp.JSON200: /me is a
	// discriminated union and we need the `type` discriminator to reject a
	// non-agent token (AsMeAgent does not validate it — it would decode a
	// user/service-account into an empty-bindings agent). Reading resp.Body also
	// avoids depending on the response Content-Type (the generated typed field
	// is only populated for an application/json content type).
	var probe struct {
		Type string `json:"type"`
	}
	if err := json.Unmarshal(resp.Body, &probe); err != nil {
		return nil, fmt.Errorf("decode /me response: %w", err)
	}
	if probe.Type != "" && probe.Type != "agent" {
		return nil, fmt.Errorf("this token belongs to a %q, not an agent; agent commands require an agent token", probe.Type)
	}
	var agent control.MeAgent
	if err := json.Unmarshal(resp.Body, &agent); err != nil {
		return nil, fmt.Errorf("decode /me response: %w", err)
	}
	return &agent, nil
}

func (a *app) accessRequestE(cmd *cobra.Command, opts *accessRequestOptions) error {
	items, err := opts.compose()
	if err != nil {
		return err
	}

	ctx := cmd.Context()
	st, err := a.requireState(ctx)
	if err != nil {
		return err
	}
	client, err := a.controlClient(ctx)
	if err != nil {
		return err
	}

	fileResp, err := client.FileAccessRequestWithResponse(ctx, control.AccessRequestFileRequest{
		Reason: strEmptyToNil(opts.reason),
		Items:  items,
	})
	if err != nil {
		return err
	}
	var req *control.AccessRequestResponse
	switch {
	case fileResp.JSON202 != nil:
		req = fileResp.JSON202
	case fileResp.ApplicationproblemJSON409 != nil:
		dup := fileResp.ApplicationproblemJSON409
		// Filing is all-or-nothing, so a 409 on a composite means one of its
		// targets is already pending and NOTHING was filed. Attaching would
		// silently swap the composite for the older, smaller request — the
		// agent would wait on a grant that covers only part of what it asked
		// for. Surface the collision instead so the agent drops the pending
		// target or withdraws the older request and re-files.
		if opts.targetCount() > 1 {
			return fmt.Errorf("nothing was filed: one of the requested targets already has a pending request (%s); "+
				"inspect it with `jentic access status %s`, then either drop that target from this request "+
				"or withdraw the pending one (`jentic access withdraw %s`) and re-file",
				dup.ExistingRequestId, dup.ExistingRequestId, dup.ExistingRequestId)
		}
		fmt.Fprintln(a.Out, theme.Warnf("A pending request already exists (%s); attaching to it.", dup.ExistingRequestId))
		req, err = a.getAccessRequest(ctx, client, dup.ExistingRequestId)
		if err != nil {
			return err
		}
	default:
		// A non-2xx, non-409 response: surface it through the shared adapter.
		if aerr := apiErrorFor(fileResp, nil); aerr != nil {
			return aerr
		}
		return fmt.Errorf("unexpected backend response (status %d)", fileResp.StatusCode())
	}

	timedOut := false
	if opts.wait && !requestIsTerminal(req) {
		waited, waitErr := a.pollAccessRequest(ctx, client, req.Id, opts.timeout)
		if waitErr != nil {
			if errors.Is(waitErr, errAccessWaitTimeout) {
				// Print the (still-pending) request so the agent has the id and
				// approve_url, then signal "pending" via a distinct exit code.
				timedOut = true
			} else {
				return waitErr
			}
		} else {
			req = waited
		}
	}

	if cmdcore.JSONOrPretty(cmd, opts.json) {
		absolutizeApproveURL(st.BaseURL, req)
		if err := cmdcore.WriteJSON(a.Out, req); err != nil {
			return err
		}
	} else {
		absolutizeApproveURL(st.BaseURL, req)
		a.printRequest(req, true)
	}

	switch {
	case timedOut:
		// Codes here mirror the exit taxonomy the help text documents (AGT-6):
		// the decorator renders the envelope/styled line from the CodedError, so
		// envelope code and exit code come from the same table.
		return &ux.CodedError{
			Code:       ux.CodeTimeoutPending,
			Msg:        fmt.Sprintf("still pending after %s", opts.timeout),
			Actionable: "jentic access status " + req.Id,
		}
	case req.Status == statusPartiallyApproved:
		// A newly-granted scope only takes effect once re-minted into the token;
		// do it for the agent so it needn't run a separate `access refresh`.
		a.refreshIfScopeGranted(cmd, req)
	}
	if err := terminalAccessError(req); err != nil {
		return err
	}
	// Fully approved. A newly-granted scope bakes into the token at mint time, so
	// re-mint now if the request granted one — the agent can then execute
	// immediately without a separate `access refresh`. A binding-only plan
	// (toolkit/credential binds, no scope) needs no re-mint: bindings are live
	// server-side, so this is a no-op in that case.
	a.refreshIfScopeGranted(cmd, req)
	return nil
}

// terminalAccessError maps a decided access request's status to the coded error
// that drives the exit taxonomy documented in the `access request` help (AGT-6):
//
//	denied / expired / withdrawn -> BROKER_DENIED  (exit 2) — the agent still
//	    cannot do what it asked, so this must never look like success.
//	partially_approved           -> PARTIAL_APPROVAL (exit 4) — some items were
//	    granted but at least one was not, so a scripted agent must not proceed as
//	    if the capability is fully available; the printed items show what remains.
//	anything else (approved, …)  -> nil (exit 0).
//
// Pure and status-only so the exit-code contract can be tested without a live
// backend (QA-20). The caller handles timeout (TIMEOUT_PENDING) and the re-mint
// side effect before calling this.
func terminalAccessError(req *control.AccessRequestResponse) error {
	switch req.Status {
	case statusDenied:
		return &ux.CodedError{
			Code:       ux.CodeBrokerDenied,
			Msg:        fmt.Sprintf("access request %s was denied", req.Id),
			Actionable: "jentic access status " + req.Id,
		}
	case statusExpired, statusWithdrawn:
		return &ux.CodedError{
			Code:       ux.CodeBrokerDenied,
			Msg:        fmt.Sprintf("request %s is %s, not approved; nothing was granted", req.Id, req.Status),
			Actionable: "jentic access status " + req.Id,
		}
	case statusPartiallyApproved:
		return &ux.CodedError{
			Code:       ux.CodePartialApproval,
			Msg:        "partially approved — not all requested items were granted",
			Actionable: "jentic access status " + req.Id,
		}
	}
	return nil
}

// refreshIfScopeGranted re-mints the agent's token when (and only when) the
// decided request granted a new scope — the one thing that is baked into the
// token at mint time and so needs a refresh to become usable. Toolkit/credential
// bindings are resolved live by the broker, so a `--provision`/`--toolkit` plan
// needs no re-mint; re-minting anyway would be a wasted round-trip. Best-effort:
// a mint failure is non-fatal (the agent can still run `jentic access refresh`),
// and static credentials (injected token, API key) are skipped.
func (a *app) refreshIfScopeGranted(cmd *cobra.Command, req *control.AccessRequestResponse) {
	if !requestGrantedScope(req) {
		return
	}
	st := clictx.ActiveContext(cmd.Context())
	if st == nil {
		return
	}
	creds := credsFromState(st)
	// Static credentials (injected token, jak_* API key) have no mintable
	// token — nothing to refresh.
	if creds.InjectedBearerToken != "" {
		return
	}
	if key, err := auth.ReadAPIKey(creds.IdentityRef()); err == nil && key != "" {
		return
	}
	if _, err := auth.RefreshBearerToken(creds); err != nil {
		fmt.Fprintln(a.Err, theme.Dimf("granted scope not yet on your token; run `jentic access refresh` to pick it up"))
	}
}

// requestGrantedScope reports whether a decided request approved a scope:grant
// item — the only grant that bakes into the token and so needs a re-mint.
// Toolkit/credential binds are resolved live by the broker, so a binding-only
// plan returns false (no re-mint needed).
func requestGrantedScope(req *control.AccessRequestResponse) bool {
	for _, it := range req.Items {
		if it.ResourceType == "scope" && it.Action == "grant" && it.Status == "approved" {
			return true
		}
	}
	return false
}

func (a *app) accessListE(cmd *cobra.Command, opts *accessListOptions) error {
	ctx := cmd.Context()
	client, err := a.controlClient(ctx)
	if err != nil {
		return err
	}

	const maxPages = 1000
	var all []control.AccessRequestResponse
	var hasMore bool
	var nextCursor string
	cursor := opts.cursor

	for page := 0; ; page++ {
		params := &control.ListAccessRequestsParams{}
		if opts.status != "" {
			params.Status = ptr(opts.status)
		}
		if cursor != "" {
			params.Cursor = ptr(cursor)
		}
		if opts.limit > 0 {
			params.Limit = ptr(opts.limit)
		}
		resp, listErr := client.ListAccessRequestsWithResponse(ctx, params)
		if err := apiErrorFor(resp, listErr); err != nil {
			return err
		}
		if resp.JSON200 == nil {
			return fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
		}
		res := resp.JSON200
		all = append(all, res.Data...)
		hasMore = res.HasMore
		nextCursor = deref(res.NextCursor)
		if !opts.all || !res.HasMore || nextCursor == "" {
			break
		}
		if page+1 >= maxPages {
			break
		}
		cursor = nextCursor
	}

	if cmdcore.JSONOrPretty(cmd, opts.json) {
		return cmdcore.WriteList(a.Out, all, nextCursor, nil)
	}
	a.printRequestList(all, hasMore)
	return nil
}

func (a *app) accessStatusE(cmd *cobra.Command, id string, jsonFlag bool) error {
	ctx := cmd.Context()
	st, err := a.requireState(ctx)
	if err != nil {
		return err
	}
	client, err := a.controlClient(ctx)
	if err != nil {
		return err
	}
	req, err := a.getAccessRequest(ctx, client, id)
	if err != nil {
		return err
	}
	absolutizeApproveURL(st.BaseURL, req)
	if cmdcore.JSONOrPretty(cmd, jsonFlag) {
		return cmdcore.WriteJSON(a.Out, req)
	}
	a.printRequest(req, true)
	return nil
}

func (a *app) accessWithdrawE(cmd *cobra.Command, id string, jsonFlag bool) error {
	ctx := cmd.Context()
	client, err := a.controlClient(ctx)
	if err != nil {
		return err
	}
	resp, err := client.WithdrawAccessRequestWithResponse(ctx, id)
	if err := apiErrorFor(resp, err); err != nil {
		return err
	}
	if resp.JSON200 == nil {
		return fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	req := resp.JSON200
	if cmdcore.JSONOrPretty(cmd, jsonFlag) {
		return cmdcore.WriteJSON(a.Out, req)
	}
	fmt.Fprintln(a.Out, theme.Successf("Withdrew access request %s.", req.Id))
	a.printRequest(req, false)
	return nil
}

// getAccessRequest fetches a single access request by id via GET
// /access-requests/{id}, mapping non-2xx through the shared adapter.
func (a *app) getAccessRequest(ctx context.Context, client *control.ClientWithResponses, id string) (*control.AccessRequestResponse, error) {
	resp, err := client.GetAccessRequestWithResponse(ctx, id)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	return resp.JSON200, nil
}

func (a *app) accessRefreshE(cmd *cobra.Command, jsonFlag bool) error {
	st, err := a.requireState(cmd.Context())
	if err != nil {
		return err
	}
	return a.accessRefreshContextE(cmd, st, jsonFlag)
}

// accessRefreshContextE is the context arm of `jentic access refresh`: force
// a fresh assertion exchange for the active (identity, environment) so the new
// token carries the current scope grants, then confirm with /me. Same
// fresh-mint-not-refresh-token semantics as the legacy arm (issue #673).
func (a *app) accessRefreshContextE(cmd *cobra.Command, st *clictx.ActiveState, jsonFlag bool) error {
	creds := credsFromState(st)
	if creds.InjectedBearerToken != "" {
		return errors.New("this session uses an injected bearer token ($JENTIC_BEARER_TOKEN), which the CLI cannot re-mint; " +
			"obtain a fresh token from your orchestrator")
	}
	if key, err := auth.ReadAPIKey(creds.IdentityRef()); err == nil && key != "" {
		return fmt.Errorf("identity %q authenticates with a static API key, which has no token to refresh; "+
			"its scopes change only when an admin updates the key", st.IdentityName)
	}
	if st.BaseURL == "" {
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("environment %q has no base_url", st.EnvironmentName),
			Actionable: "Set it with `jentic env add` / edit the environment.",
		}
	}
	// Force a fresh assertion exchange so the re-minted token carries the
	// current scope grants (invalidate + re-mint); the SDK client getMe uses
	// then resolves that freshly-cached token. Issue #673.
	if _, err := auth.RefreshBearerToken(creds); err != nil {
		return asCoded(err)
	}
	me, err := a.getMe(cmd.Context())
	if err != nil {
		return err
	}
	if cmdcore.JSONOrPretty(cmd, jsonFlag) {
		return cmdcore.WriteJSON(a.Out, me)
	}
	fmt.Fprintln(a.Out, theme.Successf("Refreshed token for %s.", me.Id))
	a.printMe(me)
	return nil
}

// pollAccessRequest loops Get until the request leaves the pending state, the
// timeout elapses, or the context is cancelled. It reuses the register poll
// cadence so the wait backs off the same way.
func (a *app) pollAccessRequest(ctx context.Context, client *control.ClientWithResponses, id string, timeout time.Duration) (*control.AccessRequestResponse, error) {
	fmt.Fprintln(a.Out, theme.Dimf("Waiting for a human to decide request %s (up to %s; Ctrl-C to stop) …", id, timeout))
	deadline := time.Now().Add(timeout)
	pollInitial, pollMax, pollStep := a.PollCadence()
	delay := pollInitial
	for {
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("%w after %s (request %s)", errAccessWaitTimeout, timeout, id)
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(delay):
		}
		if delay < pollMax {
			delay += pollStep
		}
		req, err := a.getAccessRequest(ctx, client, id)
		if err != nil {
			return nil, err
		}
		if requestIsTerminal(req) {
			return req, nil
		}
	}
}

// absolutizeApproveURL rewrites a relative approve_url onto the environment's base
// URL so the value the CLI prints (or emits as JSON) is directly openable, rather
// than a path fragment the operator has to guess a host for (impl/5.0 §6b,
// jentic-one#777). An already-absolute URL and an empty value are left untouched.
func absolutizeApproveURL(baseURL string, r *control.AccessRequestResponse) {
	if r == nil || r.ApproveUrl == "" {
		return
	}
	if u, err := url.Parse(r.ApproveUrl); err == nil && u.IsAbs() {
		return
	}
	base, err := url.Parse(strings.TrimRight(baseURL, "/"))
	if err != nil {
		return
	}
	ref, err := url.Parse(r.ApproveUrl)
	if err != nil {
		return
	}
	r.ApproveUrl = base.ResolveReference(ref).String()
}
