package cmdcore

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/theme"
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
// door (register_v2.go): with an active context it registers that context's
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
// prose, matching the pre-AGT-21 behavior for non-agent callers.
func isMachineCtx(ctx context.Context) bool {
	if st := clictx.FromContext(ctx); st != nil {
		return st.Mode != clictx.ModeHuman
	}
	return false
}

func (a *App) registerE(ctx context.Context, opts *registerOptions) error {
	// An explicit --url always means "connect me to THIS install": it takes the
	// setup arm even when some other context is active, creating/reusing the
	// matching environment and switching to it — never silently registering
	// with whatever happened to be active.
	if opts.url == "" {
		if st := clictx.ActiveV2(ctx); st != nil {
			return a.RegisterV2Active(ctx, st, opts.name, opts.timeout, opts.force)
		}
	}
	vals := SetupValues{URL: opts.url, Env: opts.env, Name: opts.name}
	_, err := a.RegisterV2Setup(ctx, vals, opts.timeout, opts.force, opts.interactive)
	if errors.Is(err, ErrOnboardCancelled) {
		return nil
	}
	return err
}

func notEmptyField(label string) func(string) error {
	return func(s string) error {
		if strings.TrimSpace(s) == "" {
			return fmt.Errorf("%s must not be empty", label)
		}
		return nil
	}
}

// registerResumeHint is shown while waiting for approval, pointing back at the
// command the user actually ran so the documented resume path is correct.
const registerResumeHint = "Waiting for approval (Ctrl-C to stop and resume later with `jentic register`)..."

// Poll cadence for the approval wait. Package-level so tests can shrink it to
// keep the pending-path cases near-instant instead of real wall-clock seconds.
var (
	PollInitialDelay = 2 * time.Second
	PollMaxDelay     = 10 * time.Second
	PollDelayStep    = 1 * time.Second
)

// agentConsoleURL builds the operator-facing UI link for approving an agent.
// The SPA is mounted under /app, so the agent detail page (where the Approve
// action lives) is {baseURL}/app/agents/{id}. This mirrors how access requests
// surface a clickable approve_url instead of a raw API endpoint.
func agentConsoleURL(baseURL, agentID string) string {
	return config.AppURL(baseURL, "agents/"+agentID)
}

// agentClaimURL builds the human-facing UI link for CLAIMING ownership of a
// self-registered agent, carrying the single-use claim token so the console can
// pre-fill it. The console claim page lives at {baseURL}/app/agents/{id}/claim
// and reads the token from the `token` query param (enterprise AgentClaimPage:
// searchParams.get("token")). The backend does not hand back a ready-made claim
// URL and the page may not exist on every deployment, so the raw token + the
// `jentic identity claim` command are always shown alongside as the reliable
// fallback (an agent cannot claim itself; a human must). The token is a
// short-lived bearer capability shown once — never persisted, never logged.
func agentClaimURL(baseURL, agentID, claimToken string) string {
	u := config.AppURL(baseURL, "agents/"+agentID+"/claim")
	if claimToken != "" {
		u += "?token=" + url.QueryEscape(claimToken)
	}
	return u
}

// presentClaimAffordance guides the HUMAN to take ownership of a just-registered
// agent when the backend enabled claiming (non-empty claimToken). It is a no-op
// when claiming is off (OSS default) — so the OSS onboarding output is unchanged
// — and in machine mode, where the terminal ux.Result.Fields carry the
// machine-actionable signal instead (an agent cannot claim itself). Shown once:
// the token is single-use, short-lived, and deliberately never persisted, so we
// surface it exactly like the backend's "returned once" contract. Both a
// console link (by convention; the page may not exist everywhere) AND the raw
// token + exact command are printed, so the human always has a reliable path.
func (a *App) presentClaimAffordance(ctx context.Context, baseURL, agentID, claimToken string) {
	if claimToken == "" || isMachineCtx(ctx) {
		return
	}
	fmt.Fprintln(a.Out, "\n"+theme.Heading.Render("Claim ownership of this agent (you, the human — an agent cannot claim itself):"))
	fmt.Fprintf(a.Out, "    %s\n", theme.Command.Render(agentClaimURL(baseURL, agentID, claimToken)))
	fmt.Fprintln(a.Out, theme.Dim.Render("    Open the link above, or run the command below with the one-time token:"))
	fmt.Fprintf(a.Out, "    %s\n", theme.Command.Render(fmt.Sprintf("jentic identity claim %s --token %s", agentID, claimToken)))
	fmt.Fprintf(a.Out, "    %s\n", theme.Dimf("Token %s is single-use and short-lived — it is shown only once and is not saved.", claimToken))
}

