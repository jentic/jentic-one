package api

import (
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newIdentityClaimCmd implements `jentic identity claim <agent-id> --token <t>`:
// a HUMAN takes ownership of a self-registered (DCR) agent by presenting the
// single-use claim token minted at registration (jentic-one #1042). It POSTs
// /agents/{id}:claim through the generated control SDK, authenticated as the
// active context — the token is the proof, so no agent permission is needed, but
// the backend only accepts a USER actor (an agent cannot claim itself, and gets
// a 403). It is fenced for that reason: claiming is a human-only ownership
// mutation, alongside `identity add`/`delete`.
func newIdentityClaimCmd(app *app) *cobra.Command {
	var token string
	cmd := &cobra.Command{
		Use:   "claim <agent-id>",
		Short: "Claim ownership of a self-registered agent with its one-time claim token",
		Long: "claim binds a self-registered agent to you (the human) using the\n" +
			"single-use token shown once at `jentic register` time. Present it with\n" +
			"--token; an agent cannot claim itself. Requires an active human context\n" +
			"(`jentic context use`).",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.identityClaimE(cmd, args[0], token)
		},
	}
	cmd.Flags().StringVar(&token, "token", "", "The one-time claim token returned at registration (required)")
	_ = cmd.MarkFlagRequired("token")
	return cmd
}

// identityClaimE performs POST /agents/{id}:claim and maps the backend's claim
// error surface onto actionable coded errors (13 §3a). Split out from the
// command constructor so it can be unit-tested against a mock control plane.
func (a *app) identityClaimE(cmd *cobra.Command, agentID, token string) error {
	ctx := cmd.Context()
	aud := ux.FromContext(ctx)

	if strings.TrimSpace(token) == "" {
		return reportCoded(aud, &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        "a claim token is required",
			Actionable: "Pass the one-time token from `jentic register`: `jentic identity claim " + agentID + " --token <claim_token>`.",
		})
	}

	client, err := a.controlClient(ctx)
	if err != nil {
		return reportCoded(aud, err)
	}

	resp, cerr := client.ClaimAgentWithResponse(ctx, agentID, control.ClaimRequest{Token: token})
	if err := apiErrorFor(resp, cerr); err != nil {
		return reportCoded(aud, a.claimErr(err, agentID))
	}

	// Success: ownership transferred. Report as an update to the agent (owner_id
	// transitioned unowned -> you). resp.JSON200 carries the AgentResponse.
	res := ux.Result{
		Status:   ux.StatusUpdated,
		Resource: "agent",
		ID:       agentID,
		Message:  "ownership claimed",
	}
	if resp.JSON200 != nil {
		res.Fields = map[string]any{"owned": true}
	}
	aud.Render(res)
	return nil
}

// claimErr maps a control-plane claim failure onto an actionable coded error,
// keying on HTTP status (the :claim spec documents 400/401/403/422/500/503; the
// backend also returns 409 for an already-owned agent, which falls through here
// by status). Non-HTTPError transport failures pass through unchanged.
func (a *app) claimErr(err error, agentID string) error {
	var he *HTTPError
	if !errors.As(err, &he) {
		return err
	}
	switch he.StatusCode {
	case http.StatusBadRequest, http.StatusUnprocessableEntity:
		return &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        "the claim token was rejected: " + he.Detail(),
			Actionable: "Claim tokens are single-use and short-lived. Re-run `jentic register` to mint a fresh one, then claim promptly.",
		}
	case http.StatusUnauthorized:
		return &ux.CodedError{
			Code:       ux.CodeNotAuthenticated,
			Msg:        "not authenticated to claim: " + he.Detail(),
			Actionable: "Select a registered human context first (`jentic context use <name>`), then retry.",
		}
	case http.StatusForbidden:
		return &ux.CodedError{
			Code:       ux.CodeFenced,
			Msg:        "only a human (USER) may claim an agent: " + he.Detail(),
			Actionable: "Run this from a human context — an agent cannot claim itself. Open the claim link from `jentic register` in a browser instead.",
		}
	case http.StatusNotFound:
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("no agent %q found to claim: %s", agentID, he.Detail()),
			Actionable: "Check the agent id printed at registration (`jentic register`).",
		}
	case http.StatusConflict:
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        "this agent is already owned: " + he.Detail(),
			Actionable: "Ownership is set once; if this is unexpected, contact the current owner or an operator.",
		}
	default:
		return err
	}
}
