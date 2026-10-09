package api

// connect.go is `jentic connect <vendor>` / `jentic connect --api
// <vendor/name/version>`: the agent-initiable leg of credential provisioning
// (theme-7 Phase 1b), the CLI twin of the request_connection MCP tool. It
// starts a connect session over POST /integrations:connect and prints the
// approval_url the agent relays to its human operator; --wait polls the
// session's status until it connects or ends (the poll_token capability rides
// GET /connect-sessions/{id}/status). A session a human finishes by entering
// a credential (resolved_flow manual_* or awaiting_app) can stay open for
// hours, so --wait does not block on it unless --timeout is set explicitly.
// Deliberately NOT fenced: connecting a credential is the agent's own
// recovery surface — approval still blocks on a human in the browser.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// connectWaitDefault bounds --wait: human approval usually lands within
// minutes; past this the command exits 3 (TIMEOUT_PENDING) so a scripted
// caller can retry the poll later — the session itself stays alive server-side.
const connectWaitDefault = 5 * time.Minute

// errConnectSessionEnded is the --wait poll's "token no longer answers"
// outcome. The status route reports a terminal outcome (rejected, expired,
// cancelled, failed) against the token for a while after the session ends,
// and answers a missing session or a stale token with the same 403
// invalid_poll_token (no enumeration oracle). A repeat ask for the same target
// returns the same session with a fresh token, so a 403 during --wait means
// the session ended or a newer request replaced this token — the CLI cannot
// tell which.
var errConnectSessionEnded = errors.New("connect session ended or replaced by a newer request")

// connectTerminalStatuses ends the --wait loop
// (GET /connect-sessions/{id}/status: pending|polling|connected|failed|expired).
// A rejection arrives as failed with error_code "rejected", a cancel as failed
// with error_code "cancelled".
var connectTerminalStatuses = map[string]bool{
	"connected": true,
	"failed":    true,
	"expired":   true,
}

// connectStatusResponse is the typed projection of the status poll's 200 body
// (the generated JSON200 is untyped).
type connectStatusResponse struct {
	Status       string   `json:"status"`
	ConnectedAs  string   `json:"connected_as,omitempty"`
	CredentialID string   `json:"credential_id,omitempty"`
	BoundScopes  []string `json:"bound_scopes,omitempty"`
	ErrorCode    string   `json:"error_code,omitempty"`
}

