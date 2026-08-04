package cmd

import (
	"errors"
	"fmt"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

type resetPasswordOptions struct {
	config   string
	email    string
	password string
	yes      bool
}

func newResetPasswordCmd(app *App) *cobra.Command {
	opts := &resetPasswordOptions{}
	cmd := &cobra.Command{
		Use:   "reset-password",
		Short: "Set a temporary password for a user who is locked out or forgot theirs",
		Long: "reset-password sets a one-time temporary password for an existing user and\n" +
			"forces them to choose a new one at their next sign-in. The operator never\n" +
			"learns the user's standing password — you hand over the temporary one, the\n" +
			"user changes it themselves, and the temporary credential dies on first use.\n\n" +
			"This is the operator recovery path: there is no email-based self-service\n" +
			"reset. It also clears any login lockout. Drives the app's reset path against\n" +
			"the running stack (Docker compose) or the local install (venv).",
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			return app.resetPasswordE(opts)
		},
	}
	cmd.Flags().StringVar(&opts.config, "config", "",
		"path to the generated config (default: ~/.jentic/jentic-one.yaml)")
	cmd.Flags().StringVar(&opts.email, "email", "", "user email (prompted if omitted)")
	cmd.Flags().StringVar(&opts.password, "password", "",
		"temporary password (prompted if omitted; prefer the prompt to keep it out of shell history)")
	cmd.Flags().BoolVarP(&opts.yes, "yes", "y", false, "skip prompts; require --email and --password")
	return cmd
}

func (a *App) resetPasswordE(opts *resetPasswordOptions) error {
	prompt := credentialPrompt{
		heading:       "Reset a user's password",
		subheading:    "Set a one-time temporary password. The user must change it at next sign-in.",
		emailLabel:    "User email",
		passwordLabel: "Temporary password",
	}
	if err := a.collectCredentials(prompt, opts.yes, &opts.email, &opts.password); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
			return nil
		}
		return err
	}

	if opts.email == "" {
		return errors.New("user email is required (use --email or run interactively)")
	}
	if len(opts.password) < minPasswordLen {
		return fmt.Errorf("temporary password must be at least %d characters", minPasswordLen)
	}

	fmt.Fprintln(a.Out, theme.Infof("Setting a temporary password for %s ...", opts.email))

	// A generated compose file marks a Docker install: reset inside a one-shot
	// app container. Otherwise drive the local venv directly.
	composePath := a.Paths.ComposePath()
	if proc.FileExists(composePath) {
		// The reset runs in a one-shot app container, so a stopped daemon would
		// otherwise surface a raw compose error. Announce the probe first since
		// it may poll (~30s) for a cold-starting daemon (see start.go).
		fmt.Fprintln(a.Out, theme.Infof("Checking the Docker daemon (waiting up to ~30s if it is still starting) ..."))
		if err := requireDockerDaemon("jenticctl reset-password"); err != nil {
			return err
		}
		if err := install.ComposeResetPassword(a.Out, composePath, opts.email, opts.password); err != nil {
			return fmt.Errorf("reset password (docker): %w", err)
		}
	} else {
		configPath := opts.config
		if configPath == "" {
			configPath = a.Paths.InstallConfigPath()
		}
		if !proc.FileExists(configPath) {
			return fmt.Errorf("not configured: %s not found — run `jenticctl install` first", configPath)
		}
		py := install.VenvPython(a.Paths.VenvPath())
		if !proc.FileExists(py) {
			return fmt.Errorf("no local environment at %s — run `jenticctl install` first", a.Paths.VenvPath())
		}
		if err := install.VenvResetPassword(a.Out, py, configPath, opts.email, opts.password); err != nil {
			return fmt.Errorf("reset password (local): %w", err)
		}
	}

	fmt.Fprintln(a.Out, theme.Successf("Temporary password set for %s.", opts.email))
	fmt.Fprintln(a.Out, theme.Dim.Render("  Share it over a trusted channel; they will be required to change it at next sign-in."))
	return nil
}
