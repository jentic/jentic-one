package ctlcmd

import (
	"context"
	"errors"
	"fmt"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

const minPasswordLen = 12

type setupOptions struct {
	config   string
	email    string
	password string
	yes      bool
}

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
	cmd.Flags().StringVar(&opts.config, "config", "",
		"path to the generated config (default: ~/.jentic/jentic-one.yaml)")
	cmd.Flags().StringVar(&opts.email, "email", "", "admin email (prompted if omitted)")
	cmd.Flags().StringVar(&opts.password, "password", "",
		"admin password (prompted if omitted; prefer the prompt to keep it out of shell history)")
	cmd.Flags().BoolVarP(&opts.yes, "yes", "y", false, "skip prompts; require --email and --password")
	return cmd
}

func (a *app) setupE(ctx context.Context, opts *setupOptions) error {
	// A generated compose file marks a Docker install: the admin is created in a
	// one-shot app container. Probe the daemon up front — before prompting for
	// credentials — so a user on a stopped daemon isn't asked to type an email
	// and password only to be told the daemon is down afterwards. The probe may
	// poll (~30s) for a cold-starting daemon, so announce it (see start.go).
	composePath := a.Paths.ComposePath()
	dockerInstall := proc.FileExists(composePath)
	if dockerInstall {
		announceDaemonCheck(a.Out)
		if err := requireDockerDaemon(ctx, "jenticctl setup"); err != nil {
			return err
		}
	}

	prompt := credentialPrompt{
		heading:       "First-run setup",
		subheading:    "Create the first administrator. This runs once; afterwards manage users in the UI.",
		emailLabel:    "Admin email",
		passwordLabel: "Admin password",
	}
	if err := a.collectCredentials(prompt, opts.yes, &opts.email, &opts.password); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
			return nil
		}
		return err
	}

	if opts.email == "" {
		return errors.New("admin email is required (use --email or run interactively)")
	}
	if len(opts.password) < minPasswordLen {
		return fmt.Errorf("admin password must be at least %d characters", minPasswordLen)
	}

	fmt.Fprintln(a.Out, theme.Infof("Creating the first admin account ..."))

	if dockerInstall {
		if err := install.ComposeCreateAdmin(a.Out, composePath, opts.email, opts.password); err != nil {
			return fmt.Errorf("create admin (docker): %w", err)
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
		if err := install.VenvCreateAdmin(a.Out, py, configPath, opts.email, opts.password); err != nil {
			return fmt.Errorf("create admin (local): %w", err)
		}
	}

	fmt.Fprintln(a.Out, theme.Successf("Admin account created for %s.", opts.email))
	fmt.Fprintln(a.Out, theme.Dim.Render("  Sign in at the admin UI with the email and password you just set."))
	return nil
}