// --- onboarding body -------------------------------------------------------
//
// The rest of this file implements `jentic register` / `jentic bootstrap`
// against the XDG context store. `register` is the single onboarding FRONT
// DOOR: a machine with an active context registers that context's identity; a
// fresh machine gets the one-command setup. It is the human-facing composition
// of the same primitives `jentic env/identity/context` + `jentic identity
// register` expose individually:
//
//	env add <derived> --url <URL>       (setup arm only)
//	identity add <name> --type agent    (setup arm only)
//	context create <derived> --use      (setup arm only)
//	identity register                   (key mint + RFC 7591 DCR)
//	...wait for operator approval, then prove it with a token exchange.

// SetupValues are the two things the fresh-machine arm must learn: where the
// install lives and what to call this agent. Everything else is derived.
// Exported (with exported fields) because bootstrap now lives in the
// localagentcmd package (ARCH-1) and constructs this to drive onboarding.
type SetupValues struct {
	URL  string // control-plane URL (becomes the environment's base_url)
	Env  string // environment name ("" -> derived from the URL host)
	Name string // identity + client name ("" -> derived from the hostname)
}

// ErrOnboardCancelled signals a user-aborted interactive onboarding (Esc in
// the form). Callers treat it as a clean no-op exit (matching the legacy arm's
// "Cancelled." + nil contract) — it exists so composed flows (bootstrap) can
// stop their remaining steps without inventing a fake success.
var ErrOnboardCancelled = errors.New("onboarding cancelled")

