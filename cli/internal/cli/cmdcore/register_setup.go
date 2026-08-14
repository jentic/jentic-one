package cmdcore

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/huh"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

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
