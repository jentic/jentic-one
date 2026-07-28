package cmd

import (
	"context"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/agentauth"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// bootstrapOptions collects every knob for the zero-to-playing flow. It is a
// superset of register + skill options because bootstrap orchestrates both.
type bootstrapOptions struct {
	profile   string
	baseURL   string
	name      string
	timeout   time.Duration
	force     bool
	yes       bool
	noActive  bool
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
		baseURL:   o.baseURL,
		yes:       o.yes,
	}
}

func newBootstrapCmd(app *App) *cobra.Command {
	opts := &bootstrapOptions{}

	cmd := &cobra.Command{
		Use:   "bootstrap",
		Short: "Register an agent, wait for approval, and prime your operator — in one step",
		Long: "bootstrap takes a fresh machine from nothing to ready: it registers this\n" +
			"profile as an agent (Dynamic Client Registration), prints an approval link\n" +
			"and waits for a human to approve it, mints and saves tokens, sets the\n" +
			"profile as the default, and generates the Jentic CLI-usage skill into your\n" +
			"agent runtime's native layout.\n\n" +
			"It is a thin orchestration of `jentic register` and `jentic skill`: nothing\n" +
			"here you can't do by hand, just sequenced so you can start playing right\n" +
			"away. Re-running refreshes everything idempotently.",
		Example: "  jentic bootstrap\n" +
			"  jentic bootstrap --operator claude --yes\n" +
			"  jentic bootstrap --profile demo --base-url http://localhost:9000 --all --yes\n" +
			"  jentic bootstrap --skip-skill   # identity only\n" +
			"  jentic bootstrap --dry-run",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts.interactive = wantsInteractive(cmd, opts.yes, bootstrapFieldFlags...)
			return app.bootstrapE(cmd.Context(), opts)
		},
	}

	cmd.Flags().StringVar(&opts.profile, "profile", "", "profile to create/use (default: config default_profile)")
	cmd.Flags().StringVar(&opts.baseURL, "base-url", "", "Jentic control-plane base URL")
	cmd.Flags().StringVar(&opts.name, "name", "", "agent client name shown to the approver")
	cmd.Flags().DurationVar(&opts.timeout, "timeout", 5*time.Minute, "how long to wait for approval")
	cmd.Flags().BoolVar(&opts.force, "force", false, "re-register the agent even if the profile already has one (does not overwrite an edited skill block)")
	cmd.Flags().BoolVar(&opts.noActive, "no-activate", false, "do not set this profile as the default")
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

	if opts.interactive {
		if err := a.promptBootstrap(opts); err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
				return nil
			}
			return err
		}
	}

	profileName, baseURL, err := a.resolveIdentity(opts.profile, opts.baseURL)
	if err != nil {
		return err
	}

	// Flag validation happens before anything else, even for paths a flag
	// doesn't apply to: `--skip-skill --scope typo` should error, not be
	// silently ignored (a typo the user meant to matter must never pass).
	if _, err := resolveScope(opts.scope); err != nil {
		return err
	}

	// Resolve the skill targets (operators + placement scope) up front, before
	// any registration or activation. Identity provisioning has irreversible
	// side effects (a registered agent, an activated profile); a selection
	// error (e.g. no operators resolvable on a non-interactive shell) must
	// surface here so we never half-complete the flow and then fail at the
	// skill step.
	var (
		targets []skillTarget
		env     skillgen.DetectEnv
	)
	if !opts.skipSkill {
		reg := skillgen.DefaultRegistry()
		env, err = a.detectEnv()
		if err != nil {
			return err
		}
		targets, err = a.chooseTargets(reg, env, opts.skillOptions())
		if err != nil {
			// Same cancel idiom as promptBootstrap above: Esc in the skill
			// picker means "never mind", not a failed bootstrap.
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
		return a.bootstrapDryRun(profileName, baseURL, targets, env, opts)
	}

	// After the operator is chosen, offer to isolate it behind a dedicated Unix
	// user (the true credential boundary). This is asked BEFORE any registration
	// side effect and BEFORE any sudo, so declining costs nothing and leaves no
	// half-provisioned state. It is shared with `jenticctl wizard`, which reaches
	// it through this same bootstrap flow.
	if !opts.skipSkill {
		// Interactivity for the sudo gate matches the skill picker's own rule
		// (!yes && a real TTY), not opts.interactive — the wizard deliberately
		// leaves opts.interactive false (it owns the profile prompts) while the
		// user is very much at a terminal.
		agentInteractive := !opts.yes && term.IsTerminal(os.Stdin.Fd())
		if err := a.setupAgentUser(ctx, operatorNames(adapters), agentInteractive); err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("Agent-user setup cancelled."))
			} else {
				// Isolation is best-effort: a failure here must not block the
				// identity/skill provisioning the operator came for.
				fmt.Fprintln(a.Out, theme.Warnf("agent-user setup skipped: %v", err))
			}
		}
	}

	// Step 1+2: register (DCR) and wait for human approval, reusing the exact
	// register plumbing so behaviour stays identical.
	tokens, err := a.bootstrapIdentity(ctx, profileName, baseURL, opts)
	if err != nil {
		return err
	}

	// Step 3: make this the active profile so bare `jentic` commands use it.
	if !opts.noActive {
		if err := config.SetDefaultProfile(a.Paths, profileName); err != nil {
			return fmt.Errorf("set default profile: %w", err)
		}
		fmt.Fprintln(a.Out, theme.Successf("Active profile set to %q", profileName))
	}

	// Step 4: write the skill into the operator's native layout, reusing the
	// shared skill-writing body. A user-edited managed block is reported but
	// not fatal: the identity is already provisioned.
	if !opts.skipSkill {
		fmt.Fprintln(a.Out)
		if err := a.writeSkill(targets, env, opts.skillOptions()); err != nil {
			// Identity is already provisioned, so a skill-content failure is
			// reported but not fatal — the agent can re-run `jentic skill init`.
			fmt.Fprintln(a.Out, theme.Warnf("skill generation failed: %v", err))
		}
	}

	a.bootstrapSummary(profileName, tokens)
	return nil
}