func newConnectCmd(a *app) *cobra.Command {
	var scopes []string
	var reason string
	var registration string
	var apiRef string
	var authType string
	var rulesJSON string
	var wait bool
	var timeout time.Duration
	cmd := &cobra.Command{
		Use:   "connect [<vendor>] [--api <vendor/name/version>]",
		Short: "Start connecting a credential for a vendor or a registry API (a human approves it)",
		Long: "connect starts a connect session and prints the approval_url. Name a verified\n" +
			"vendor from the deployment's vendor registry (e.g. `jentic connect github`), or\n" +
			"any registry API with --api <vendor/name/version> (where the deployment takes\n" +
			"API connect requests): its spec decides the credential — pass --auth-type when\n" +
			"it declares several schemes — and --rules proposes the permission rules you need.\n" +
			"Relay the URL to your human operator: they approve the connection (or enter the\n" +
			"API key) in the browser — this command never completes an approval by itself.\n" +
			"Once they confirm, check your new binding with `jentic whoami` and retry the\n" +
			"call that was blocked. --wait polls the session until it connects or ends\n" +
			"(rejected, expired, or cancelled), printing the approval_url and heartbeats\n" +
			"on stderr and a single JSON result on stdout. A session a human finishes by\n" +
			"entering a credential can take hours: --wait returns at once for it unless\n" +
			"--timeout is set — relay the URL, end your turn, and retry later.\n" +
			"When several shared OAuth apps serve the vendor, the error lists them\n" +
			"(details.candidates): ask your user which one to use and re-run with\n" +
			"--registration <registration_id>. Choosing the app is your user's call.",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
			var vendor string
			if len(args) == 1 {
				vendor = args[0]
			}
			if (vendor == "") == (apiRef == "") {
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeMissingArgument,
					Msg:  "connect needs exactly one target: a vendor key or --api <vendor/name/version>",
					Actionable: "Pass a vendor registry key (`jentic connect github`) or a registry API " +
						"(`jentic connect --api <vendor/name/version>`), not both.",
				})
			}
			var api *control.ApiTargetRequest
			if apiRef != "" {
				parsed, err := parseConnectAPI(apiRef)
				if err != nil {
					return reportCoded(aud, &ux.CodedError{
						Code:       ux.CodeMissingArgument,
						Msg:        "--api: " + err.Error(),
						Actionable: "Pass the API identity search or inspect reports, e.g. --api stripe-com/stripe-com-api/2024-06-20.",
					})
				}
				api = parsed
			}
			if authType != "" && api == nil {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        "--auth-type applies to an --api target only",
					Actionable: "Drop --auth-type, or connect the registry API with --api <vendor/name/version>.",
				})
			}
			if len(authType) > connectAuthTypeMax {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        fmt.Sprintf("--auth-type must be at most %d characters, got %d", connectAuthTypeMax, len(authType)),
					Actionable: "Pass a scheme name or kind the API's spec declares (api_key, bearer, basic, oauth2).",
				})
			}
			var rules []control.PermissionRuleSchema
			if rulesJSON != "" {
				decoded, err := decodeConnectRules(json.RawMessage(rulesJSON))
				if err != nil {
					return reportCoded(aud, &ux.CodedError{
						Code: ux.CodeMissingArgument,
						Msg:  "--rules: " + err.Error(),
						Actionable: `Pass a JSON list of rules, e.g. --rules '[{"effect":"allow","methods":["GET"],` +
							`"path":"/v1/charges","match_mode":"exact"}]' (the denial's suggested_rules fit as-is).`,
					})
				}
				rules = decoded
			}
			target := connectTargetLabel(vendor, api)
			rerun := "jentic connect " + vendor
			if api != nil {
				rerun = "jentic connect --api " + target
			}
			// Bound reason client-side (the route's max_length=1024): an
			// overlong reason must be a clear argument error here, not a
			// route 422 rendered as a retryable transport failure.
			if len(reason) > connectReasonMax {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        fmt.Sprintf("--reason must be at most %d characters, got %d", connectReasonMax, len(reason)),
					Actionable: "Shorten --reason to a one-liner the approver can read at a glance.",
				})
			}
			if len(scopes) > connectScopesMax {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        fmt.Sprintf("--scopes must list at most %d scopes, got %d", connectScopesMax, len(scopes)),
					Actionable: "Request only the vendor scopes the task needs; the approver confirms the final set.",
				})
			}
			if len(registration) > connectRegistrationMax {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        fmt.Sprintf("--registration must be at most %d characters, got %d", connectRegistrationMax, len(registration)),
					Actionable: "Pass the registration_id of the shared OAuth app your user picked (e.g. oar_…).",
				})
			}
			// --timeout only shapes --wait; a zero/negative budget is a
			// contradiction ("wait for no time"), not a request for the
			// default — reject it instead of silently substituting.
			if wait && timeout <= 0 {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        fmt.Sprintf("--timeout must be positive with --wait, got %s", timeout),
					Actionable: "Pass a positive --timeout (e.g. --timeout 5m), or drop --wait and poll later.",
				})
			}
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}
			body := control.IntegrationsConnectRequest{Api: api}
			if api == nil {
				body.Vendor = &vendor
			}
			if authType != "" {
				body.AuthType = &authType
			}
			if len(rules) > 0 {
				body.RequestedPermissionRules = &rules
			}
			if len(scopes) > 0 {
				body.RequestedScopes = &scopes
			}
			if reason != "" {
				body.Reason = &reason
			}
			if registration != "" {
				body.OauthAppRegistrationId = &registration
			}
			// Never set body.AgentId: an agent caller IS the agent (the
			// control plane injects the identity and refuses an override);
			// a user token connecting FOR an agent uses the dashboard.
			resp, callErr := client.IntegrationsConnectWithResponse(cmd.Context(), body)
			if err := apiErrorFor(resp, callErr); err != nil {
				return reportCoded(aud, connectCoded(cmd.Context(), client, target, api != nil, err))
			}
			var created connectResponse
			if err := json.Unmarshal(resp.Body, &created); err != nil {
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeInternalError,
					Msg:  "decode /integrations:connect response: " + err.Error(),
				})
			}
			// No poll_token in the envelope: the redaction funnel scrubs
			// *_token keys on every output surface, so a printed capability
			// token would render as [REDACTED] anyway — and the agent's loop
			// doesn't need it (--wait polls with the in-memory token; the
			// binding check is `jentic whoami` either way).
			humanEntry := isHumanEntryFlow(created.ResolvedFlow)
			envelope := map[string]any{
				"session_id":    created.SessionID,
				"approval_url":  created.ApprovalURL,
				"resolved_flow": created.ResolvedFlow,
				"next_step": "Relay the approval_url to your human operator — they approve the " +
					"connection in the browser. Then confirm the new binding with `jentic whoami` " +
					"and retry the call that was blocked.",
			}
			if humanEntry {
				envelope["next_step"] = humanEntryNextStep("`jentic whoami`")
			}
			if !wait {
				aud.Render(envelope)
				return nil
			}
			if humanEntry && !cmd.Flags().Changed("timeout") {
				// A human typing a credential can take hours: blocking the
				// agent's turn on it helps no one. An explicit --timeout
				// still waits that long.
				envelope["status"] = "pending"
				aud.Render(envelope)
				return nil
			}
			// --wait renders ONE stdout document at the end (13 §1). The
			// operator needs the URL while we wait, so it goes to stderr now.
			fmt.Fprintln(a.Err, "Relay this approval_url to your human operator: "+created.ApprovalURL)
			// Ctrl-C during --wait only stops the polling: the connect
			// session (and its pending credential row) stays alive
			// server-side until its TTL expires — accepted behavior; the
			// operator can still approve it and `jentic whoami` shows the
			// resulting binding.
			final, err := a.waitForConnectSession(cmd.Context(), client, created.SessionID, created.PollToken, timeout, humanEntry)
			if errors.Is(err, errConnectSessionEnded) {
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeResolveFailed,
					Msg: fmt.Sprintf("connect session %s ended or was replaced by a newer request "+
						"for the same target", created.SessionID),
					Actionable: "Check `jentic whoami` — if the binding is there, retry the original call. " +
						"Otherwise ask your human user whether they rejected it before running `" + rerun +
						"` again (a newer request for the same target reuses the same approval_url).",
				})
			}
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}
			if final.Status == "connected" {
				envelope["status"] = final.Status
				envelope["connected_as"] = final.ConnectedAs
				envelope["credential_id"] = final.CredentialID
				envelope["bound_scopes"] = final.BoundScopes
				envelope["next_step"] = "Connected. Confirm the new binding with `jentic whoami` " +
					"and retry the call that was blocked."
				aud.Render(envelope)
				return nil
			}
			return reportCoded(aud, connectOutcomeCoded(created.SessionID, final.Status, final.ErrorCode, rerun))
		},
	}
	cmd.Flags().StringSliceVar(&scopes, "scopes", nil,
		"vendor scope names to request (the human approver confirms the final set)")
	cmd.Flags().StringVar(&reason, "reason", "",
		"why you need this connection (shown to the approver; max 1024 chars)")
	cmd.Flags().StringVar(&registration, "registration", "",
		"registration_id of the shared OAuth app to connect through, when several serve the vendor (your user picks it)")
	cmd.Flags().StringVar(&apiRef, "api", "",
		"registry API to connect a credential for, as vendor/name/version (instead of a vendor key)")
	cmd.Flags().StringVar(&authType, "auth-type", "",
		"with --api: the declared scheme name or kind to use (api_key, bearer, basic, oauth2) when the spec declares several")
	cmd.Flags().StringVar(&rulesJSON, "rules", "",
		"permission rules you ask for on the binding, as a JSON list (the approver reviews them; a denial's suggested_rules fit as-is)")
	cmd.Flags().BoolVar(&wait, "wait", false,
		"poll the session until it connects or ends (rejected, expired, or cancelled)")
	cmd.Flags().DurationVar(&timeout, "timeout", connectWaitDefault,
		"how long --wait polls before exiting 3 (TIMEOUT_PENDING)")
	return cmd
}

