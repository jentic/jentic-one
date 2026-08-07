package api

import (
	"context"
	"fmt"

	"github.com/jentic/jentic-one/cli/internal/agentauth"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

func newLogoutCmd(app *app) *cobra.Command {
	opts := &identityOptions{}
	cmd := &cobra.Command{
		Use:   "logout",
		Short: "Revoke and clear the profile's cached tokens",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.logoutE(cmd.Context(), opts)
		},
	}
	opts.Bind(cmd)
	return cmd
}

func (a *app) logoutE(ctx context.Context, opts *identityOptions) error {
	profileName, baseURL, err := a.ResolveIdentity(opts.Profile, opts.BaseURL)
	if err != nil {
		return err
	}
	sess, err := agentauth.Open(a.Paths, profileName, baseURL)
	if err != nil {
		return err
	}

	tokens, err := sess.Profile.LoadTokens()
	if err != nil {
		return err
	}
	if tokens != nil && tokens.AccessToken != "" {
		// Best-effort revoke; ignore server-side errors but report transport ones.
		if revErr := sess.Client.Revoke(ctx, tokens.AccessToken, tokens.AccessToken); revErr != nil {
			fmt.Fprintln(a.Out, theme.Warnf("warning: revoke failed: %v", revErr))
		}
		if tokens.RefreshToken != "" {
			_ = sess.Client.Revoke(ctx, tokens.AccessToken, tokens.RefreshToken)
		}
	}
	if err := sess.Profile.ClearTokens(); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Cleared tokens for profile %q", profileName))
	return nil
}
