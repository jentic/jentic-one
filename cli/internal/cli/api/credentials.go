package api

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/client/paginate"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newCredentialsCmd is `jentic credentials`: listing, inspection and checking of
// the identity's credentials. The server already returns CredentialRedactedResponse
// (no live secret); the CLI redaction funnel is belt-and-braces on top. Not
// fenced — no local config mutation (impl/5.0 §6b, jentic-one#742); `check` is
// gated server-side on credentials:write.
func newCredentialsCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:     "credentials",
		Aliases: []string{"creds"},
		Short:   "List and inspect credentials (secrets are server-masked)",
		Args:    cobra.NoArgs,
		RunE:    func(cmd *cobra.Command, _ []string) error { return cmd.Help() },
	}
	cmd.AddCommand(newCredentialsListCmd(app), newCredentialsShowCmd(app), newCredentialsCheckCmd(app))
	return cmd
}

func newCredentialsListCmd(_ *app) *cobra.Command {
	var vendor, cursor string
	var limit int
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List credentials",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}

			// A single-page fetch when --limit/--cursor is given; otherwise walk all.
			if limit > 0 || cursor != "" {
				items, next, ferr := fetchCredentialPage(cmd.Context(), client, vendor, cursor, limit)
				if ferr != nil {
					return reportCoded(aud, asCoded(ferr))
				}
				aud.Render(ux.NewPage(items, next))
				return nil
			}

			all, aerr := paginate.All(cmd.Context(), func(ctx context.Context, cur string) (paginate.Page[control.CredentialRedactedResponse], error) {
				items, next, ferr := fetchCredentialPage(ctx, client, vendor, cur, 0)
				return paginate.Page[control.CredentialRedactedResponse]{Items: items, Next: next}, ferr
			})
			if aerr != nil {
				return reportCoded(aud, asCoded(aerr))
			}
			aud.Render(ux.NewPage(all, ""))
			return nil
		},
	}
	cmd.Flags().StringVar(&vendor, "vendor", "", "Filter by vendor")
	cmd.Flags().IntVar(&limit, "limit", 0, "Fetch a single page of at most N credentials")
	cmd.Flags().StringVar(&cursor, "cursor", "", "Fetch the page at this opaque cursor")
	return cmd
}

func fetchCredentialPage(ctx context.Context, client *control.ClientWithResponses, vendor, cursor string, limit int) ([]control.CredentialRedactedResponse, string, error) {
	params := &control.ListCredentialsParams{}
	if vendor != "" {
		params.Vendor = &vendor
	}
	if cursor != "" {
		params.Cursor = &cursor
	}
	if limit > 0 {
		params.Limit = &limit
	}
	resp, err := client.ListCredentialsWithResponse(ctx, params)
	if err != nil {
		return nil, "", err
	}
	if resp.JSON200 == nil {
		return nil, "", fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	next := ""
	if resp.JSON200.HasMore && resp.JSON200.NextCursor != nil {
		next = *resp.JSON200.NextCursor
	}
	return resp.JSON200.Data, next, nil
}

func newCredentialsShowCmd(_ *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "show <credential_id>",
		Short: "Show a credential (secret-masked)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}
			resp, err := client.GetCredentialWithResponse(cmd.Context(), args[0])
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}
			if resp.JSON200 == nil {
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeResolveFailed,
					Msg:  fmt.Sprintf("credential %q not found (status %d)", args[0], resp.StatusCode()),
				})
			}
			aud.Render(resp.JSON200)
			return nil
		},
	}
	return cmd
}

// newCredentialsCheckCmd is `jentic credentials check`: one read call with a
// stored credential that names why it fails (#630) instead of the agent finding
// out several steps later.
func newCredentialsCheckCmd(_ *app) *cobra.Command {
	return &cobra.Command{
		Use:   "check <credential_id>",
		Short: "Make one test call with a credential and name why it fails",
		Long: `Make one read call with a stored credential and report what the API says.

The control plane picks a GET with no required input from the API's own spec
and sends it through the broker's egress policy with a 5 second deadline.
Nothing is stored and the secret is never printed.

The status is ok, or why the credential fails: bad_key, expired,
missing_scope, wrong_base_url or unreachable. untested means no call could be
made, and the reason says why.

Exit codes: 0 for ok and untested; 2 for a credential to fix (bad_key,
expired, missing_scope, wrong_base_url); 1 for unreachable, worth retrying.
Needs the credentials:write scope.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}
			resp, err := client.CheckCredentialWithResponse(cmd.Context(), args[0])
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}
			if resp.JSON200 == nil {
				return reportCoded(aud, &ux.CodedError{
					Code: ux.CodeResolveFailed,
					Msg:  fmt.Sprintf("credential %q could not be checked (status %d)", args[0], resp.StatusCode()),
				})
			}
			aud.Render(resp.JSON200)
			if coded := checkFailure(args[0], resp.JSON200); coded != nil {
				return reportCoded(aud, coded)
			}
			return nil
		},
	}
}

// checkFailure maps a failed check onto the exit-code contract: unreachable is
// worth retrying (TRANSPORT_ERROR, exit 1); any other named failure means fix
// the credential, not retry (RESOLVE_FAILED, exit 2). ok and untested pass.
func checkFailure(id string, r *control.CredentialCheckResponse) *ux.CodedError {
	code := ux.CodeResolveFailed
	switch r.Status {
	case control.CredentialCheckStatusOk, control.CredentialCheckStatusUntested:
		return nil
	case control.CredentialCheckStatusUnreachable:
		code = ux.CodeTransportError
	}
	return &ux.CodedError{
		Code:       code,
		Msg:        fmt.Sprintf("credential %s failed its check (%s): %s", id, r.Status, r.Reason),
		Actionable: "Fix what the reason names, then run `jentic credentials check " + id + "` again.",
		Details:    map[string]any{"status": string(r.Status)},
	}
}
