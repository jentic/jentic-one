package api

import (
	"context"
	"errors"
	"fmt"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/internal/agentauth"
	"github.com/jentic/jentic-one/cli/internal/authclient"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
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
	// Context-first (same policy as agentSession): with an active V2 context,
	// logout revokes and clears the XDG-stored token for the active
	// (identity, environment). --profile/--base-url pin the legacy store.
	if st := a.activeState(ctx, opts); st != nil {
		return a.logoutContextE(ctx, st)
	}
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

// logoutContextE is the V2-context arm of logout: best-effort server-side
// revoke of the cached access token, then drop it from the XDG state dir.
// Static credentials are untouched — an injected token belongs to the
// orchestrator and a jak_* API key is a long-lived credential removed via
// `jentic identity delete`, not logout.
func (a *app) logoutContextE(ctx context.Context, st *clictx.ActiveState) error {
	creds := credsFromState(st)
	if creds.InjectedBearerToken != "" {
		return errors.New("this session uses an injected bearer token ($JENTIC_BEARER_TOKEN); " +
			"there is no CLI-stored token to clear — unset the variable instead")
	}
	ref := creds.IdentityRef()
	tokens, err := auth.ReadTokens(ref)
	if err == nil && tokens != nil && tokens.AccessToken != "" && st.BaseURL != "" {
		// Best-effort revoke; ignore server-side errors but report transport ones.
		if revErr := authclient.New(st.BaseURL).Revoke(ctx, tokens.AccessToken, tokens.AccessToken); revErr != nil {
			fmt.Fprintln(a.Out, theme.Warnf("warning: revoke failed: %v", revErr))
		}
	}
	if err := auth.InvalidateTokens(ref); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Cleared tokens for identity %q in environment %q", st.IdentityName, st.EnvironmentName))
	return nil
}