// waitForConnectSession polls the status route until the session is terminal
// or the budget lapses (TIMEOUT_PENDING, exit 3 — retry later is meaningful:
// the session stays alive server-side). Cadence is the App's shared approval
// schedule; heartbeats ride stderr so they never corrupt the JSON stdout an
// agent parses (the pollImportJobProgress posture).
func (a *app) waitForConnectSession(
	ctx context.Context, client *control.ClientWithResponses, sessionID, pollToken string, timeout time.Duration,
	humanEntry bool,
) (*connectStatusResponse, error) {
	if timeout <= 0 {
		// Callers validate up front (RunE); this guard keeps a direct caller
		// from spinning forever on a zero budget.
		return nil, &ux.CodedError{
			Code: ux.CodeMissingArgument,
			Msg:  fmt.Sprintf("--timeout must be positive, got %s", timeout),
		}
	}
	start := time.Now()
	deadline := start.Add(timeout)
	delay, maxDelay, step := a.PollCadence()
	const heartbeatAfter = 2 * time.Second
	nextHeartbeat := start.Add(heartbeatAfter)
	params := &control.PollConnectSessionStatusParams{PollToken: &pollToken}
	for {
		resp, callErr := client.PollConnectSessionStatusWithResponse(ctx, sessionID, params)
		if err := apiErrorFor(resp, callErr); err != nil {
			var he *HTTPError
			if errors.As(err, &he) && he.StatusCode == http.StatusForbidden {
				return nil, errConnectSessionEnded
			}
			return nil, err
		}
		var status connectStatusResponse
		if err := json.Unmarshal(resp.Body, &status); err != nil {
			return nil, fmt.Errorf("decode connect-session status: %w", err)
		}
		// Normalize the case ONCE: every consumer (the terminal-set check
		// here and the connected/expired/failed switch in RunE) reads the
		// same canonical form, so a differently-cased terminal status can
		// never fall through to the failed arm.
		status.Status = strings.ToLower(strings.TrimSpace(status.Status))
		if connectTerminalStatuses[status.Status] {
			return &status, nil
		}
		if time.Now().After(deadline) {
			actionable := "The approval is still with your operator; re-run with --wait later, or " +
				"confirm the binding with `jentic whoami` once they approve."
			if humanEntry {
				actionable = "A human still has to enter the credential, which can take hours: end your " +
					"turn and retry the call that was blocked later (check `jentic whoami` first)."
			}
			return nil, &ux.CodedError{
				Code: ux.CodeTimeoutPending,
				Msg: fmt.Sprintf("connect session %s still %s after %s", sessionID,
					valueOr(status.Status, "pending"), timeout),
				Actionable: actionable,
			}
		}
		if now := time.Now(); now.After(nextHeartbeat) {
			fmt.Fprintln(a.Err, theme.StylesFromContext(ctx).Dimf(
				"  waiting for approval (%s, %ds elapsed) …",
				valueOr(status.Status, "pending"), int(now.Sub(start).Seconds())))
			nextHeartbeat = now.Add(3 * time.Second)
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(delay):
		}
		if delay < maxDelay {
			delay += step
		}
	}
}

