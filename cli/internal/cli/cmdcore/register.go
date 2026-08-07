package cmdcore

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/agentauth"
	"github.com/jentic/jentic-one/cli/internal/authclient"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/profile"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

type registerOptions struct {
	profile     string
	baseURL     string
	name        string
	timeout     time.Duration
	force       bool
	yes         bool
	interactive bool
}

// NewRegisterCmd builds the `register` command that provisions an agent
// identity with the control plane. Shared by both trees via cmdcore.
func NewRegisterCmd(app *App) *cobra.Command {
	opts := &registerOptions{}

	cmd := &cobra.Command{
		Use:   "register",
		Short: "Register this profile as an agent and obtain tokens",
		Long: "register generates an Ed25519 keypair (if absent), performs Dynamic\n" +
			"Client Registration, waits for an operator to approve the agent, then mints\n" +
			"and saves an access/refresh token pair to the profile. `jentic execute` uses\n" +
			"those tokens as the Authorization bearer.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			opts.interactive = WantsInteractive(cmd, opts.yes, registerFieldFlags...)
			return app.registerE(cmd.Context(), opts)
		},
	}

	cmd.Flags().StringVar(&opts.profile, "profile", "", "profile name (default: config default_profile)")
	cmd.Flags().StringVar(&opts.baseURL, "base-url", "", "Jentic control-plane base URL")
	cmd.Flags().StringVar(&opts.name, "name", "", "agent client name")
	cmd.Flags().DurationVar(&opts.timeout, "timeout", 5*time.Minute, "how long to wait for approval")
	cmd.Flags().BoolVar(&opts.force, "force", false, "re-register even if the profile already has an agent")
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
var registerFieldFlags = []string{"profile", "base-url", "name"}

// bootstrapFieldFlags extend the register set with the skill-target and
// activation flags bootstrap adds, so a flag-driven run (e.g. `--operator
// claude`) is not treated as interactive.
var bootstrapFieldFlags = append(append([]string{}, registerFieldFlags...),
	"operator", "all", "scope", "skip-skill", "no-activate")

// WantsInteractive also requires a real terminal (so pipes/CI stay non-interactive).
func WantsInteractive(cmd *cobra.Command, yes bool, fieldFlags ...string) bool {
	return flagsAllowPrompt(cmd, yes, fieldFlags...) && term.IsTerminal(os.Stdin.Fd())
}

func (a *App) registerE(ctx context.Context, opts *registerOptions) error {
	if opts.interactive {
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

		fmt.Fprintln(a.Out, theme.Headingf("Agent onboarding"))
		fmt.Fprintln(a.Out, theme.Dim.Render("Register this machine as an agent; an operator approves it, then tokens mint."))
		if err := promptOnboarding(&profileName, &baseURL, &name); err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
				return nil
			}
			return err
		}
		opts.profile, opts.baseURL, opts.name = profileName, baseURL, name
	}

	cfg, err := config.Load(a.Paths)
	if err != nil {
		return err
	}
	profileName, baseURL, err := a.ResolveIdentity(opts.profile, opts.baseURL)
	if err != nil {
		return err
	}

	// When a shared agent account exists, a new identity is provisioned into the
	// agent's own home (chowned + checked out) rather than the operator's ~/.jentic.
	// An identity that already exists operator-side is translated over first, so
	// enabling isolation carries an existing registration across instead of minting
	// a fresh agent_id that would need re-approval.
	target := a.resolveIdentityTarget(cfg)
	if _, err := a.translateOperatorProfile(target, profileName); err != nil {
		return err
	}

	sess, err := agentauth.Open(target.paths, profileName, baseURL)
	if err != nil {
		return err
	}

	if opts.force {
		sess.ResetRegistration()
	}

	if err := a.ensureRegistered(ctx, sess, profileName, opts.name); err != nil {
		return err
	}

	tokens, err := waitForApproval(ctx, a.Out, sess, opts.timeout, registerResumeHint)
	if err != nil {
		return err
	}

	// Check out the profile (agent-home default when agent-owned; register never
	// touches the operator's own default) and hand the agent its config.
	if err := a.checkOutProfile(target, profileName, false); err != nil {
		return err
	}
	a.handOffToAgent(target)

	fmt.Fprintln(a.Out, theme.Successf("Tokens saved to %s", sess.Profile.Dir()))
	// Preview only the short-lived access token as a "it worked" confirmation. The
	// refresh token is the long-lived credential — never echo it (not even a
	// truncated preview), since it lands in terminal scrollback, tmux buffers, and
	// CI logs. It is safe on disk in the profile (0600); that is the only place it
	// needs to be.
	fmt.Fprintln(a.Out, theme.Field("access", shorten(tokens.AccessToken)))
	if !tokens.AccessExpiresAt.IsZero() {
		fmt.Fprintln(a.Out, theme.Field("expires", tokens.AccessExpiresAt.Format(time.RFC3339)))
	}
	fmt.Fprintf(a.Out, "\n%s %s\n", theme.Dim.Render("Ready:"), theme.Command.Render(fmt.Sprintf("jentic execute --profile %s <operation>", profileName)))
	return nil
}

// promptOnboarding collects the three CLI-side onboarding values interactively,
// styled to match the install wizard. The server only honors the agent name on
// /register; profile and base URL are local setup.
func promptOnboarding(profileName, baseURL, name *string) error {
	return prompt.NewForm(
		huh.NewGroup(
			prompt.Input().Title("Profile").
				Description("Local identity slot the tokens are saved under.").
				Value(profileName).Validate(notEmptyField("profile")),
			prompt.Input().Title("Control-plane base URL").
				Description("Where this agent registers and mints tokens.").
				Value(baseURL).Validate(notEmptyField("base url")),
			prompt.Input().Title("Agent name").
				Description("Shown to the operator approving this agent.").
				Value(name).Validate(notEmptyField("name")),
		),
	).WithShowHelp(true).Run()
}