// RegisterV2Setup is the fresh-machine onboarding: create the environment +
// identity + context trio (idempotently — re-running reuses what exists),
// activate it, then fall into the shared register-and-wait flow. It returns
// the resolved values so composed flows (bootstrap) can reuse them (e.g. the
// install URL for skill templating).
func (a *App) RegisterV2Setup(ctx context.Context, vals SetupValues, timeout time.Duration, force, interactive bool) (SetupValues, error) {
	if interactive && vals.URL == "" {
		fmt.Fprintln(a.Out, theme.Headingf("Agent onboarding"))
		fmt.Fprintln(a.Out, theme.Dim.Render("Connect this machine to a Jentic install; an operator approves it, then tokens mint."))
		if vals.Name == "" {
			vals.Name = defaultIdentityName()
		}
		if err := promptOnboardingV2(&vals.URL, &vals.Name); err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
				return vals, ErrOnboardCancelled
			}
			return vals, err
		}
	}
	if vals.URL == "" {
		return vals, &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        "no Jentic install to register with",
			Actionable: "Pass --url <control-plane URL> (e.g. jentic register --url https://jentic.example.com).",
		}
	}
	vals.URL = strings.TrimRight(vals.URL, "/")
	// UX-2/QA-9: rewrite a localhost control-plane URL to 127.0.0.1. The
	// token-exchange audience is matched byte-for-byte against the backend's
	// canonical_base_url, and a local backend canonicalises to 127.0.0.1 — so
	// `--url http://localhost:8000` would sign an aud the server rejects with
	// invalid_grant (mis-read as "pending approval"). Normalising here removes
	// the papercut at the source and keeps the seeded broker_url on 127.0.0.1 too.
	if norm, changed := normalizeLoopbackURL(vals.URL); changed {
		fmt.Fprintln(a.Err, theme.Dim.Render(fmt.Sprintf(
			"note: using %s (localhost is normalised to 127.0.0.1 so the token audience matches the local backend)", norm)))
		vals.URL = norm
	}
	if vals.Name == "" {
		vals.Name = defaultIdentityName()
	}
	if vals.Env == "" {
		vals.Env = deriveEnvName(vals.URL)
	}
	for flag, name := range map[string]string{"--env": vals.Env, "--name": vals.Name} {
		if !sdkconfig.ValidName(name) {
			return vals, &ux.CodedError{
				Code:       ux.CodeMissingArgument,
				Msg:        fmt.Sprintf("invalid %s value %q (allowed: ^[a-z0-9][a-z0-9-]{0,63}$)", flag, name),
				Actionable: "Use lowercase letters, digits and hyphens.",
			}
		}
	}

	// Upsert the trio and activate it. The context is named per-identity-per-env
	// (env "-" name) so registering a SECOND agent into the same env gets its own
	// switchable context instead of silently hijacking the first agent's binding
	// (which used to leave you authenticated as the wrong, older identity). Env is
	// still reused only when it points at the same URL (silently re-pointing an
	// env would hijack every context bound to it); the identity is upserted.
	envName := vals.Env
	contextName := sdkconfig.SanitizeName(envName + "-" + vals.Name)
	if err := sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
		if env, ok := cfg.Environments[envName]; ok && env.BaseURL != vals.URL {
			return &ux.CodedError{
				Code: ux.CodeMissingArgument,
				Msg: fmt.Sprintf("environment %q already exists and points at %s, not %s",
					envName, env.BaseURL, vals.URL),
				Actionable: "Pass a different --env name, or re-point it explicitly with `jentic env add --force`.",
			}
		}
		if _, ok := cfg.Environments[envName]; !ok {
			env := sdkconfig.Env{BaseURL: vals.URL}
			// Local convenience: when the control plane is a loopback address,
			// the broker is co-located on the standard local broker port over
			// plain HTTP (jenticctl install stands both up together). Seeding it
			// here means `jentic execute` works out of the box on a local install
			// instead of falling back to the https default and hitting a TLS
			// error. For REMOTE/enterprise URLs we leave broker_url unset — there
			// the broker frequently lives on a different domain and MUST be set
			// explicitly (`jentic env add --broker-url`); it is never derived.
			if bu := localBrokerURL(vals.URL); bu != "" {
				env.BrokerURL = bu
			}
			cfg.Environments[envName] = env
		}
		if _, ok := cfg.Identities[vals.Name]; !ok {
			cfg.Identities[vals.Name] = sdkconfig.Identity{Type: "agent"}
		}
		// (Re)bind unconditionally: the per-identity name is stable, so re-running
		// for the SAME identity+env is idempotent, while a NEW identity gets its
		// own context. Then activate it — register's contract is "when this
		// returns, THIS identity is the live one".
		cfg.Contexts[contextName] = sdkconfig.Context{
			Environment: envName, Identity: vals.Name, Mode: clictx.ModeHuman,
		}
		cfg.ActiveContext = contextName
		return nil
	}); err != nil {
		return vals, err
	}

	a.registerProgress(ctx, theme.Successf("Environment %q → %s", envName, vals.URL))
	a.registerProgress(ctx, theme.Successf("Identity %q (agent)", vals.Name))
	a.registerProgress(ctx, theme.Successf("Context %q (active)", contextName))

	return vals, a.registerV2(ctx, vals.Name, envName, vals.URL, vals.Name, timeout, force)
}

// RegisterV2Active registers the ACTIVE context's identity with its
// environment — the same store `jentic identity register` writes, plus the
// human-facing approval wait.
func (a *App) RegisterV2Active(ctx context.Context, st *clictx.ActiveState, clientName string, timeout time.Duration, force bool) error {
	if st.BaseURL == "" {
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("environment %q has no base_url", st.EnvironmentName),
			Actionable: "Set it with `jentic env add` / edit the environment.",
		}
	}
	if clientName == "" {
		clientName = st.IdentityName
	}
	return a.registerV2(ctx, st.IdentityName, st.EnvironmentName, st.BaseURL, clientName, timeout, force)
}

