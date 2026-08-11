package cmdcore

// register_v2.go is the V2 (XDG context-store) arm of `jentic register` and
// `jentic bootstrap`. `register` is the single onboarding FRONT DOOR: which
// store it provisions is decided by resolveOnboardArm, so a fresh machine and
// a context-driven machine both get the one-command experience, while
// unmigrated V1 users keep the exact legacy behavior. The V2 arm is the
// human-facing composition of the same primitives `jentic env/identity/context`
// + `jentic identity register` expose individually:
//
//	env add <derived> --url <URL>       (setup arm only)
//	identity add <name> --type agent    (setup arm only)
//	context create <derived> --use      (setup arm only)
//	identity register                   (key mint + RFC 7591 DCR)
//	...wait for operator approval, then prove it with a token exchange.

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/huh"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// onboardArm selects which store an onboarding command (register/bootstrap)
// provisions.
type onboardArm int

const (
	// armLegacy provisions a ~/.jentic profile (V1). Chosen only when the
	// caller explicitly pinned the legacy store (--profile/--base-url) or the
	// machine has legacy config and no V2 config — unmigrated users keep V1
	// behavior verbatim until `jentic migrate`.
	armLegacy onboardArm = iota
	// armV2Active registers the ACTIVE context's identity with its environment
	// (the `identity register` flow plus the approval wait).
	armV2Active
	// armV2Setup is the fresh-machine flow: no config anywhere. One command
	// creates the environment + identity + context trio in the XDG store,
	// activates it, then registers — the V2 onboarding path.
	armV2Setup
)

// resolveOnboardArm decides the onboarding arm. explicitLegacy is true when the
// caller set --profile/--base-url (those name legacy-store entities, so they
// pin the V1 arm — same escape-hatch contract as the data-plane commands).
func (a *App) resolveOnboardArm(ctx context.Context, explicitLegacy bool) (onboardArm, *clictx.ActiveState) {
	if explicitLegacy {
		return armLegacy, nil
	}
	if st := clictx.ActiveV2(ctx); st != nil {
		return armV2Active, st
	}
	// No V2 context: an unmigrated V1 machine stays legacy; a machine with no
	// config at all onboards onto V2.
	if fc, err := config.Load(a.Paths); err == nil && fc != nil && fc.Loaded {
		return armLegacy, nil
	}
	return armV2Setup, nil
}

// v2SetupValues are the two things the fresh-machine arm must learn: where the
// install lives and what to call this agent. Everything else is derived.
type v2SetupValues struct {
	url  string // control-plane URL (becomes the environment's base_url)
	env  string // environment name ("" -> derived from the URL host)
	name string // identity + client name ("" -> derived from the hostname)
}

// errOnboardCancelled signals a user-aborted interactive onboarding (Esc in
// the form). Callers treat it as a clean no-op exit (matching the legacy arm's
// "Cancelled." + nil contract) — it exists so composed flows (bootstrap) can
// stop their remaining steps without inventing a fake success.
var errOnboardCancelled = errors.New("onboarding cancelled")

// registerV2Setup is the fresh-machine onboarding: create the environment +
// identity + context trio (idempotently — re-running reuses what exists),
// activate it, then fall into the shared register-and-wait flow. It returns
// the resolved values so composed flows (bootstrap) can reuse them (e.g. the
// install URL for skill templating).
func (a *App) registerV2Setup(ctx context.Context, vals v2SetupValues, timeout time.Duration, force, interactive bool) (v2SetupValues, error) {
	if interactive && vals.url == "" {
		fmt.Fprintln(a.Out, theme.Headingf("Agent onboarding"))
		fmt.Fprintln(a.Out, theme.Dim.Render("Connect this machine to a Jentic install; an operator approves it, then tokens mint."))
		if vals.name == "" {
			vals.name = defaultIdentityName()
		}
		if err := promptOnboardingV2(&vals.url, &vals.name); err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
				return vals, errOnboardCancelled
			}
			return vals, err
		}
	}
	if vals.url == "" {
		return vals, &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        "no Jentic install to register with",
			Actionable: "Pass --url <control-plane URL> (e.g. jentic register --url https://jentic.example.com).",
		}
	}
	vals.url = strings.TrimRight(vals.url, "/")
	if vals.name == "" {
		vals.name = defaultIdentityName()
	}
	if vals.env == "" {
		vals.env = deriveEnvName(vals.url)
	}
	for flag, name := range map[string]string{"--env": vals.env, "--name": vals.name} {
		if !sdkconfig.ValidName(name) {
			return vals, &ux.CodedError{
				Code:       ux.CodeMissingArgument,
				Msg:        fmt.Sprintf("invalid %s value %q (allowed: ^[a-z0-9][a-z0-9-]{0,63}$)", flag, name),
				Actionable: "Use lowercase letters, digits and hyphens.",
			}
		}
	}

	// Upsert the trio and activate it. Idempotent: an existing environment of
	// the same name is REUSED only when it points at the same URL (silently
	// re-pointing an env would hijack every context bound to it); identity and
	// context are reused as-is.
	envName := vals.env
	contextName := envName
	if err := sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
		if env, ok := cfg.Environments[envName]; ok && env.BaseURL != vals.url {
			return &ux.CodedError{
				Code: ux.CodeMissingArgument,
				Msg: fmt.Sprintf("environment %q already exists and points at %s, not %s",
					envName, env.BaseURL, vals.url),
				Actionable: "Pass a different --env name, or re-point it explicitly with `jentic env add --force`.",
			}
		}
		if _, ok := cfg.Environments[envName]; !ok {
			cfg.Environments[envName] = sdkconfig.Env{BaseURL: vals.url}
		}
		if _, ok := cfg.Identities[vals.name]; !ok {
			cfg.Identities[vals.name] = sdkconfig.Identity{Type: "agent"}
		}
		if _, ok := cfg.Contexts[contextName]; !ok {
			cfg.Contexts[contextName] = sdkconfig.Context{
				Environment: envName, Identity: vals.name, Mode: clictx.ModeHuman,
			}
		}
		cfg.ActiveContext = contextName
		return nil
	}); err != nil {
		return vals, err
	}

	fmt.Fprintln(a.Out, theme.Successf("Environment %q → %s", envName, vals.url))
	fmt.Fprintln(a.Out, theme.Successf("Identity %q (agent)", vals.name))
	fmt.Fprintln(a.Out, theme.Successf("Context %q (active)", contextName))

	return vals, a.registerV2(ctx, vals.name, envName, vals.url, vals.name, timeout, force)
}