// bootstrapIdentity registers the agent if needed and resolves a token pair. It
// mints once first: an already-approved agent returns tokens immediately, so we
// skip the approval banner and wait loop entirely. Only when the first mint is
// pending do we print the approval link and poll until approval (or timeout /
// Ctrl-C).
func (a *App) bootstrapIdentity(ctx context.Context, profileName, baseURL string, opts *bootstrapOptions) (*tokensView, error) {
	sess, err := agentauth.Open(a.Paths, profileName, baseURL)
	if err != nil {
		return nil, err
	}
	if opts.force {
		sess.ResetRegistration()
	}

	if err := a.ensureRegistered(ctx, sess, profileName, opts.name); err != nil {
		return nil, err
	}

	return waitForApproval(ctx, a.Out, sess, opts.timeout, bootstrapResumeHint)
}

// bootstrapDryRun describes the steps without registering or writing anything.
func (a *App) bootstrapDryRun(profileName, baseURL string, targets []skillTarget, env skillgen.DetectEnv, opts *bootstrapOptions) error {
	fmt.Fprintln(a.Out, theme.Infof("would register agent for profile %q at %s (or reuse an existing registration)", profileName, baseURL))
	fmt.Fprintln(a.Out, theme.Infof("would wait up to %s for human approval if the agent is still pending, then mint tokens", opts.timeout))
	if !opts.noActive {
		fmt.Fprintln(a.Out, theme.Infof("would set %q as the default profile", profileName))
	}
	if opts.skipSkill {
		fmt.Fprintln(a.Out, theme.Dim.Render("would skip skill generation (--skip-skill)"))
	} else {
		fmt.Fprintln(a.Out)
		dry := opts.skillOptions()
		dry.dryRun = true
		if err := a.writeSkill(targets, env, dry); err != nil {
			return err
		}
	}
	return nil
}

func (a *App) bootstrapSummary(profileName string, tokens *tokensView) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Success.Render("You're ready."))
	fmt.Fprintln(a.Out, theme.Field("profile", profileName))
	if tokens != nil {
		fmt.Fprintln(a.Out, theme.Field("access", shorten(tokens.AccessToken)))
		if !tokens.AccessExpiresAt.IsZero() {
			fmt.Fprintln(a.Out, theme.Field("expires", tokens.AccessExpiresAt.Format(time.RFC3339)))
		}
	}
	fmt.Fprintf(a.Out, "\n%s %s\n", theme.Dim.Render("Try:"), theme.Command.Render(fmt.Sprintf("jentic execute --profile %s <operation>", profileName)))
}

// promptBootstrap collects the onboarding values interactively, reusing the
// register wizard fields. Skill targets are picked later by skillInit's own
// interactive picker, so they are not prompted here.
func (a *App) promptBootstrap(opts *bootstrapOptions) error {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return err
	}
	profileName := cfg.ResolvedProfileName(opts.profile)
	baseURL := cfg.ResolvedBaseURLOr(opts.baseURL)
	name := opts.name
	if name == "" {
		name = "jentic-cli-" + profileName
	}
	if err := promptOnboarding(&profileName, &baseURL, &name); err != nil {
		return err
	}
	opts.profile, opts.baseURL, opts.name = profileName, baseURL, name
	return nil
}