// registerV2 is the shared V2 register-and-wait body: mint the env-scoped key
// if absent, perform RFC 7591 DCR (reusing an existing registration so the
// flow is resumable), persist client_id/status, then wait for operator
// approval by attempting the token exchange — exactly the credential every
// data command will use, so success here IS end-to-end proof.
func (a *App) registerV2(ctx context.Context, identity, envName, baseURL, clientName string, timeout time.Duration, force bool) error {
	ref := auth.IdentityRef{Identity: identity, Environment: envName}

	// A jak_* API-key identity has nothing to register: the key IS the
	// long-lived credential.
	if key, err := auth.ReadAPIKey(ref); err == nil && key != "" {
		a.registerProgress(ctx, theme.Infof("Identity %q already authenticates to %q with an API key; nothing to register.", identity, envName))
		if isMachineCtx(ctx) {
			ux.FromContext(ctx).Render(ux.Result{
				Status: ux.StatusRegistered, Resource: "identity", Name: identity,
				Message: "already authenticates with an API key",
			})
		}
		return nil
	}

	priv, err := auth.GetOrGenerateKey(ref)
	if err != nil {
		return err
	}
	pub, ok := priv.Public().(ed25519.PublicKey)
	if !ok {
		return &ux.CodedError{Code: ux.CodeInternalError, Msg: "env-scoped key is not Ed25519"}
	}

	cfg, err := sdkconfig.Load()
	if err != nil {
		return err
	}
	reg := cfg.Identities[identity].Environments[envName]
	if force {
		// Re-register: drop the old registration AND its cached tokens (they
		// were minted as the old client_id and can only confuse from here).
		reg = sdkconfig.EnvRegState{}
		if err := auth.InvalidateTokens(ref); err != nil {
			return err
		}
	}

	clientID := reg.ClientID
	claimToken := ""
	if clientID == "" {
		a.registerProgress(ctx, theme.Infof("Registering agent %q with %s ...", clientName, baseURL))
		r, rerr := auth.Register(baseURL, clientName, auth.PublicKeyToJWKS(pub))
		if rerr != nil {
			// AGT-21: DCR is the most common failure point (control plane
			// unreachable / TLS / 4xx). Surface it as a coded TRANSPORT_ERROR so
			// an agent gets a closed error_code + actionable step instead of a
			// raw exit-1 string it cannot branch on. The interceptor's
			// decorateCodedErrors renders this through the Audience.
			return &ux.CodedError{
				Code:       ux.CodeTransportError,
				Msg:        "agent registration failed: " + rerr.Error(),
				Actionable: "Check the install URL is reachable (jentic env list) and the control plane is running, then re-run register.",
			}
		}
		clientID = r.ClientID
		// The claim token is minted ONCE, here at registration; a later re-run
		// (existing clientID) never sees it again — matching the backend's
		// "returned once" contract. Empty on the OSS default (no minter).
		claimToken = r.ClaimToken
		status := r.Status
		if status == "" {
			status = "pending"
		}
		if err := saveRegState(identity, envName, clientID, status); err != nil {
			return err
		}
		a.registerProgress(ctx, theme.Successf("Registered: client_id=%s status=%s", clientID, status))
	} else {
		a.registerProgress(ctx, theme.Infof("Using existing registration client_id=%s (identity %q, environment %q)", clientID, identity, envName))
	}

	// If claiming is enabled, guide the HUMAN to take ownership. This is shown
	// once (the token is single-use, short-lived, never persisted) and before
	// the approval wait, since the human is present now. On a claim-enabled
	// backend the console claim page does claim (sets owner_id) AND approve
	// (pending -> active) — both are required before this agent can mint a
	// token. No-op on the OSS default (empty token).
	a.presentClaimAffordance(ctx, baseURL, clientID, claimToken)

	creds := auth.Credentials{BaseURL: baseURL, IdentityName: identity, EnvironmentName: envName}
	if err := a.waitForApprovalV2(ctx, creds, clientID, timeout, claimToken != ""); err != nil {
		return err
	}
	if err := saveRegState(identity, envName, clientID, "approved"); err != nil {
		return err
	}

	if isMachineCtx(ctx) {
		// Machine mode: one JSON Result on stdout is the terminal success signal,
		// replacing the human "Token minted" / "Ready:" prose (which stays
		// suppressed by registerProgress). If claiming is enabled, surface the
		// fact machine-readably (never the token in prose) so an automated caller
		// can route a human to complete the claim — an agent cannot claim itself.
		res := ux.Result{
			Status: ux.StatusRegistered, Resource: "identity", Name: identity, ID: clientID,
			Message: "approved; token minted",
		}
		if claimToken != "" {
			// A claim needs a HUMAN (an agent cannot claim itself), so the machine
			// signal is the fact + where a human goes — not the raw token, which
			// the key-based redactor would mask anyway and which the agent cannot
			// action. The human-readable token is shown once via presentClaimAffordance.
			res.Fields = map[string]any{
				"claim_required": true,
				"claim_url":      agentClaimURL(baseURL, clientID, ""),
				"claim_command":  fmt.Sprintf("jentic identity claim %s --token <claim_token>", clientID),
			}
		}
		ux.FromContext(ctx).Render(res)
		return nil
	}
	fmt.Fprintln(a.Out, theme.Successf("Token minted for %s.", identity))
	// Make the active identity unambiguous and switching obvious: register may
	// have created a NEW per-identity context (env "-" identity), so spell out
	// who you now are and how to move between agents. This is the same name
	// RegisterV2Setup activated.
	contextName := sdkconfig.SanitizeName(envName + "-" + identity)
	fmt.Fprintf(a.Out, "\n%s\n", theme.Dimf("You are now %q on %q (context %q).", identity, envName, contextName))
	fmt.Fprintf(a.Out, "%s %s\n", theme.Dim.Render("Switch agents:"), theme.Command.Render("jentic context use <name>"))
	fmt.Fprintf(a.Out, "%s %s\n", theme.Dim.Render("See all:      "), theme.Command.Render("jentic context list"))
	// Human-context nudge (UX6): `register` mints tokens only — it silently skips
	// the agent skill + isolation that `bootstrap` adds. A person who reached here
	// by hand may have wanted the full setup, so point them at it. This whole
	// success block is human-only (machine mode returned above), so no TTY guard
	// is needed.
	fmt.Fprintf(a.Out, "\n%s %s%s\n",
		theme.Dim.Render("Tip:"),
		theme.Command.Render("jentic bootstrap"),
		theme.Dim.Render(" also installs the agent skill and can isolate the agent — run it if you're setting up a coding agent."))
	// Multi-agent case: registering a SECOND agent into an env creates its own
	// per-identity context and silently makes it active (RegisterV2Setup). Spell
	// out WHY a new context appeared and how to get back, so a user who now sees
	// an unfamiliar active context isn't left wondering what happened to their
	// first agent (UX5).
	if prev := siblingContextInEnv(envName, contextName); prev != "" {
		fmt.Fprintf(a.Out, "\n%s\n", theme.Dimf(
			"Created a new context %q (another agent in env %q); your previous context %q is unchanged.",
			contextName, envName, prev))
		fmt.Fprintf(a.Out, "%s %s\n", theme.Dim.Render("Switch back:  "), theme.Command.Render("jentic context use "+prev))
	}
	a.printNextSteps()
	return nil
}