// registerV2Active registers the ACTIVE context's identity with its
// environment — the same store `jentic identity register` writes, plus the
// human-facing approval wait.
func (a *App) registerV2Active(ctx context.Context, st *clictx.ActiveState, clientName string, timeout time.Duration, force bool) error {
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
		fmt.Fprintln(a.Out, theme.Infof("Identity %q already authenticates to %q with an API key; nothing to register.", identity, envName))
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
	if clientID == "" {
		fmt.Fprintln(a.Out, theme.Infof("Registering agent %q with %s ...", clientName, baseURL))
		r, rerr := auth.Register(baseURL, clientName, auth.PublicKeyToJWKS(pub))
		if rerr != nil {
			return rerr
		}
		clientID = r.ClientID
		status := r.Status
		if status == "" {
			status = "pending"
		}
		if err := saveRegState(identity, envName, clientID, status); err != nil {
			return err
		}
		fmt.Fprintln(a.Out, theme.Successf("Registered: client_id=%s status=%s", clientID, status))
	} else {
		fmt.Fprintln(a.Out, theme.Infof("Using existing registration client_id=%s (identity %q, environment %q)", clientID, identity, envName))
	}

	creds := auth.Credentials{BaseURL: baseURL, IdentityName: identity, EnvironmentName: envName}
	if err := a.waitForApprovalV2(ctx, creds, clientID, timeout); err != nil {
		return err
	}
	if err := saveRegState(identity, envName, clientID, "approved"); err != nil {
		return err
	}

	fmt.Fprintln(a.Out, theme.Successf("Token minted for %s.", identity))
	fmt.Fprintf(a.Out, "\n%s %s\n", theme.Dim.Render("Ready:"), theme.Command.Render("jentic catalog"))
	return nil
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
func (a *App) waitForApprovalV2(ctx context.Context, creds auth.Credentials, clientID string, timeout time.Duration) error {
	// Force a FRESH mint even if a (stale-scoped or old-client) token is
	// cached: register's contract is "when this returns, the server accepts
	// this identity as of NOW".
	classify := func(err error) (pending bool, out error) {
		var p *auth.PendingError
		if errors.As(err, &p) {
			return true, nil
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

	fmt.Fprintln(a.Out, "\n"+theme.Heading.Render("Approve this agent in the Jentic console:"))
	fmt.Fprintf(a.Out, "    %s\n", theme.Command.Render(agentConsoleURL(creds.BaseURL, clientID)))
	fmt.Fprintf(a.Out, "    %s\n\n", theme.Dim.Render(fmt.Sprintf("(or POST %s/agents/%s:approve — requires agents:write)", creds.BaseURL, clientID)))
	fmt.Fprintln(a.Out, theme.Dim.Render(registerResumeHint))

	deadline := time.Now().Add(timeout)
	delay := PollInitialDelay
	for {
		if time.Now().After(deadline) {
			return &ux.CodedError{
				Code:       ux.CodeTimeoutPending,
				Msg:        fmt.Sprintf("timed out after %s waiting for approval; re-run once approved", timeout),
				Actionable: "have an operator approve the agent, then re-run the same command",
				Details: map[string]any{
					"agent_id":    clientID,
					"approve_url": agentConsoleURL(creds.BaseURL, clientID),
				},
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
			fmt.Fprintln(a.Out, theme.Success.Render("Agent approved."))
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

// sanitizeConfigName lowercases s and strips everything outside the config
// name charset (^[a-z0-9][a-z0-9-]{0,63}$), returning "" when nothing valid
// survives.
func sanitizeConfigName(s string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(s) {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9':
			b.WriteRune(r)
		case r == '-' && b.Len() > 0:
			b.WriteRune(r)
		}
		if b.Len() >= 64 {
			break
		}
	}
	out := strings.TrimRight(b.String(), "-")
	if !sdkconfig.ValidName(out) {
		return ""
	}
	return out
}
