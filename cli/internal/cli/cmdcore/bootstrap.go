package cmdcore

import (
	"context"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// bootstrapOptions collects every knob for the zero-to-playing flow. It is a
// superset of register + skill options because bootstrap orchestrates both.
type bootstrapOptions struct {
	url       string // install URL (fresh-machine setup arm)
	env       string // environment name override
	name      string
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

// skillOptions projects the skill-related bootstrap flags onto the shared
// skillOptions type so bootstrap reuses the exact skill selection and writing
// code. bootstrap's --force is deliberately *not* forwarded: it means
// "re-register the agent", and must not silently clobber a managed skill block
// the user hand-edited. Refreshing an edited block stays an explicit, separate
// `jentic skill init --force`.
func (o *bootstrapOptions) skillOptions() *skillOptions {
	return &skillOptions{
		operators: o.operators,
		all:       o.all,
		scope:     o.scope,
		yes:       o.yes,
	}
}

// BootstrapForWizard runs the shared bootstrap flow (register + approval wait +
// skill) from the ctl `wizard` command. It keeps bootstrapOptions private to
// cmdcore while letting the ctl tree drive it with just the values the wizard
// owns: the install URL the wizard just brought up, plus the operator picks.
// operators empty + yes=false runs bootstrap's interactive operator picker; a
// named operator (yes=true) drives it non-interactively; empty operators +
// yes=true auto-detects. interactive stays false so bootstrap does not
// re-prompt for the URL the wizard already collected.
func (a *App) BootstrapForWizard(ctx context.Context, installURL string, timeout time.Duration, operators []string, yes bool) error {
	return a.bootstrapE(ctx, &bootstrapOptions{
		url:       installURL,
		timeout:   timeout,
		operators: operators,
		yes:       yes,
	})
}

// NewBootstrapCmd builds the `bootstrap` command that runs first-time agent
// setup (register + skill install) in one step. Shared by both trees via cmdcore.
func NewBootstrapCmd(app *App) *cobra.Command {
	opts := &bootstrapOptions{}

	cmd := &cobra.Command{
		Use:   "bootstrap",
		Short: "Register an agent, wait for approval, and prime your operator — in one step",
		Long: "bootstrap takes a fresh machine from nothing to ready: it connects this\n" +
			"machine to a Jentic install (creating the environment, identity and context\n" +
			"if needed), registers the agent (Dynamic Client Registration), prints an\n" +
			"approval link and waits for a human to approve it, mints and saves tokens,\n" +
			"and generates the Jentic CLI-usage skill into your agent runtime's native\n" +
			"layout. It also offers to isolate the coding agent behind its own Unix user.\n\n" +
			"It is a thin orchestration of `jentic register` and `jentic skill`: nothing\n" +
			"here you can't do by hand, just sequenced so you can start playing right\n" +
			"away. Re-running refreshes everything idempotently.",
		Example: "  jentic bootstrap\n" +
			"  jentic bootstrap --url https://jentic.example.com --operator claude --yes\n" +
			"  jentic bootstrap --skip-skill   # identity only\n" +
			"  jentic bootstrap --dry-run",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts.interactive = WantsInteractive(cmd, opts.yes, bootstrapFieldFlags...)
			return app.bootstrapE(cmd.Context(), opts)
		},
	}

	cmd.Flags().StringVar(&opts.url, "url", "", "Jentic install URL to connect to (creates environment/identity/context on first run)")
	cmd.Flags().StringVar(&opts.env, "env", "", "environment name for --url (default: derived from the URL host)")
	cmd.Flags().StringVar(&opts.name, "name", "", "agent client name shown to the approver")
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

func (a *App) bootstrapE(ctx context.Context, opts *bootstrapOptions) error {
	fmt.Fprintln(a.Out, theme.Headingf("Bootstrap"))
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
	st := clictx.ActiveV2(ctx)
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
			// Esc in the skill picker means "never mind", not a failed bootstrap.
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
				return nil
			}
			// For bootstrap the natural escape hatch on a headless box with
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
		return a.bootstrapDryRun(st, targets, env, opts)
	}

	// After the operator is chosen, offer to isolate it behind a dedicated Unix
	// user (the true credential boundary). This is asked BEFORE any registration
	// side effect and BEFORE any sudo, so declining costs nothing and leaves no
	// half-provisioned state. It is shared with `jenticctl wizard`, which reaches
	// it through this same bootstrap flow. The identity itself stays in the
	// operator's XDG store; `jentic run` exports the active context's material
	// into the agent's home at launch.
	var setup agentSetup
	if !opts.skipSkill {
		// Interactivity for the sudo gate matches the skill picker's own rule
		// (!yes && a real TTY), not opts.interactive — the wizard deliberately
		// leaves opts.interactive false (it owns the URL prompt) while the
		// user is very much at a terminal.
		agentInteractive := !opts.yes && term.IsTerminal(os.Stdin.Fd())
		s, err := a.setupAgentUser(ctx, operatorNames(targetAdapters(targets)), agentInteractive)
		if err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("Agent-user setup cancelled."))
			} else {
				// Isolation is best-effort: a failure here must not block the
				// identity/skill provisioning the operator came for.
				fmt.Fprintln(a.Out, theme.Warnf("agent-user setup skipped: %v", err))
			}
		}
		setup = s
	}

	// Step 1+2: register and wait for approval via the shared flow.
	var installURL string
	identity := opts.name
	if st != nil {
		installURL = st.BaseURL
		if identity == "" {
			identity = st.IdentityName
		}
		if err := a.registerV2Active(ctx, st, opts.name, opts.timeout, opts.force); err != nil {
			return err
		}
	} else {
		vals, err := a.registerV2Setup(ctx,
			v2SetupValues{url: opts.url, env: opts.env, name: opts.name},
			opts.timeout, opts.force, opts.interactive)
		if errors.Is(err, errOnboardCancelled) {
			return nil
		}
		if err != nil {
			return err
		}
		installURL, identity = vals.url, vals.name
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
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Success.Render("You're ready."))
	fmt.Fprintln(a.Out, theme.Field("identity", identity))
	if installURL != "" {
		fmt.Fprintln(a.Out, theme.Field("install", installURL))
	}
	fmt.Fprintf(a.Out, "\n%s %s\n", theme.Dim.Render("Try:"), theme.Command.Render("jentic catalog"))

	// If we created a dedicated agent account, offer to start a session in the
	// agent's home right now — the operator has just seen the summary and the
	// natural next step is `cd <home>; jentic run <agent>`. Accepting runs it;
	// declining leaves the printed command for later. Only when interactive and
	// a real account exists.
	agentInteractive := !opts.yes && term.IsTerminal(os.Stdin.Fd())
	if setup.created && setup.agentUser != "" && agentInteractive {
		if err := a.offerAgentSession(ctx, setup); err != nil && !errors.Is(err, huh.ErrUserAborted) {
			return err
		}
	}
	return nil
}