// siblingContextInEnv returns the name of an existing context bound to the same
// environment as (but a different context than) the one just registered — the
// signal that this register minted a SECOND agent into an env that already had
// one. Returns "" when there is no such sibling (the common single-agent case)
// or the config can't be read. Deterministic (lowest name) so the "switch back"
// pointer is stable.
func siblingContextInEnv(envName, currentContext string) string {
	cfg, err := sdkconfig.Load()
	if err != nil {
		return ""
	}
	prev := ""
	for name, c := range cfg.Contexts {
		if name == currentContext || c.Environment != envName {
			continue
		}
		if prev == "" || name < prev {
			prev = name
		}
	}
	return prev
}

// printNextSteps teaches the core discover -> inspect -> execute workflow after a
// successful register. Bare `jentic register` on an already-configured machine
// used to (in V1) reopen onboarding; now it just re-mints, so this block is what
// makes "what do I do now?" obvious — a few copy-pasteable examples plus the
// pointer to full help, in place of a bare "Ready:" line.
func (a *App) printNextSteps() {
	fmt.Fprintf(a.Out, "\n%s\n", theme.Heading.Render("Next steps"))
	steps := []struct{ desc, cmd string }{
		{"Browse the API catalog", "jentic catalog"},
		{"Find an operation (each result prints a ready-to-paste inspect/execute target)", "jentic search \"send a slack message\""},
		{"See what you can run right now", "jentic access whoami"},
		{"A fresh agent is bound to no APIs — request access to one you found", "jentic access request --toolkit <vendor/name> --wait"},
		{"Inspect that operation (paste the target search printed)", "jentic inspect <METHOD:url from search>"},
		{"Run it (same target)", "jentic execute <METHOD:url from search> -d '{\"key\":\"value\"}'"},
	}
	for _, s := range steps {
		fmt.Fprintf(a.Out, "  %s\n    %s\n", theme.Dim.Render(s.desc), theme.Command.Render(s.cmd))
	}
	fmt.Fprintf(a.Out, "\n%s %s\n", theme.Dim.Render("See all commands:"), theme.Command.Render("jentic --help"))
	// Advertise doctor as the first thing to run when stuck (UX9): it is
	// read-only and each of its warnings already carries the exact remediation,
	// but it lives under "Local agent client" and isn't surfaced at the moments
	// of confusion.
	fmt.Fprintf(a.Out, "%s %s\n", theme.Dim.Render("Stuck? Check your setup:"), theme.Command.Render("jentic doctor"))
}