// connectCoded maps the connect route's failure surface onto the coded
// taxonomy — the CLI twin of requestConnectionError (mcp_request_connection.go):
// the API-target and session-cap codes first (apiTargetCoded), then 404 unknown
// vendor / 400 unsupported flow → RESOLVE_FAILED (change the ask), 400
// ambiguous_vendor → RESOLVE_FAILED listing the shared apps (the user picks one),
// 400 invalid_oauth_app_registration → RESOLVE_FAILED (the pin is unusable), 422 →
// RESOLVE_FAILED with the route's reason (in practice a --rules entry), 403 →
// the missing credentials:connect permission (operator grant), 429 → the
// per-actor rate limit, 503 → the vendor's OAuth client is not configured
// (operator action). target is the vendor key or the API identity; isAPI says
// which.
func connectCoded(ctx context.Context, client *control.ClientWithResponses, target string, isAPI bool, err error) *ux.CodedError {
	vendor := target
	rerun := "jentic connect " + target
	if isAPI {
		rerun = "jentic connect --api " + target
	}
	var he *HTTPError
	if errors.As(err, &he) {
		if coded, _ := apiTargetCoded(he, target, err, connectLane{}); coded != nil {
			return coded
		}
		if isAmbiguousVendor(he) {
			candidates := listVendorAppCandidates(ctx, client, vendor, he)
			return ambiguousVendorCoded(vendor, err, candidates,
				fmt.Sprintf("re-run `%s --registration <registration_id>` with the id they pick", rerun))
		}
		if isInvalidRegistration(he) {
			return invalidRegistrationCoded(vendor, err,
				fmt.Sprintf("run `%s` without --registration to see the shared apps that serve it", rerun))
		}
		switch he.StatusCode {
		case http.StatusUnprocessableEntity:
			return &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("the connect request for %s was rejected: %v", target, err),
				Actionable: "Fix the argument the error names (usually a --rules entry: a valid path " +
					"pattern, and an allow rule must constrain methods, path, or operations), then retry.",
			}
		case http.StatusBadRequest, http.StatusNotFound:
			if isAPI {
				return &ux.CodedError{
					Code: ux.CodeResolveFailed,
					Msg:  fmt.Sprintf("cannot start a connect session for API %s: %v", target, err),
					Actionable: "Check the request against the error, then retry `jentic connect --api " +
						target + "`; if it keeps failing, report the gap to your human user.",
				}
			}
			return &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("cannot start a connect session for vendor %q: %v", vendor, err),
				Actionable: "Pass a vendor registry key this deployment supports (e.g. \"github\"). " +
					"For an API outside the registry, run `jentic connect --api <vendor/name/version>` " +
					"instead; if this deployment does not take API connect requests, ask your " +
					"operator to connect a credential in the dashboard.",
			}
		case http.StatusForbidden:
			return &ux.CodedError{
				Code: ux.CodeBrokerDenied,
				Msg:  fmt.Sprintf("starting a connect session requires the credentials:connect permission: %v", err),
				Actionable: "Ask your human operator to grant this agent the credentials:connect " +
					"permission in the dashboard. Once they confirm, run `jentic logout` (clears only the cached " +
					"token) so the next call mints a token carrying the permission, then retry `jentic connect`.",
			}
		case http.StatusTooManyRequests:
			return &ux.CodedError{
				Code:       ux.CodeTransportError,
				Msg:        fmt.Sprintf("connect sessions are rate limited: %v", err),
				Actionable: "Wait briefly and retry `jentic connect`; do not loop on it.",
			}
		case http.StatusServiceUnavailable:
			return &ux.CodedError{
				Code: ux.CodeBrokerDenied,
				Msg:  fmt.Sprintf("vendor %q is registered but not configured on this deployment: %v", vendor, err),
				Actionable: fmt.Sprintf("Ask your human operator to configure the %q vendor's OAuth "+
					"client on this deployment (or connect the credential in the dashboard), then retry.", vendor),
			}
		}
	}
	return asCoded(err)
}

