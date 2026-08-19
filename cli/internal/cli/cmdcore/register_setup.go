package cmdcore

import (
	"context"
	"errors"
	"fmt"
	"net/url"
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
// The rest of this file implements `jentic register` / `jentic setup`
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

// SetupValues are the things the fresh-machine arm must learn: where the
// install lives, what to call this agent, and (for a remote install) where the
// broker lives. Everything else is derived.
// Exported (with exported fields) because setup now lives in the
// localagentcmd package (ARCH-1) and constructs this to drive onboarding.
type SetupValues struct {
	URL       string // control-plane URL (becomes the environment's base_url)
	Env       string // environment name ("" -> derived from the URL host)
	Name      string // identity + client name ("" -> derived from the hostname)
	BrokerURL string // broker URL ("" -> loopback seed, or unset for remote — never derived)
}

// ErrOnboardCancelled signals a user-aborted interactive onboarding (Esc in
// the form). Callers treat it as a clean no-op exit (matching the legacy arm's
// "Cancelled." + nil contract) — it exists so composed flows (setup) can
// stop their remaining steps without inventing a fake success.
var ErrOnboardCancelled = errors.New("onboarding cancelled")

// RegisterSetup is the fresh-machine onboarding: create the environment +
// identity + context trio (idempotently — re-running reuses what exists),
// activate it, then fall into the shared register-and-wait flow. It returns
// the resolved values so composed flows (setup) can reuse them (e.g. the
// install URL for skill templating).
func (a *App) RegisterSetup(ctx context.Context, vals SetupValues, timeout time.Duration, force, interactive bool) (SetupValues, error) {
	st := theme.StylesFromContext(ctx)
	if interactive && vals.URL == "" {
		fmt.Fprintln(a.Out, st.Headingf("Agent onboarding"))
		fmt.Fprintln(a.Out, st.Dim.Render("Connect this machine to a Jentic install; an operator approves it, then tokens mint."))
		if vals.Name == "" {
			vals.Name = defaultIdentityName()
		}
		if err := promptOnboarding(&vals.URL, &vals.Name, &vals.BrokerURL); err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, st.Dim.Render("Cancelled."))
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
		fmt.Fprintln(a.Err, st.Dim.Render(fmt.Sprintf(
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
	// An explicit broker URL must be sane BEFORE anything is persisted: it is
	// the target `jentic execute` sends the agent bearer to, so it obeys the
	// same transport invariant as every other bearer-carrying URL (https
	// required for any non-loopback host — SEC-1).
	vals.BrokerURL = strings.TrimRight(strings.TrimSpace(vals.BrokerURL), "/")
	if vals.BrokerURL != "" {
		if err := validateBrokerURL(vals.BrokerURL); err != nil {
			return vals, err
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
	// brokerConfigured records whether the resolved environment ends up with a
	// broker_url — seeded here for a loopback control plane, or already present
	// on a pre-existing env. A remote env with no broker gets a next-step hint
	// below (remote-cli-usage F1/Phase B): `jentic execute` fail-closes without
	// one. Reading the resolved value (not re-deriving from the URL) avoids a
	// false hint when re-registering an existing remote env that already has a
	// manually-set broker_url.
	brokerConfigured := false
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
			if bu := seedBrokerURL(vals.BrokerURL, vals.URL); bu != "" {
				env.BrokerURL = bu
			}
			cfg.Environments[envName] = env
		} else if vals.BrokerURL != "" {
			// Existing env: an explicit --broker-url completes an env that has
			// none, and refuses (without --force) to silently repoint one that
			// already names a DIFFERENT broker — same shape as the base-URL
			// mismatch guard above.
			if err := applyBrokerURL(cfg, envName, vals.BrokerURL, force); err != nil {
				return err
			}
		}
		brokerConfigured = cfg.Environments[envName].BrokerURL != ""
		// (Re)bind and activate the context. The context name is derived by
		// SanitizeName(env + "-" + name); that primitive is many-to-one (charset
		// collapse + 64-char clamp), so two DIFFERENT (env, identity) pairs can
		// derive the SAME contextName — e.g. two long identity names sharing a
		// 64-char prefix. Overwriting unconditionally would then silently repoint
		// the context onto a different identity (F2, review round-3 #4): the exact
		// "authenticated as the wrong identity" failure the per-identity naming is
		// meant to prevent. Guard it: if the derived context already exists and
		// binds a DIFFERENT identity or environment, refuse without --force rather
		// than hijack it. Re-registering the SAME identity+env stays idempotent.
		if existing, ok := cfg.Contexts[contextName]; ok &&
			(existing.Identity != vals.Name || existing.Environment != envName) && !force {
			return &ux.CodedError{
				Code: ux.CodeMissingArgument,
				Msg: fmt.Sprintf("context %q already binds identity %q on environment %q; "+
					"registering %q on %q would overwrite it (their names collapse to the same context name)",
					contextName, existing.Identity, existing.Environment, vals.Name, envName),
				Actionable: "Use a shorter/distinct --name, or pass --force to repoint this context.",
			}
		}
		if _, ok := cfg.Identities[vals.Name]; !ok {
			cfg.Identities[vals.Name] = sdkconfig.Identity{Type: "agent"}
		}
		// Then activate it — register's contract is "when this returns, THIS
		// identity is the live one".
		cfg.Contexts[contextName] = sdkconfig.Context{
			Environment: envName, Identity: vals.Name, Mode: clictx.ModeHuman,
		}
		cfg.ActiveContext = contextName
		return nil
	}); err != nil {
		return vals, err
	}

	a.registerProgress(ctx, st.Successf("Environment %q → %s", envName, vals.URL))
	if vals.BrokerURL != "" {
		a.registerProgress(ctx, st.Successf("Broker → %s", vals.BrokerURL))
	}
	a.registerProgress(ctx, st.Successf("Identity %q (agent)", vals.Name))
	a.registerProgress(ctx, st.Successf("Context %q (active)", contextName))

	// Remote control plane with no broker → teach the mandatory next step now,
	// at the moment it matters (remote-cli-usage F1/Phase B). `jentic execute`
	// fail-closes when base_url is remote and broker_url is empty. Routed through
	// registerProgress, so it is suppressed in machine mode (never corrupts an
	// agent's stdout stream).
	if !brokerConfigured {
		a.registerProgress(ctx, theme.Warnf("No broker_url set for remote environment %q. "+
			"`jentic execute` needs one — re-run `jentic register --url %s --broker-url https://<broker-host>` to fill it in "+
			"(ask your operator for the broker URL).", envName, vals.URL))
	}

	return vals, a.registerAndWait(ctx, vals.Name, envName, vals.URL, vals.Name, timeout, force)
}

// RegisterActive registers the ACTIVE context's identity with its
// environment — the same store `jentic identity register` writes, plus the
// human-facing approval wait. A non-empty brokerURL is applied to the active
// environment first (fill-if-empty; a different existing broker refuses
// without force), so `jentic register --broker-url …` is the one-line way to
// complete a remote environment that was onboarded without one.
func (a *App) RegisterActive(ctx context.Context, st *clictx.ActiveState, clientName, brokerURL string, timeout time.Duration, force bool) error {
	if st.BaseURL == "" {
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("environment %q has no base_url", st.EnvironmentName),
			Actionable: "Set it with `jentic env add` / edit the environment.",
		}
	}
	brokerURL = strings.TrimRight(strings.TrimSpace(brokerURL), "/")
	if brokerURL != "" {
		if err := validateBrokerURL(brokerURL); err != nil {
			return err
		}
		if err := sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
			return applyBrokerURL(cfg, st.EnvironmentName, brokerURL, force)
		}); err != nil {
			return err
		}
		a.registerProgress(ctx, theme.StylesFromContext(ctx).Successf(
			"Broker → %s (environment %q)", brokerURL, st.EnvironmentName))
	}
	if clientName == "" {
		clientName = st.IdentityName
	}
	return a.registerAndWait(ctx, st.IdentityName, st.EnvironmentName, st.BaseURL, clientName, timeout, force)
}