// saveRegState persists the (identity, environment) registration record.
func saveRegState(identity, envName, clientID, status string) error {
	return sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
		ident := cfg.Identities[identity]
		if ident.Environments == nil {
			ident.Environments = make(map[string]sdkconfig.EnvRegState)
		}
		ident.Environments[envName] = sdkconfig.EnvRegState{ClientID: clientID, Status: status}
		cfg.Identities[identity] = ident
		return nil
	})
}

// waitForApprovalV2 is the XDG-store sibling of waitForApproval: it proves
// approval by forcing a fresh token exchange (the exact credential path every
// data command uses). A pending registration prints the operator console link
// and polls on the shared cadence; the timeout returns TIMEOUT_PENDING (exit 3)
// because re-running after approval resumes cleanly (AGT-4 semantics).
//
// claimPending is true when registration returned a claim token: the human must
// still claim + approve in the console before the agent can mint. In that state
// the backend rejects the token exchange with an ambiguous 400 invalid_grant
// "Assertion is invalid" — the SAME string it uses for a real audience mismatch
// (the approval-status gate is checked before signature/audience). So while a
// claim is outstanding we treat that as PENDING (keep waiting / exit clean)
// rather than the hard audience-mismatch failure, which would abort the flow the
// moment it started.
func (a *App) waitForApprovalV2(ctx context.Context, creds auth.Credentials, clientID string, timeout time.Duration, claimPending bool) error {
	// Force a FRESH mint even if a (stale-scoped or old-client) token is
	// cached: register's contract is "when this returns, the server accepts
	// this identity as of NOW".
	classify := func(err error) (pending bool, out error) {
		var p *auth.PendingError
		if errors.As(err, &p) {
			return true, nil
		}
		// QA-9: an assertion-validation failure (usually an audience mismatch) is
		// NOT pending — polling would hang forever. Stop with an actionable code
		// so the operator fixes the URL/backend rather than waiting. EXCEPTION:
		// when a claim is still outstanding, the backend returns this same string
		// for a not-yet-claimed/approved agent, so treat it as pending instead of
		// aborting (the human is being pointed at the claim console right now).
		var ai *auth.AssertionInvalidError
		if errors.As(err, &ai) {
			if claimPending {
				return true, nil
			}
			return false, &ux.CodedError{
				Code: ux.CodeNotAuthenticated,
				Msg:  "the backend rejected the signed assertion: " + ai.Error(),
				Actionable: "This is almost always an audience mismatch: the URL you registered with must exactly match " +
					"the backend's canonical_base_url. For a local backend use http://127.0.0.1:8000 (not localhost), " +
					"or align the backend's auth.canonical_base_url to the URL you used.",
			}
		}
		return false, fmt.Errorf("mint token: %w", err)
	}

	_, err := auth.RefreshBearerToken(creds)
	if err == nil {
		return nil // already approved — nothing to wait for
	}
	if pending, cerr := classify(err); !pending {
		return cerr
	}

	// Pending: point the human at the right console action, or (machine) surface
	// the URL as a stderr diagnostic — never on stdout, which is reserved for the
	// terminal JSON Result. When a claim is outstanding the claim affordance was
	// already printed above (it claims AND approves), so we don't repeat the
	// approve-console block; we just note we're waiting. Otherwise point at the
	// approve console. The TIMEOUT_PENDING envelope also carries the URL in
	// details, so an agent that times out still gets it machine-readably
	// (AGT-21/AGT-4).
	switch {
	case isMachineCtx(ctx) && claimPending:
		fmt.Fprintf(a.Err, "waiting for claim + approval: %s\n", agentClaimURL(creds.BaseURL, clientID, ""))
	case isMachineCtx(ctx):
		fmt.Fprintf(a.Err, "waiting for approval: %s\n", agentConsoleURL(creds.BaseURL, clientID))
	case claimPending:
		fmt.Fprintln(a.Out, "\n"+theme.Dim.Render("Waiting for you to claim + approve this agent in the console (see the link above)..."))
		fmt.Fprintln(a.Out, theme.Dim.Render(registerResumeHint))
	default:
		fmt.Fprintln(a.Out, "\n"+theme.Heading.Render("Approve this agent in the Jentic console:"))
		fmt.Fprintf(a.Out, "    %s\n", theme.Command.Render(agentConsoleURL(creds.BaseURL, clientID)))
		fmt.Fprintf(a.Out, "    %s\n\n", theme.Dim.Render(fmt.Sprintf("(or POST %s/agents/%s:approve — requires agents:write)", creds.BaseURL, clientID)))
		fmt.Fprintln(a.Out, theme.Dim.Render(registerResumeHint))
	}

	deadline := time.Now().Add(timeout)
	delay := PollInitialDelay
	for {
		if time.Now().After(deadline) {
			msg := fmt.Sprintf("timed out after %s waiting for approval; re-run once approved", timeout)
			actionable := "have an operator approve the agent, then re-run the same command"
			details := map[string]any{
				"agent_id":    clientID,
				"approve_url": agentConsoleURL(creds.BaseURL, clientID),
			}
			if claimPending {
				msg = fmt.Sprintf("timed out after %s waiting for the claim + approval; re-run once you've claimed and approved it", timeout)
				actionable = "open the claim link printed above (it claims and approves this agent), then re-run the same command"
				details["claim_url"] = agentClaimURL(creds.BaseURL, clientID, "")
			}
			return &ux.CodedError{
				Code:       ux.CodeTimeoutPending,
				Msg:        msg,
				Actionable: actionable,
				Details:    details,
			}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(delay):
		}
		if delay < PollMaxDelay {
			delay += PollDelayStep
		}

		if _, err := auth.BearerToken(creds); err == nil {
			a.registerProgress(ctx, theme.Success.Render("Agent approved."))
			return nil
		} else if pending, cerr := classify(err); !pending {
			return cerr
		}
	}
}