func notEmptyField(label string) func(string) error {
	return func(s string) error {
		if strings.TrimSpace(s) == "" {
			return fmt.Errorf("%s must not be empty", label)
		}
		return nil
	}
}

// resumeHint messages are shown while waiting for approval. Each points back at
// the command the user actually ran so the documented resume path is correct.
const (
	registerResumeHint  = "Waiting for approval (Ctrl-C to stop and resume later with `jentic register`)..."
	bootstrapResumeHint = "Waiting for approval (Ctrl-C to stop and resume later with `jentic bootstrap`)..."
)

// Poll cadence for the approval wait. Package-level so tests can shrink it to
// keep the pending-path cases near-instant instead of real wall-clock seconds.
// Exported so the ctl wizard (a separate package) reuses the exact cadence.
var (
	PollInitialDelay = 2 * time.Second
	PollMaxDelay     = 10 * time.Second
	PollDelayStep    = 1 * time.Second
)

// ensureRegistered makes sure sess has an agent_id, registering via DCR if the
// profile has none. The resolved name falls back to the stored name, then a
// profile-derived default. It is shared by `register` and `bootstrap` so both
// commands provision identity identically.
func (a *App) ensureRegistered(ctx context.Context, sess *agentauth.Session, profileName, name string) error {
	if name == "" {
		name = sess.Meta.AgentName
	}
	if name == "" {
		name = "jentic-cli-" + profileName
	}

	if sess.Meta.AgentID != "" {
		fmt.Fprintln(a.Out, theme.Infof("Using existing agent_id=%s (profile %q)", sess.Meta.AgentID, profileName))
		return nil
	}

	fmt.Fprintln(a.Out, theme.Infof("Registering agent %q with %s ...", name, sess.Meta.BaseURL))
	reg, err := sess.Client.Register(ctx, name, sess.Key.JWKS())
	if err != nil {
		return fmt.Errorf("register: %w", err)
	}
	sess.Meta.AgentID = reg.ClientID
	sess.Meta.AgentName = name
	sess.Meta.RegistrationAccessToken = reg.RegistrationAccessToken
	if err := sess.Profile.SaveMeta(sess.Meta); err != nil {
		return err
	}
	fmt.Fprintln(a.Out, theme.Successf("Registered: agent_id=%s status=%s", reg.ClientID, reg.Status))
	return nil
}

// waitForApproval resolves a token pair for an already-registered agent. It
// mints once up front: an already-approved agent yields tokens immediately, so
// no approval banner or polling is shown. Only when the first mint reports a
// pending agent do we print the approval endpoint and poll until approval, the
// timeout, or Ctrl-C. resumeHint is the message shown while waiting (callers
// point it at the command the user actually ran).
func waitForApproval(ctx context.Context, out io.Writer, sess *agentauth.Session, timeout time.Duration, resumeHint string) (*tokensView, error) {
	tokens, err := sess.MintFresh(ctx)
	if err == nil {
		// Already approved — nothing to wait for.
		return toView(tokens), nil
	}
	var pending *authclient.PendingError
	if !errors.As(err, &pending) {
		return nil, fmt.Errorf("mint token: %w", err)
	}

	fmt.Fprintln(out, "\n"+theme.Heading.Render("Approve this agent in the Jentic console:"))
	fmt.Fprintf(out, "    %s\n", theme.Command.Render(agentConsoleURL(sess.Meta.BaseURL, sess.Meta.AgentID)))
	fmt.Fprintf(out, "    %s\n\n", theme.Dim.Render(fmt.Sprintf("(or POST %s/agents/%s:approve — requires agents:write)", sess.Meta.BaseURL, sess.Meta.AgentID)))
	fmt.Fprintln(out, theme.Dim.Render(resumeHint))

	return pollForTokens(ctx, out, sess, timeout)
}

// agentConsoleURL builds the operator-facing UI link for approving an agent.
// The SPA is mounted under /app, so the agent detail page (where the Approve
// action lives) is {baseURL}/app/agents/{id}. This mirrors how access requests
// surface a clickable approve_url instead of a raw API endpoint.
func agentConsoleURL(baseURL, agentID string) string {
	return config.AppURL(baseURL, "agents/"+agentID)
}

// pollForTokens loops MintFresh until the agent is active, the timeout elapses,
// or the context is cancelled (Ctrl-C). The caller has already attempted (and
// announced) the first mint.
func pollForTokens(ctx context.Context, out io.Writer, sess *agentauth.Session, timeout time.Duration) (*tokensView, error) {
	deadline := time.Now().Add(timeout)
	delay := PollInitialDelay
	for {
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("timed out after %s waiting for approval; re-run once approved", timeout)
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(delay):
		}
		if delay < PollMaxDelay {
			delay += PollDelayStep
		}

		tokens, err := sess.MintFresh(ctx)
		if err == nil {
			fmt.Fprintln(out, theme.Success.Render("Agent approved."))
			return toView(tokens), nil
		}
		var pending *authclient.PendingError
		if !errors.As(err, &pending) {
			return nil, fmt.Errorf("mint token: %w", err)
		}
	}
}

type tokensView struct {
	AccessToken     string
	RefreshToken    string
	AccessExpiresAt time.Time
}

func toView(t *profile.Tokens) *tokensView {
	return &tokensView{
		AccessToken:     t.AccessToken,
		RefreshToken:    t.RefreshToken,
		AccessExpiresAt: t.AccessExpiresAt,
	}
}

func shorten(s string) string {
	if len(s) <= 16 {
		return s
	}
	return s[:10] + "..." + s[len(s)-4:]
}
