package localagentcmd

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// setupOptions collects every knob for the zero-to-playing flow. It is a
// superset of register + skill options because setup orchestrates both.
type setupOptions struct {
	url       string // install URL (fresh-machine setup arm)
	env       string // environment name override
	name      string
	brokerURL string // explicit broker (data plane) URL for the environment
	timeout   time.Duration
	force     bool
	yes       bool
	skipSkill bool

	// skill placement
	operators []string
	all       bool
	scope     string
	dryRun    bool

	interactive bool
}

// skillOptions projects the skill-related setup flags onto the shared
// skillOptions type so setup reuses the exact skill selection and writing
// code. setup's --force is deliberately *not* forwarded: it means
// "re-register the agent", and must not silently clobber a managed skill block
// the user hand-edited. Refreshing an edited block stays an explicit, separate
// `jentic skill init --force`.
func (o *setupOptions) skillOptions() *skillOptions {
	return &skillOptions{
		operators: o.operators,
		all:       o.all,
		scope:     o.scope,
		yes:       o.yes,
	}
}

// SetupForWizard runs the shared setup flow (register + approval wait +
// skill) from the ctl `wizard` command. It keeps setupOptions private to
// cmdcore while letting the ctl tree drive it with just the values the wizard
// owns: the install URL the wizard just brought up, plus the operator picks.
// operators empty + yes=false runs setup's interactive operator picker; a
// named operator (yes=true) drives it non-interactively; empty operators +
// yes=true auto-detects. interactive stays false so setup does not
// re-prompt for the URL the wizard already collected.
func (a *Cmd) SetupForWizard(ctx context.Context, installURL string, timeout time.Duration, operators []string, yes bool) error {
	return a.setupE(ctx, &setupOptions{
		url:       installURL,
		timeout:   timeout,
		operators: operators,
		yes:       yes,
	})
}

// NewSetupCmd builds the `setup` command that runs first-time agent
// setup (register + skill install) in one step. Shared by both trees via cmdcore.
func NewSetupCmd(app *cmdcore.App) *cobra.Command {
	a := &Cmd{App: app}
	opts := &setupOptions{}

	cmd := &cobra.Command{
		Use:   "setup",
		Short: "Set up a new local agent (identity + skills) — start here if you're a person",
		Long: "setup takes a fresh machine from nothing to ready: it connects this\n" +
			"machine to a Jentic install (creating the environment, identity and context\n" +
			"if needed), registers the agent (Dynamic Client Registration), prints an\n" +
			"approval link and waits for a human to approve it, mints and saves tokens,\n" +
			"and generates the Jentic CLI-usage skill into your agent runtime's native\n" +
			"layout, plus the `jentic mcp` server entry for each detected MCP runtime.\n\n" +
			"It is a thin orchestration of `jentic register` and `jentic skill`: nothing\n" +
			"here you can't do by hand, just sequenced so you can start playing right\n" +
			"away. Re-running refreshes everything idempotently.",
		Example: "  jentic setup\n" +
			"  jentic setup --url https://jentic.example.com --broker-url https://broker.jentic.example.com\n" +
			"  jentic setup --url https://jentic.example.com --operator claude --yes\n" +
			"  jentic setup --skip-skill   # identity only\n" +
			"  jentic setup --dry-run",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts.interactive = cmdcore.WantsInteractive(cmd, opts.yes, cmdcore.SetupFieldFlags...)
			return a.setupE(cmd.Context(), opts)
		},
	}

	cmd.Flags().StringVar(&opts.url, "url", "", "Jentic install URL to connect to (creates environment/identity/context on first run)")
	cmd.Flags().StringVar(&opts.env, "env", "", "environment name for --url (default: derived from the URL host)")
	cmd.Flags().StringVar(&opts.name, "name", "", "agent client name shown to the approver")
	cmd.Flags().StringVar(&opts.brokerURL, "broker-url", "", "broker (data plane) URL for the environment — execute needs it on a remote install; seeded automatically for loopback")
	cmd.Flags().DurationVar(&opts.timeout, "timeout", 5*time.Minute, "how long to wait for approval")
	cmd.Flags().BoolVar(&opts.force, "force", false, "re-register the agent even if the identity already has one (does not overwrite an edited skill block)")
	cmd.Flags().BoolVar(&opts.skipSkill, "skip-skill", false, "provision identity only; do not write skill files")
	cmd.Flags().StringSliceVar(&opts.operators, "operator", nil, "operators to target (repeatable or comma-separated)")
	cmd.Flags().BoolVar(&opts.all, "all", false, "target every supported operator")
	cmd.Flags().StringVar(&opts.scope, "scope", "", "skill placement scope: user or project (default: per-operator)")
	cmd.Flags().BoolVar(&opts.dryRun, "dry-run", false, "show what would happen without registering or writing")
	cmd.Flags().BoolVarP(&opts.yes, "yes", "y", false, "non-interactive: no prompts; with no --operator/--all, the skill targets the detected operators")

	return cmd
}

// NewSetupAliasCmd builds a hidden `bootstrap` command that is a pure
// compatibility alias for `setup` (the command's pre-rename name). It is a
// full second instance rather than a cobra alias so no help output, docs, or
// generated reference ever mentions the old name — it only works when typed.
func NewSetupAliasCmd(app *cmdcore.App) *cobra.Command {
	cmd := NewSetupCmd(app)
	cmd.Use = "bootstrap"
	cmd.Hidden = true
	return cmd
}

