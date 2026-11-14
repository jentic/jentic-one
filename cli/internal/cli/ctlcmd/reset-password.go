package ctlcmd

import (
	"context"

	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/spf13/cobra"
)

type resetPasswordOptions = adminFlowOptions // pragma: allowlist secret

func newResetPasswordCmd(app *app) *cobra.Command {
	opts := &resetPasswordOptions{}
	cmd := &cobra.Command{
		Use:   "reset-password",
		Short: "Set a temporary password for a user who is locked out or forgot theirs",
		Long: `reset-password sets a one-time temporary password for an existing user and
forces them to choose a new one at their next sign-in. The operator never
learns the user's standing password — you hand over the temporary one, the
user changes it themselves, and the temporary credential dies on first use.

This is the operator recovery path: there is no email-based self-service
reset. It also clears any login lockout. Drives the app's reset path against
the running stack (Docker compose) or the local install (venv).`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.resetPasswordE(cmd.Context(), opts)
		},
	}
	bindAdminFlowFlags(cmd, opts, "user email", "temporary password")
	return cmd
}

func (a *app) resetPasswordE(ctx context.Context, opts *resetPasswordOptions) error {
	return a.runAdminFlow(ctx, opts, adminFlow{
		cmdName: "jenticctl reset-password",
		prompt: credentialPrompt{
			heading:       "Reset a user's password",
			subheading:    "Set a one-time temporary password. The user must change it at next sign-in.",
			emailLabel:    "User email",
			passwordLabel: "Temporary password",
		},
		progress:  "Setting a temporary password for %s ...",
		success:   "Temporary password set for %s.",
		hint:      "  Share it over a trusted channel; they will be required to change it at next sign-in.",
		docker:    install.ComposeResetPassword,
		venv:      install.VenvResetPassword,
		errPrefix: "reset password",
	})
}
