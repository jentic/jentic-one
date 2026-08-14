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

// newCredentialsCmd is `jentic credentials`: read-only listing/inspection of the
// identity's credentials. The server already returns CredentialRedactedResponse
// (no live secret); the CLI redaction funnel is belt-and-braces on top. Not
// fenced — read-only, no local config mutation (impl/5.0 §6b, jentic-one#742).
func newCredentialsCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:     "credentials",
		Aliases: []string{"creds"},
		Short:   "List and inspect credentials (secrets are server-masked)",
		Args:    cobra.NoArgs,
		RunE:    func(cmd *cobra.Command, _ []string) error { return cmd.Help() },
	}
	cmd.AddCommand(newCredentialsListCmd(app), newCredentialsShowCmd(app))
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