// promptOnboardingV2 collects the two fresh-machine onboarding values, styled
// like the legacy register wizard. Environment/context names are derived (and
// overridable via flags), so the form stays two fields.
func promptOnboardingV2(installURL, name *string) error {
	return prompt.NewForm(
		huh.NewGroup(
			prompt.Input().Title("Jentic install URL").
				Description("The control-plane URL this agent registers with and talks to.").
				Value(installURL).Validate(notEmptyField("url")),
			prompt.Input().Title("Agent name").
				Description("Identity name, shown to the operator approving this agent.").
				Value(name).Validate(notEmptyField("name")),
		),
	).WithShowHelp(true).Run()
}

// deriveEnvName proposes an environment name from the install URL: the first
// DNS label of the host, sanitized to the config-name charset ("default" when
// nothing survives — e.g. a bare IP). Predictable and overridable via --env.
func deriveEnvName(installURL string) string {
	u, err := url.Parse(installURL)
	if err != nil || u.Hostname() == "" {
		return "default"
	}
	label, _, _ := strings.Cut(u.Hostname(), ".")
	if s := sanitizeConfigName(label); s != "" {
		return s
	}
	return "default"
}

// localBrokerURL returns the co-located local broker URL for a loopback control
// plane, or "" for any non-loopback (remote/enterprise) URL. On a local install
// jenticctl stands the broker up on the standard broker port over plain HTTP, on
// the same loopback host, so `jentic execute` should target it there rather than
// the https default. It is deliberately NOT a general base_url→broker_url
// derivation: remote deployments run the broker on a different domain and must
// set broker_url explicitly.
func localBrokerURL(installURL string) string {
	u, err := url.Parse(installURL)
	if err != nil {
		return ""
	}
	host := u.Hostname()
	if !isLoopbackHost(host) {
		return ""
	}
	// QA-9: canonicalise a "localhost" broker host to 127.0.0.1 too, so a seeded
	// broker_url never carries the audience-mismatching name even if this is ever
	// called with a non-normalised URL.
	if host == "localhost" {
		host = "127.0.0.1"
	}
	_, port, _ := strings.Cut(config.DefaultBrokerHost, ":")
	return "http://" + net.JoinHostPort(host, port)
}

