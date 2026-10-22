package localagentcmd

// run_container_provider.go is --container-share-provider-credentials: the
// operator hands a container session their own LLM-provider login, for
// testing. It is off by default because it breaks container mode's rule that
// no credential of the operator's enters the agent: the agent can use the
// shared identity directly, outside Jentic.

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/localagent/container"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// shareProviderFlag is the flag's name; like every container-only flag it is
// --container-* and refused without --container.
const shareProviderFlag = "container-share-provider-credentials"

// confirmProviderShare asks the operator to confirm the share. Tests replace
// it.
var confirmProviderShare = func() (bool, error) {
	confirm := false
	err := prompt.RunConfirm(
		huh.NewConfirm().
			Title("Share your LLM-provider login with the agent container?").
			Description("The agent can use this identity directly, bypassing Jentic. For local testing only.").
			Value(&confirm),
	)
	return confirm, err
}

// providerShareInputs are what prepareProviderShare reads from the host; the
// command fills them from the real environment, tests from fixtures.
type providerShareInputs struct {
	home        string
	environ     []string
	yes         bool
	interactive bool
}

// prepareProviderShare builds the session's provider share, prints the
// warning and asks for confirmation (skipped with --yes). It returns nil when
// the flag is off. Without --yes and without a terminal to ask on it refuses
// (INTERACTIVE_CONFIRM_BLOCKED); a declined prompt aborts the run.
func (a *Cmd) prepareProviderShare(cmd *cobra.Command, opts *runOptions) (*container.ProviderShare, error) {
	if !opts.container.shareProvider {
		return nil, nil
	}
	return a.prepareProviderShareFrom(providerShareInputs{
		home:        localagent.OperatorHome(),
		environ:     os.Environ(),
		yes:         opts.yes,
		interactive: cmdcore.WantsInteractive(cmd, opts.yes),
	})
}

func (a *Cmd) prepareProviderShareFrom(in providerShareInputs) (*container.ProviderShare, error) {
	share, err := buildProviderShare(in.home, in.environ)
	if err != nil {
		return nil, fmt.Errorf("--%s: %w", shareProviderFlag, err)
	}
	a.warnProviderShare(share)
	switch {
	case in.yes:
	case !in.interactive:
		return nil, &ux.CodedError{
			Code:       ux.CodeConfirmBlocked,
			Msg:        "--" + shareProviderFlag + " needs confirmation",
			Actionable: "Re-run with --yes to share your provider login with the agent container, or drop --" + shareProviderFlag + ".",
		}
	default:
		ok, err := confirmProviderShare()
		if err != nil {
			return nil, err
		}
		if !ok {
			return nil, fmt.Errorf("not sharing your provider login: run without --%s", shareProviderFlag)
		}
	}
	return &share, nil
}

// buildProviderShare reads the operator's provider selection from
// ~/.claude/settings.json and the environment, and resolves the credential
// paths it shares. A path that is present but would escape (a symlink out of
// the home or into another sensitive dir) refuses the share; one that is
// absent is simply not mounted.
func buildProviderShare(home string, environ []string) (container.ProviderShare, error) {
	settingsEnv, err := localagent.ClaudeSettingsEnv(home)
	if err != nil {
		return container.ProviderShare{}, err
	}
	provider, env, googleCredentials := container.ProviderEnv(settingsEnv, environ)
	var src container.ProviderSources
	resolve := func(p string, wantDir bool, allowed string) (string, error) {
		if _, err := os.Lstat(p); os.IsNotExist(err) {
			return "", nil
		}
		resolved, err := localagent.ResolveShareablePath(home, p, wantDir, allowed)
		if err != nil {
			return "", fmt.Errorf("refusing to share %s: %w", p, err)
		}
		return resolved, nil
	}
	switch provider {
	case container.ProviderBedrock:
		if src.AWSDir, err = resolve(filepath.Join(home, ".aws"), true, ".aws"); err != nil {
			return container.ProviderShare{}, err
		}
	case container.ProviderVertex:
		if src.GCloudDir, err = resolve(filepath.Join(home, ".config", "gcloud"), true, ".config/gcloud"); err != nil {
			return container.ProviderShare{}, err
		}
		if googleCredentials != "" {
			// Named explicitly, so it must exist: a missing file is an error,
			// not a silent skip.
			if src.GoogleCredentials, err = localagent.ResolveShareablePath(home, googleCredentials, false, ".config/gcloud"); err != nil {
				return container.ProviderShare{}, fmt.Errorf("refusing to share GOOGLE_APPLICATION_CREDENTIALS %s: %w", googleCredentials, err)
			}
		}
	}
	return container.NewProviderShare(provider, env, src)
}

// warnProviderShare prints, once, what the share hands the agent: the
// mounts and the variable names (never their values).
func (a *Cmd) warnProviderShare(share container.ProviderShare) {
	w := a.Err
	fmt.Fprintln(w, theme.Warnf("WARNING: --%s hands your own %s login to the agent container.", shareProviderFlag, providerLabel(share.Provider)))
	fmt.Fprintln(w, "  The agent can read these credentials and use that identity directly, bypassing Jentic:")
	fmt.Fprintln(w, "  its LLM calls are not governed, recorded or limited by Jentic. Use it for local testing only.")
	if len(share.Mounts) == 0 {
		fmt.Fprintln(w, "  Mounted: nothing (no provider credential files found).")
	}
	for _, m := range share.Mounts {
		fmt.Fprintf(w, "  Mounted read-only: %s -> %s\n", m.Source, m.Target)
	}
	if names := share.EnvNames(); len(names) > 0 {
		fmt.Fprintf(w, "  Passed in ~/.claude/settings.json env: %s\n", strings.Join(names, ", "))
	} else {
		fmt.Fprintln(w, "  Passed in ~/.claude/settings.json env: nothing.")
	}
	if share.Provider == container.ProviderBedrock {
		fmt.Fprintln(w, "  Every profile and key in ~/.aws is visible to the agent. awsAuthRefresh is not run in the container:")
		fmt.Fprintln(w, "  if your SSO session lapses, run `aws sso login` on this machine; the mounted cache picks it up.")
	}
}

func providerLabel(provider string) string {
	switch provider {
	case container.ProviderBedrock:
		return "AWS Bedrock"
	case container.ProviderVertex:
		return "Google Vertex AI"
	default:
		return "Anthropic API"
	}
}

// providerShareGetenv is the getenv the proxy's tunnel allowlist reads when
// the operator shares their provider login: the shared env, which is what the
// agent's Claude Code sees.
func providerShareGetenv(share *container.ProviderShare) func(string) string {
	if share == nil {
		return os.Getenv
	}
	return func(k string) string { return share.Env[k] }
}
