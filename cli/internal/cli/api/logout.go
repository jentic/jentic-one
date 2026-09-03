package api

import (
	"context"
	"errors"
	"fmt"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

func newLogoutCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "logout",
		Short: "Revoke and clear the active identity's cached token",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.logoutE(cmd.Context())
		},
	}
	return cmd
}

// logoutE revokes (best-effort, RFC 7009) and clears the XDG-stored token for
// the active (identity, environment). Static credentials are untouched — an
// injected token belongs to the orchestrator and a jak_* API key is a
// long-lived credential removed via `jentic identity delete`, not logout.
func (a *app) logoutE(ctx context.Context) error {
	st, err := a.requireState(ctx)
	if err != nil {
		return err
	}
	return a.logoutContextE(ctx, st)
}

func (a *app) logoutContextE(_ context.Context, st *clictx.ActiveState) error {
	// No mint happens here (logout only reads/clears local state and revokes),
	// so this deliberately does NOT go through credsFromState: a broken
	// ca_cert_path must never block clearing local tokens.
	if st.InjectedBearerToken != "" {
		return errors.New("this session uses an injected bearer token ($JENTIC_BEARER_TOKEN); " +
			"there is no CLI-stored token to clear — unset the variable instead")
	}
	ref := auth.IdentityRef{Identity: st.IdentityName, Environment: st.EnvironmentName}
	tokens, err := auth.ReadTokens(ref)
	if err == nil && tokens != nil && tokens.AccessToken != "" && st.BaseURL != "" {
		// Best-effort revoke; report but do not fail on server/transport errors.
		if revErr := auth.RevokeToken(st.BaseURL, tokens.AccessToken, tokens.AccessToken); revErr != nil {
			fmt.Fprintln(a.Out, theme.Warnf("warning: revoke failed: %v", revErr))
		}
	}
	if err := auth.InvalidateTokens(ref); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Cleared tokens for identity %q in environment %q", st.IdentityName, st.EnvironmentName))
	return nil
}
