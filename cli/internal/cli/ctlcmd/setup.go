package ctlcmd

import (
	"context"

	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/spf13/cobra"
)

type setupOptions = adminFlowOptions

func newSetupCmd(app *app) *cobra.Command {
	opts := &setupOptions{}
	cmd := &cobra.Command{
		Use:   "setup",
		Short: "Create the first admin account (one-time first-run setup)",
		Long: "setup creates the first administrator for a freshly-installed jentic-one.\n" +
			"It drives the app's one-time create-admin path against the running stack\n" +
			"(Docker compose) or the local install (venv), so the credential never\n" +
			"leaves the operator boundary — there is no default password to rotate.\n\n" +
			"This succeeds only while no users exist; once an admin is created, manage\n" +
			"further users from the admin UI. Run `jenticctl install` first if you have\n" +
			"not set up locally yet.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.setupE(cmd.Context(), opts)
		},
	}
	bindAdminFlowFlags(cmd, opts, "admin email", "admin password")
	return cmd
}

func (a *app) setupE(ctx context.Context, opts *setupOptions) error {
	return a.runAdminFlow(ctx, opts, adminFlow{
		cmdName: "jenticctl setup",
		prompt: credentialPrompt{
			heading:       "First-run setup",
			subheading:    "Create the first administrator. This runs once; afterwards manage users in the UI.",
			emailLabel:    "Admin email",
			passwordLabel: "Admin password",
		},
		progress:  "Creating the first admin account ...",
		success:   "Admin account created for %s.",
		hint:      "  Sign in at the admin UI with the email and password you just set.",
		docker:    install.ComposeCreateAdmin,
		venv:      install.VenvCreateAdmin,
		errPrefix: "create admin",
	})
}