// vendorAppCandidate is one shared OAuth app an ambiguous connect could run
// through: a registration-backed GET /vendors row for the vendor key.
type vendorAppCandidate struct {
	RegistrationID string `json:"registration_id"`
	Name           string `json:"name"`
	DisplayName    string `json:"display_name"`
}

// listVendorAppCandidates reads GET /vendors (capabilities:read, in the
// default agent permission set) and keeps the registration-backed rows the
// 400 ambiguous_vendor problem counted: its registration_ids when it lists
// them (an API-target connect matches apps by the API they serve, not by
// key), else every row for vendor — the same set the route's ambiguity check
// counts when no flow is preferred. Best effort: any failure yields nil, and
// the caller's advice still routes the choice to the user without the list.
func listVendorAppCandidates(ctx context.Context, client *control.ClientWithResponses, vendor string, he *HTTPError) []vendorAppCandidate {
	if client == nil {
		return nil
	}
	wanted := map[string]bool{}
	if he != nil {
		raw, _ := he.Fields()["registration_ids"].([]any)
		for _, v := range raw {
			if id, ok := v.(string); ok && id != "" {
				wanted[id] = true
			}
		}
	}
	resp, err := client.ListVendorsWithResponse(ctx)
	if apiErrorFor(resp, err) != nil || resp.JSON200 == nil {
		return nil
	}
	var out []vendorAppCandidate
	for _, v := range resp.JSON200.Data {
		if v.RegistrationId == nil || *v.RegistrationId == "" {
			continue
		}
		if len(wanted) > 0 && !wanted[*v.RegistrationId] {
			continue
		}
		if len(wanted) == 0 && v.Key != vendor {
			continue
		}
		out = append(out, vendorAppCandidate{
			RegistrationID: *v.RegistrationId,
			Name:           v.Name,
			DisplayName:    v.DisplayName,
		})
	}
	return out
}

