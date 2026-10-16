package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newAccessCmd is `jentic access`: the calling agent's own access through one
// of its credential bindings. Not fenced — read-only, no local config
// mutation (mirrors `jentic credentials`).
func newAccessCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "access",
		Short: "Show what your credential bindings let you do",
		Args:  cobra.NoArgs,
		RunE:  func(cmd *cobra.Command, _ []string) error { return cmd.Help() },
	}
	cmd.AddCommand(newAccessShowCmd(app))
	return cmd
}

func newAccessShowCmd(app *app) *cobra.Command {
	return &cobra.Command{
		Use:   "show <credential_id>",
		Short: "Show your access through one credential: rules, rule set, scopes, suspension",
		Long: "show answers with what your binding to the credential grants: its\n" +
			"permission rules with their ids (data), the shared rule set it points at\n" +
			"(whose rules apply instead of data), the credential's granted OAuth scopes,\n" +
			"and whether the binding is suspended. Credential ids come from whoami.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
			access, err := app.getBindingAccess(cmd.Context(), args[0])
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}
			aud.Render(access)
			return nil
		},
	}
}

// getBindingAccess reads the calling agent's binding to credentialID
// (GET /credentials/{cid}/agents/{aid}/permissions), verbatim. A credential
// the agent is not bound to (or whose binding is suspended, which hides it)
// is a RESOLVE_FAILED pointing at whoami.
func (a *app) getBindingAccess(ctx context.Context, credentialID string) (map[string]any, error) {
	me, err := a.getMe(ctx)
	if err != nil {
		return nil, err
	}
	client, err := a.controlClient(ctx)
	if err != nil {
		return nil, err
	}
	resp, err := client.ListAgentCredentialPermissionsWithResponse(ctx, credentialID, me.Id)
	if err := apiErrorFor(resp, err); err != nil {
		var httpErr *HTTPError
		if errors.As(err, &httpErr) && httpErr.StatusCode == http.StatusNotFound {
			return nil, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("no active credential binding for %q", credentialID),
				Actionable: "check whoami (`jentic whoami`) for your credential bindings and their " +
					"credential ids; a suspended binding is hidden until your operator resumes it",
			}
		}
		return nil, err
	}
	var access map[string]any
	if err := json.Unmarshal(resp.Body, &access); err != nil {
		return nil, fmt.Errorf("decode binding access response: %w", err)
	}
	return access, nil
}
