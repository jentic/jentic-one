package ctlcmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

const minPasswordLen = 12

type adminFlowOptions struct {
	config   string
	email    string
	password string
	yes      bool
}

type adminFlow struct {
	cmdName   string
	prompt    credentialPrompt
	progress  string
	success   string
	hint      string
	docker    func(w io.Writer, composePath, email, password string) error
	venv      func(w io.Writer, venvPython, configPath, email, password string) error
	errPrefix string
}

func bindAdminFlowFlags(cmd *cobra.Command, opts *adminFlowOptions, emailNoun, passwordNoun string) {
	cmd.Flags().StringVar(&opts.config, "config", "",
		"path to the generated config (default: ~/.jentic/jentic-one.yaml)")
	cmd.Flags().StringVar(&opts.email, "email", "", emailNoun+" (prompted if omitted)")
	cmd.Flags().StringVar(&opts.password, "password", "",
		passwordNoun+" (prompted if omitted; prefer the prompt to keep it out of shell history)")
	cmd.Flags().BoolVarP(&opts.yes, "yes", "y", false, "skip prompts; require --email and --password")
}

func (a *app) runAdminFlow(ctx context.Context, opts *adminFlowOptions, f adminFlow) error {
	// A generated compose file marks a Docker install: the command runs in a
	// one-shot app container. Probe the daemon up front — before prompting for
	// credentials — so a user on a stopped daemon isn't asked to type an email
	// and password only to be told the daemon is down afterwards. The probe may
	// poll (~30s) for a cold-starting daemon, so announce it (see start.go).
	composePath := a.Paths.ComposePath()
	dockerInstall := proc.FileExists(composePath)
	if dockerInstall {
		announceDaemonCheck(a.Out)
		if err := requireDockerDaemon(ctx, f.cmdName); err != nil {
			return err
		}
	}

	if err := a.collectCredentials(f.prompt, opts.yes, &opts.email, &opts.password); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
			return nil
		}
		return err
	}

	if opts.email == "" {
		return fmt.Errorf("%s is required (use --email or run interactively)", strings.ToLower(f.prompt.emailLabel))
	}
	if len(opts.password) < minPasswordLen {
		return fmt.Errorf("%s must be at least %d characters", strings.ToLower(f.prompt.passwordLabel), minPasswordLen)
	}

	if strings.Contains(f.progress, "%s") {
		fmt.Fprintln(a.Out, theme.Infof(f.progress, opts.email))
	} else {
		fmt.Fprintln(a.Out, theme.Infof("%s", f.progress))
	}

	if dockerInstall {
		if err := f.docker(a.Out, composePath, opts.email, opts.password); err != nil {
			return fmt.Errorf("%s (docker): %w", f.errPrefix, err)
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
		if err := f.venv(a.Out, py, configPath, opts.email, opts.password); err != nil {
			return fmt.Errorf("%s (local): %w", f.errPrefix, err)
		}
	}

	fmt.Fprintln(a.Out, theme.Successf(f.success, opts.email))
	fmt.Fprintln(a.Out, theme.Dim.Render(f.hint))
	return nil
}