func (a *Cmd) setupE(ctx context.Context, opts *setupOptions) error {
	fmt.Fprintln(a.Out, theme.Headingf("Setup"))
	fmt.Fprintln(a.Out, theme.Dim.Render("Register this machine as an agent, wait for approval, then prime your operator."))

	// Flag validation happens before anything else, even for paths a flag
	// doesn't apply to: `--skip-skill --scope typo` should error, not be
	// silently ignored (a typo the user meant to matter must never pass).
	if _, err := resolveScope(opts.scope); err != nil {
		return err
	}

	// Which arm? An explicit --url always connects to THAT install; otherwise
	// an active context registers in place, and a fresh machine gets the
	// one-command setup (interactive prompt or MISSING_ARGUMENT).
	st := clictx.ActiveContext(ctx)
	if opts.url != "" {
		st = nil // --url pins the setup arm even when a context is active
	}

	// Resolve the skill targets (operators + placement scope) up front, before
	// any registration or activation. Identity provisioning has irreversible
	// side effects (a registered agent, an activated context); a selection
	// error (e.g. no operators resolvable on a non-interactive shell) must
	// surface here so we never half-complete the flow and then fail at the
	// skill step.
	var (
		targets []skillTarget
		env     skillgen.DetectEnv
		err     error
	)
	if !opts.skipSkill {
		reg := skillgen.DefaultRegistry()
		env, err = a.detectEnv()
		if err != nil {
			return err
		}
		targets, err = a.chooseTargets(reg, env, opts.skillOptions())
		if err != nil {
			// Esc in the skill picker means "never mind", not a failed setup.
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
				return nil
			}
			// For setup the natural escape hatch on a headless box with
			// no detectable operator is identity-only provisioning — name it.
			if errors.Is(err, errNothingDetected) {
				return fmt.Errorf("%w — or pass --skip-skill to provision identity only", err)
			}
			return err
		}
		if len(targets) == 0 {
			// Interactive picker dismissed with nothing selected: treat as a
			// no-skill run rather than registering for no reason.
			opts.skipSkill = true
		}
	}

	if opts.dryRun {
		return a.setupDryRun(ctx, st, targets, env, opts)
	}

	// Step 1+2: register and wait for approval via the shared flow.
	var installURL string
	identity := opts.name
	if st != nil {
		installURL = st.BaseURL
		if identity == "" {
			identity = st.IdentityName
		}
		if err := a.RegisterActive(ctx, st, opts.name, opts.brokerURL, opts.timeout, opts.force); err != nil {
			return err
		}
	} else {
		vals, err := a.RegisterSetup(ctx,
			cmdcore.SetupValues{URL: opts.url, Env: opts.env, Name: opts.name, BrokerURL: opts.brokerURL},
			opts.timeout, opts.force, opts.interactive)
		if errors.Is(err, cmdcore.ErrOnboardCancelled) {
			return nil
		}
		if err != nil {
			return err
		}
		installURL, identity = vals.URL, vals.Name
	}

	// Step 3: write the skill into the operator's native layout, templated
	// with the install URL the identity just registered with.
	if !opts.skipSkill {
		fmt.Fprintln(a.Out)
		so := opts.skillOptions()
		so.baseURL = installURL
		if err := a.writeSkill(targets, env, so); err != nil {
			// Identity is already provisioned, so a skill-content failure is
			// reported but not fatal — the agent can re-run `jentic skill init`.
			fmt.Fprintln(a.Out, theme.Warnf("skill generation failed: %v", err))
		}

		// Step 4: write the MCP server entry per detected runtime (2-E3) —
		// absolute stable binary path, --context pinned. Best-effort:
		// registration failures are reported per runtime.
		a.registerMCPEntries(ctx, targets, false)
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Success.Render("You're ready."))
	fmt.Fprintln(a.Out, theme.Field("identity", identity))
	if installURL != "" {
		fmt.Fprintln(a.Out, theme.Field("install", installURL))
	}
	fmt.Fprintf(a.Out, "\n%s %s\n", theme.Dim.Render("Try:"), theme.Command.Render("jentic catalog"))

	return nil
}

// setupDryRun describes the steps without registering or writing. st is
// the active context (nil for the fresh-machine setup arm).
func (a *Cmd) setupDryRun(ctx context.Context, st *clictx.ActiveState, targets []skillTarget, env skillgen.DetectEnv, opts *setupOptions) error {
	if st != nil {
		fmt.Fprintln(a.Out, theme.Infof("would register identity %q with environment %q (%s), or reuse an existing registration",
			st.IdentityName, st.EnvironmentName, st.BaseURL))
	} else {
		fmt.Fprintln(a.Out, theme.Infof("would create environment/identity/context for %s and register (or reuse an existing registration)",
			valueOrPlaceholder(opts.url, "<prompted URL>")))
	}
	fmt.Fprintln(a.Out, theme.Infof("would wait up to %s for human approval if the agent is still pending, then mint a token", opts.timeout))
	if opts.skipSkill {
		fmt.Fprintln(a.Out, theme.Dim.Render("would skip skill generation (--skip-skill)"))
		return nil
	}
	fmt.Fprintln(a.Out)
	dry := opts.skillOptions()
	dry.dryRun = true
	if err := a.writeSkill(targets, env, dry); err != nil {
		return err
	}
	a.registerMCPEntries(ctx, targets, true)
	return nil
}

func valueOrPlaceholder(s, placeholder string) string {
	if s == "" {
		return placeholder
	}
	return s
}