// normalizeLoopbackURL rewrites a "localhost" host to "127.0.0.1", preserving
// scheme, port, and path. It returns the (possibly unchanged) URL and whether a
// rewrite happened. Non-localhost hosts (including 127.0.0.1 and remote hosts)
// are returned verbatim. A malformed URL is returned unchanged.
func normalizeLoopbackURL(raw string) (string, bool) {
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() != "localhost" {
		return raw, false
	}
	if p := u.Port(); p != "" {
		u.Host = net.JoinHostPort("127.0.0.1", p)
	} else {
		u.Host = "127.0.0.1"
	}
	return u.String(), true
}

// isLoopbackHost reports whether host is a loopback name/address ("localhost",
// 127.0.0.0/8, or ::1).
func isLoopbackHost(host string) bool {
	if host == "localhost" {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return false
}

// defaultIdentityName proposes an identity name from the machine hostname
// (first label, sanitized), falling back to "agent".
func defaultIdentityName() string {
	host, err := os.Hostname()
	if err == nil {
		label, _, _ := strings.Cut(host, ".")
		if s := sanitizeConfigName(label); s != "" {
			return s
		}
	}
	return "agent"
}

// sanitizeConfigName coerces s into the config name charset via the canonical
// config.SanitizeName (ARCH-22), returning "" when nothing valid survives so the
// callers (deriveEnvName/deriveIdentityName) can apply their own fallback.
func sanitizeConfigName(s string) string {
	return sdkconfig.SanitizeName(s)
}