// ambiguousVendorCoded is the shared 400 ambiguous_vendor mapping for the CLI
// and the stdio MCP mount (the Python mount renders the same text). Choosing
// which shared OAuth app mints the credential is the user's policy decision,
// so the advice is "ask, then retry with the id" — never "pick one".
// candidates ride details.candidates when the lookup succeeded; retry names
// the lane's retry form.
func ambiguousVendorCoded(vendor string, err error, candidates []vendorAppCandidate, retry string) *ux.CodedError {
	coded := &ux.CodedError{
		Code:       ux.CodeResolveFailed,
		Msg:        fmt.Sprintf("cannot start a connect session for vendor %q: %v", vendor, err),
		Actionable: ambiguousVendorActionable(vendor, len(candidates) > 0, retry),
	}
	if len(candidates) > 0 {
		coded.Details = map[string]any{"candidates": candidates}
	}
	return coded
}

// ambiguousVendorActionable renders the ask-your-user advice. listed reports
// whether details.candidates carries the shared apps.
func ambiguousVendorActionable(vendor string, listed bool, retry string) string {
	which := "ask your human user which app to use and for its registration_id"
	if listed {
		which = "show your human user the apps in details.candidates (name and registration_id) " +
			"and ask which one to use"
	}
	return fmt.Sprintf("Several shared OAuth apps serve vendor %q, and choosing one is your user's "+
		"decision, not yours: %s, then %s.", vendor, which, retry)
}

// invalidRegistrationCoded maps 400 invalid_oauth_app_registration: the pinned
// registration is missing, inactive, or serves another vendor (the route
// answers every cause the same way).
func invalidRegistrationCoded(vendor string, err error, recovery string) *ux.CodedError {
	return &ux.CodedError{
		Code: ux.CodeResolveFailed,
		Msg:  fmt.Sprintf("cannot start a connect session for vendor %q: %v", vendor, err),
		Actionable: fmt.Sprintf("The registration_id does not name an active shared OAuth app for %q. "+
			"Check the id with your human user, or %s.", vendor, recovery),
	}
}

// isAmbiguousVendor reports whether a connect failure is the 400
// ambiguous_vendor problem (errors.py _VENDOR_ERROR_MAP): more than one
// shared OAuth app serves the vendor and the request carried no pin.
func isAmbiguousVendor(he *HTTPError) bool {
	t, _ := he.Fields()["type"].(string)
	return he.StatusCode == http.StatusBadRequest && strings.HasSuffix(t, "ambiguous_vendor")
}

// isInvalidRegistration reports whether a connect failure is the 400
// invalid_oauth_app_registration problem (errors.py _CONNECT_SESSION_ERROR_MAP):
// the pinned registration is not usable for the vendor.
func isInvalidRegistration(he *HTTPError) bool {
	t, _ := he.Fields()["type"].(string)
	return he.StatusCode == http.StatusBadRequest && strings.HasSuffix(t, "invalid_oauth_app_registration")
}