// bootstrapDryRun describes the steps without registering or writing. st is
// the active context (nil for the fresh-machine setup arm).
func (a *App) bootstrapDryRun(st *clictx.ActiveState, targets []skillTarget, env skillgen.DetectEnv, opts *bootstrapOptions) error {
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
	return a.writeSkill(targets, env, dry)
}

func valueOrPlaceholder(s, placeholder string) string {
	if s == "" {
		return placeholder
	}
	return s
}

// offerAgentSession asks whether to start a session in the freshly-isolated
// agent's home and, on yes, launches it (equivalent to `cd <home>; jentic run
// <agent>`). The launch runs the agent under its own user in a login shell, so
// the operator lands straight in the isolated session. Declining is a no-op — the
// copy-paste command was already printed by the agent-user setup step.
func (a *App) offerAgentSession(ctx context.Context, setup agentSetup) error {
	homeDir := setup.homeDir
	launch := fmt.Sprintf("cd %s; jentic run %s", homeDir, setup.agentID)

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("Start a session in the agent's home now? This runs:"))
	fmt.Fprintf(a.Out, "    %s\n", theme.Command.Render(launch))

	start := true
	if err := prompt.RunConfirm(huh.NewConfirm().
		Title("Start a session in the agent's home now?").
		Description("Launches the agent as its own user, in " + homeDir + ".").
		Affirmative("Yes, start it").
		Negative("Not now").
		Value(&start)); err != nil {
		return err
	}
	if !start {
		fmt.Fprintln(a.Out, theme.Dim.Render("Not started. Run the command above whenever you're ready."))
		return nil
	}

	// Launch in the agent's own home (dir "" → login shell starts in $HOME).
	// No working-dir grant is involved here, but the launch still loads the
	// recorded grants to build the confinement profile and hands the active
	// context's credentials to the agent's own store first.
	desc, _ := localagent.Lookup(setup.agentID)
	return a.launchIsolated(ctx, setup.agentUser, desc.Binary, "", nil)
}
