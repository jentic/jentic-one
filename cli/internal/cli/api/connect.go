package api

// connect.go is `jentic connect <vendor>`: the agent-initiable leg of
// credential provisioning (theme-7 Phase 1b), the CLI twin of the
// request_connection MCP tool. It starts a connect session over
// POST /integrations:connect and prints the approval_url the agent relays to
// its human operator; --wait polls the session's status until it connects or
// ends (the poll_token capability rides GET /connect-sessions/{id}/status).
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

// errConnectSessionEnded is the --wait poll's "session is gone" outcome. An
// unhappy terminal (operator rejected, TTL expired, vendor error, cancelled)
// deletes the pending credential and — by FK cascade — the session row, and
// the status route answers a missing session with the same 403
// invalid_poll_token as a wrong token (no enumeration oracle). The token was
// minted by the create call moments earlier, so during --wait a 403 means the
// session ended without connecting.
var errConnectSessionEnded = errors.New("connect session ended without connecting")

// connectTerminalStatuses ends the --wait loop
// (GET /connect-sessions/{id}/status: pending|polling|connected|failed|expired).
// In practice only "connected" is observed — failed/expired sessions are
// deleted (errConnectSessionEnded) — but a terminal row the route does report
// still ends the loop.
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
	var wait bool
	var timeout time.Duration
	cmd := &cobra.Command{
		Use:   "connect <vendor>",
		Short: "Start connecting a credential for a registry vendor (a human approves it)",
		Long: "connect starts a connect session for a verified vendor from the deployment's\n" +
			"vendor registry (e.g. `jentic connect github`) and prints the approval_url.\n" +
			"Relay that URL to your human operator: they approve the connection and its\n" +
			"scopes in the browser — this command never completes an approval by itself.\n" +
			"Once they confirm, check your new binding with `jentic whoami` and retry the\n" +
			"call that was blocked. --wait polls the session until it connects or ends\n" +
			"(rejected, expired, or cancelled), printing the approval_url and heartbeats\n" +
			"on stderr and a single JSON result on stdout.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
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
			body := control.IntegrationsConnectRequest{Vendor: args[0]}
			if len(scopes) > 0 {
				body.RequestedScopes = &scopes
			}
			if reason != "" {
				body.Reason = &reason
			}
			// Never set body.AgentId: an agent caller IS the agent (the
			// control plane injects the identity and refuses an override);
			// a user token connecting FOR an agent uses the dashboard.
			resp, callErr := client.IntegrationsConnectWithResponse(cmd.Context(), body)
			if err := apiErrorFor(resp, callErr); err != nil {
				return reportCoded(aud, connectCoded(args[0], err))
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
			envelope := map[string]any{
				"session_id":    created.SessionID,
				"approval_url":  created.ApprovalURL,
				"resolved_flow": created.ResolvedFlow,
				"next_step": "Relay the approval_url to your human operator — they approve the " +
					"connection in the browser. Then confirm the new binding with `jentic whoami` " +
					"and retry the call that was blocked.",
			}
			if !wait {
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
			final, err := a.waitForConnectSession(cmd.Context(), client, created.SessionID, created.PollToken, timeout)
			if errors.Is(err, errConnectSessionEnded) {
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeResolveFailed,
					Msg: fmt.Sprintf("connect session %s ended without connecting "+
						"(rejected, expired, or cancelled)", created.SessionID),
					Actionable: "Ask your operator whether they rejected it; otherwise run `jentic connect " +
						args[0] + "` again and relay the fresh approval_url.",
				})
			}
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}
			switch final.Status {
			case "connected":
				envelope["status"] = final.Status
				envelope["connected_as"] = final.ConnectedAs
				envelope["credential_id"] = final.CredentialID
				envelope["bound_scopes"] = final.BoundScopes
				envelope["next_step"] = "Connected. Confirm the new binding with `jentic whoami` " +
					"and retry the call that was blocked."
				aud.Render(envelope)
				return nil
			case "expired":
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeResolveFailed,
					Msg:        fmt.Sprintf("connect session %s expired before it was approved", created.SessionID),
					Actionable: "Run `jentic connect " + args[0] + "` again and relay the fresh approval_url to your operator.",
				})
			default: // failed
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeBrokerDenied,
					Msg: fmt.Sprintf("connect session %s failed (%s)", created.SessionID,
						valueOr(final.ErrorCode, "no error code")),
					Actionable: "Relay the failure to your human operator — they can see the session " +
						"in the dashboard; retry `jentic connect` once the cause is fixed.",
				})
			}
		},
	}
	cmd.Flags().StringSliceVar(&scopes, "scopes", nil,
		"vendor scope names to request (the human approver confirms the final set)")
	cmd.Flags().StringVar(&reason, "reason", "",
		"why you need this connection (shown to the approver; max 1024 chars)")
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
	params := &control.PollConnectSessionStatusParams{PollToken: pollToken}
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
			return nil, &ux.CodedError{
				Code: ux.CodeTimeoutPending,
				Msg: fmt.Sprintf("connect session %s still %s after %s", sessionID,
					valueOr(status.Status, "pending"), timeout),
				Actionable: "The approval is still with your operator; re-run with --wait later, or " +
					"confirm the binding with `jentic whoami` once they approve.",
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
// 404 unknown vendor / 400 unsupported flow → RESOLVE_FAILED (change the ask), 403 → the missing
// credentials:connect scope (operator grant), 429 → the per-actor rate limit,
// 503 → the vendor's OAuth client is not configured (operator action).
func connectCoded(vendor string, err error) *ux.CodedError {
	var he *HTTPError
	if errors.As(err, &he) {
		switch he.StatusCode {
		case 400, 404:
			return &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("cannot start a connect session for vendor %q: %v", vendor, err),
				Actionable: "Pass a vendor registry key this deployment supports (e.g. \"github\"). " +
					"For an API outside the registry, ask your operator to connect a credential " +
					"in the dashboard instead.",
			}
		case 403:
			return &ux.CodedError{
				Code: ux.CodeBrokerDenied,
				Msg:  fmt.Sprintf("starting a connect session requires the credentials:connect scope: %v", err),
				Actionable: "Ask your human operator to grant this agent the credentials:connect " +
					"scope in the dashboard. Once they confirm, run `jentic logout` (clears only the cached " +
					"token) so the next call mints a token carrying the scope, then retry `jentic connect`.",
			}
		case 429:
			return &ux.CodedError{
				Code:       ux.CodeTransportError,
				Msg:        fmt.Sprintf("connect sessions are rate limited: %v", err),
				Actionable: "Wait briefly and retry `jentic connect`; do not loop on it.",
			}
		case 503:
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
