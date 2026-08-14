package cmdcore

import (
	"context"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/charmbracelet/x/term"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/spf13/cobra"
)

type registerOptions struct {
	url         string // install URL (fresh-machine setup arm)
	env         string // environment name override (default: derived from --url)
	name        string
	timeout     time.Duration
	force       bool
	yes         bool
	interactive bool
}

// NewRegisterCmd builds the `register` command — the single onboarding front
// door (register.go): with an active context it registers that context's
// identity; on a fresh machine --url (or the interactive prompt) creates the
// environment + identity + context trio, activates it, and registers. Shared
// by both trees via cmdcore.
func NewRegisterCmd(app *App) *cobra.Command {
	opts := &registerOptions{}

	cmd := &cobra.Command{
		Use:   "register",
		Short: "Obtain tokens for an existing agent identity (identity only, no skills) — agents run this",
		Long: "register connects this machine to a Jentic install: it generates an\n" +
			"Ed25519 keypair (if absent), performs Dynamic Client Registration, waits\n" +
			"for an operator to approve the agent, then mints and saves tokens.\n\n" +
			"With an active context (`jentic context use`), it registers that context's\n" +
			"identity with its environment. On a fresh machine, pass --url (or answer\n" +
			"the prompt) and register creates the environment, identity and context in\n" +
			"one step, activates them, and registers.\n\n" +
			"Local install: use http://127.0.0.1:8000 (NOT http://localhost:8000) — the\n" +
			"token-exchange audience is matched exactly against the backend's\n" +
			"canonical_base_url, and a local backend uses 127.0.0.1. For a loopback URL,\n" +
			"register also seeds the environment's broker_url (http://127.0.0.1:8100) so\n" +
			"`jentic execute` works without extra flags.",
		Example: "  jentic register --url http://127.0.0.1:8000   # local install (use 127.0.0.1, not localhost)\n" +
			"  jentic register --url https://jentic.example.com\n" +
			"  jentic register --url https://jentic.example.com --name crawler --env prod\n" +
			"  jentic register                      # active context (or interactive setup)\n" +
			"  jentic register --force              # re-register the active identity",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts.interactive = WantsInteractive(cmd, opts.yes, registerFieldFlags...)
			return app.registerE(cmd.Context(), opts)
		},
	}

	cmd.Flags().StringVar(&opts.url, "url", "", "Jentic install URL to connect to (creates environment/identity/context on first run)")
	cmd.Flags().StringVar(&opts.env, "env", "", "environment name for --url (default: derived from the URL host)")
	cmd.Flags().StringVar(&opts.name, "name", "", "agent name shown to the approving operator (default: hostname)")
	cmd.Flags().DurationVar(&opts.timeout, "timeout", 5*time.Minute, "how long to wait for approval")
	cmd.Flags().BoolVar(&opts.force, "force", false, "re-register even if this identity already has a registration")
	cmd.Flags().BoolVarP(&opts.yes, "yes", "y", false, "skip the interactive prompt; use flags + defaults")

	return cmd
}

// flagsAllowPrompt is the pure policy: prompt unless --yes or any of the given
// "field" flags is set. Each command passes the flags that, when present, signal
// a deliberate non-interactive invocation.
func flagsAllowPrompt(cmd *cobra.Command, yes bool, fieldFlags ...string) bool {
	if yes {
		return false
	}
	for _, f := range fieldFlags {
		if cmd.Flags().Changed(f) {
			return false
		}
	}
	return true
}

// registerFieldFlags are the flags whose presence makes `register` non-interactive.
var registerFieldFlags = []string{"url", "env", "name"}

// BootstrapFieldFlags extend the register set with the skill-target and
// activation flags bootstrap adds, so a flag-driven run (e.g. `--operator
// claude`) is not treated as interactive. Exported because bootstrap now lives
// in the localagentcmd package (ARCH-1) and needs this set.
var BootstrapFieldFlags = append(append([]string{}, registerFieldFlags...),
	"operator", "all", "scope", "skip-skill")

// WantsInteractive also requires a real terminal (so pipes/CI stay non-interactive).
func WantsInteractive(cmd *cobra.Command, yes bool, fieldFlags ...string) bool {
	return flagsAllowPrompt(cmd, yes, fieldFlags...) && term.IsTerminal(os.Stdin.Fd())
}

// registerProgress writes a human-facing progress line to stdout ONLY in a
// human session. `register` is deliberately UNFENCED (agents run it as the
// onboarding front door — api/root.go), so in agent/service-account mode these
// banners would otherwise land on stdout and corrupt the single JSON envelope an
// agent parses (AGT-21). In machine mode the lines are suppressed here; the
// terminal outcome is emitted once as a ux.Result via the Audience. Diagnostics
// that must survive machine mode (e.g. the localhost-normalisation note) go to
// a.Err, not through this helper.
func (a *App) registerProgress(ctx context.Context, line string) {
	if isMachineCtx(ctx) {
		return
	}
	fmt.Fprintln(a.Out, line)
}

// isMachineCtx reports whether the resolved mode is a fenced machine mode
// (agent/service-account) — the same test JSONOrPretty uses, but keyed off the
// context so the register body can consult it without a *cobra.Command. A
// missing state (register invoked outside the interceptor) fails OPEN to human
// prose, matching the pre-AGT-21 behavior for non-agent callers. It delegates to
// the canonical clictx.ActiveState.IsMachine so there is one machine-mode rule.
func isMachineCtx(ctx context.Context) bool {
	if st := clictx.FromContext(ctx); st != nil {
		return st.IsMachine()
	}
	return false
}

func (a *App) registerE(ctx context.Context, opts *registerOptions) error {
	// An explicit --url always means "connect me to THIS install": it takes the
	// setup arm even when some other context is active, creating/reusing the
	// matching environment and switching to it — never silently registering
	// with whatever happened to be active.
	if opts.url == "" {
		if st := clictx.ActiveContext(ctx); st != nil {
			return a.RegisterActive(ctx, st, opts.name, opts.timeout, opts.force)
		}
	}
	vals := SetupValues{URL: opts.url, Env: opts.env, Name: opts.name}
	_, err := a.RegisterSetup(ctx, vals, opts.timeout, opts.force, opts.interactive)
	if errors.Is(err, ErrOnboardCancelled) {
		return nil
	}
	return err
}