// applyBrokerURL sets envName's broker_url inside cfg with the semantics both
// register arms share: filling an EMPTY broker_url is a completion and always
// allowed; replacing a DIFFERENT non-empty one is a repoint and refuses
// without force (an env's broker is load-bearing for every context bound to
// it). Setting the same value again is a no-op, so re-running register stays
// idempotent. The environment must already exist.
func applyBrokerURL(cfg *sdkconfig.Config, envName, brokerURL string, force bool) error {
	env, ok := cfg.Environments[envName]
	if !ok {
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("environment %q does not exist", envName),
			Actionable: "Create it first: `jentic env add " + envName + " --url <control-plane URL> --broker-url " + brokerURL + "`.",
		}
	}
	if env.BrokerURL != "" && env.BrokerURL != brokerURL && !force {
		return &ux.CodedError{
			Code: ux.CodeMissingArgument,
			Msg: fmt.Sprintf("environment %q already has broker_url %s, not %s",
				envName, env.BrokerURL, brokerURL),
			Actionable: "Pass --force to repoint it, or drop --broker-url to keep the existing broker.",
		}
	}
	env.BrokerURL = brokerURL
	cfg.Environments[envName] = env
	return nil
}

// promptOnboarding collects the fresh-machine onboarding values, styled like
// the legacy register wizard. Environment/context names are derived (and
// overridable via flags), so the form stays small. The broker group only
// appears for a non-loopback install URL (a loopback install seeds the broker
// automatically); it is optional — leaving it blank falls through to the
// post-registration warning rather than blocking onboarding on a value the
// user may not know yet.
func promptOnboarding(installURL, name, brokerURL *string) error {
	return prompt.NewForm(
		huh.NewGroup(
			prompt.Input().Title("Jentic install URL").
				Description("The control-plane URL this agent registers with and talks to.").
				Value(installURL).Validate(notEmptyField("url")),
			prompt.Input().Title("Agent name").
				Description("Identity name, shown to the operator approving this agent.").
				Value(name).Validate(notEmptyField("name")),
		),
		huh.NewGroup(
			prompt.Input().Title("Broker URL (recommended)").
				Description("Data-plane URL `jentic execute` sends requests through — usually its own\nhost on a remote install; ask your operator. Leave blank to set later.").
				Value(brokerURL).Validate(optionalBrokerField()),
		).WithHideFunc(func() bool {
			// Evaluated when the form reaches this group, i.e. AFTER the URL was
			// typed: loopback installs seed the broker themselves, so only a
			// remote URL needs the extra question.
			u, err := url.Parse(strings.TrimSpace(*installURL))
			return err != nil || isLoopbackHost(u.Hostname())
		}),
	).WithShowHelp(true).Run()
}
