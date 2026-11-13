package ctlcmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/google/uuid"
	"github.com/jentic/jentic-one/cli/internal/cli/binder"
	"github.com/jentic/jentic-one/cli/internal/cli/ctl"
	"github.com/jentic/jentic-one/cli/internal/cli/ctl/generated"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

type installOptions struct {
	out          string
	skipBuild    bool
	noStart      bool
	noWizard     bool
	freshSecrets bool
	freshVenv    bool
	// preset selects an embedded config preset (impl/6.0 §3.5); empty means none
	// (schema defaults + the wizard/flags stand). The schema-driven --section-field
	// flags are bound onto the command from the generated BackendConfig and, together
	// with the preset, are overlaid onto the rendered jentic-one.yaml as an ADDITIVE
	// layer on top of the shipped wizard (plan.md Phase 6; §"schema-driven flags").
	preset     string
	backendCfg *generated.BackendConfig
	// configForm opts into the schema-derived interactive config screen (impl/6.1):
	// an extra, grouped huh form over the config sections, run after the shipped
	// wizard confirms, whose answers join the overlay. Off by default so the mature
	// wizard flow is untouched; ignored without a TTY.
	configForm bool
	// defaults / answersFile drive a HEADLESS install (impl/9.0 §9.2): the wizard
	// is a TTY-only TUI, so CI / scripted / cross-platform-E2E installs need a
	// non-interactive path. --defaults takes NewDraft() as-is; --answers overlays
	// a YAML answers file on top of the defaults. Either one skips RunWizard and
	// runs install.ValidateDraft (the same field rules the wizard enforces).
	defaults    bool
	answersFile string
	// buildLocal forces the Docker path to build the app image from a local
	// checkout instead of pulling the published image (the default). It is
	// auto-selected when a source checkout / $JENTIC_SRC / --ref is present.
	buildLocal bool
	// ref pins the git ref (branch, tag, or commit) the managed clone builds
	// from under --build-local (which it implies). Without it the build targets
	// the ref this CLI was installed from (per the install manifest), then the
	// CLI version — never the remote's default branch, which can be a different
	// generation than the CLI's compose/migration expectations.
	ref string
	// imageTag pins the app image the Docker path pulls: a bare tag, an
	// @sha256: digest, or a full ghcr.io/…@sha256:… reference (overrides the
	// CLI-version → tag mapping). Ignored under --build-local.
	imageTag string
}

// installSetupProbeTimeout bounds the post-start /health probe that resolves
// the install summary's first-admin guidance. On timeout the summary falls back
// to SetupUnknown rather than guessing.
const installSetupProbeTimeout = 5 * time.Second

func newInstallCmd(app *app) *cobra.Command {
	opts := &installOptions{backendCfg: &generated.BackendConfig{}}

	cmd := &cobra.Command{
		Use:   "install",
		Short: "Interactive wizard to configure and onboard jentic-one",
		Long: "install walks you through choosing a deployment path (from source or\n" +
			"Docker, SQLite or Postgres) and configuration, generates a jentic-one.yaml,\n" +
			"then builds the stack (local venv or Docker image), applies migrations, and\n" +
			"starts the app. Use --skip-build to only generate the config, or --no-start\n" +
			"to build without launching.\n\n" +
			"Docker path: by default this PULLS the published, signed app image\n" +
			"(ghcr.io/jentic/jentic-one-app, version-matched to the CLI) — no local build.\n" +
			"Use --build-local to build from a checkout instead (auto-selected inside a\n" +
			"jentic-one source tree or with $JENTIC_SRC), --ref to build a specific\n" +
			"branch/tag/commit, or --image-tag to pin a specific tag or @sha256: digest.\n\n" +
			"Advanced: --preset and the generated --<section>-<field> flags (e.g.\n" +
			"--server-public-base-url, --logging-file-enabled) overlay schema-driven\n" +
			"configuration onto the generated jentic-one.yaml, on top of the wizard.",
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.runInstall(cmd, opts)
		},
	}

	cmd.Flags().StringVar(&opts.out, "out", app.Paths.InstallConfigPath(),
		"path to write the generated config to")
	cmd.Flags().BoolVar(&opts.skipBuild, "skip-build", false,
		"only generate the config; don't build, migrate, or start")
	cmd.Flags().BoolVar(&opts.noStart, "no-start", false,
		"don't start the app in the background after a local install")
	cmd.Flags().BoolVar(&opts.noWizard, "no-wizard", false,
		"don't offer the guided first-run wizard after the stack starts")
	cmd.Flags().BoolVar(&opts.freshSecrets, "fresh-secrets", false,
		"rotate every generated secret instead of reusing an existing config's "+
			"(default: reuse from jentic-one.yaml or jentic-one-old.yaml so encrypted data stays readable)")
	cmd.Flags().BoolVar(&opts.freshVenv, "fresh-venv", false,
		"wipe any existing local venv before building (recovers a wedged/half-populated "+
			"~/.jentic/venv from a prior failed install; local path only)")
	cmd.Flags().StringVar(&opts.preset, "preset", "",
		"apply an embedded config preset over schema defaults ("+strings.Join(ctl.Presets(), ", ")+"); empty means none")
	cmd.Flags().BoolVar(&opts.configForm, "config-form", false,
		"after the wizard, show an extra schema-derived form to review/adjust config sections (interactive only)")
	cmd.Flags().BoolVar(&opts.defaults, "defaults", false,
		"non-interactive: skip the wizard and take its defaults (Docker + Postgres, loopback) as-is")
	cmd.Flags().StringVar(&opts.answersFile, "answers", "",
		"non-interactive: skip the wizard and take answers from a YAML file (unlisted fields keep the wizard defaults; implies --defaults for the rest)")
	cmd.Flags().BoolVar(&opts.buildLocal, "build-local", false,
		"Docker path: build the app image from a local checkout instead of pulling the "+
			"published image (auto-selected inside a jentic-one checkout, with $JENTIC_SRC, or --ref)")
	cmd.Flags().StringVar(&opts.ref, "ref", "",
		"git ref (branch/tag/commit) to build the stack from; implies --build-local "+
			"(default: the ref this CLI was installed from, then the CLI version)")
	cmd.Flags().StringVar(&opts.imageTag, "image-tag", "",
		"Docker path: pull this app image tag or @sha256: digest (or a full ghcr.io/…@sha256 ref) "+
			"instead of the version-matched tag; ignored with --build-local")

	// Schema-driven --<section>-<field> flags, generated from the backend config
	// schema (impl/6.0 §3). Additive: unset flags contribute nothing, so a plain
	// `install` is byte-identical to before. Secret-bearing leaves are EXCLUDED —
	// credentials belong in the wizard's secret-generation/.env flow, never on a
	// command line (ps/shell-history/--help exposure); the rest are registered
	// Hidden so they work yet stay out of --help and the public cli-reference noise.
	sensitive, err := ctl.SensitivePaths()
	if err != nil {
		// A corrupt embedded schema is a build-time problem; fail loud at
		// construction rather than silently binding secret flags.
		panic(fmt.Sprintf("install: cannot resolve sensitive config paths: %v", err))
	}
	binder.BindFlagsWithOptions(cmd, opts.backendCfg, binder.BindOptions{
		Exclude: excludeConsentOwned(sensitive, opts.backendCfg),
		Hidden:  true,
	})

	return cmd
}

// installHeader builds the version metadata shown in the wizard's top-right
// panel: the CLI version, plus the server version if one is already running at
// the configured base URL (probed best-effort with a short timeout).
func (a *app) installHeader() install.Header {
	baseURL := config.DefaultBaseURL
	if cfg, err := config.Load(a.Paths); err == nil {
		baseURL = cfg.ResolvedBaseURL()
	}
	info := serverinfo.Probe(baseURL, serverinfo.DefaultTimeout)
	return install.Header{
		CLIVersion:    version,
		ServerVersion: info.Version,
		ServerRunning: info.Running,
	}
}

func (a *app) runInstall(cmd *cobra.Command, opts *installOptions) error {
	draft := install.NewDraft()
	// Root all local state under ~/.jentic so SQLite databases land beside the
	// generated config and logs.
	if dataDir, err := a.Paths.Ensure(a.Paths.DataDir()); err == nil {
		draft.SQLiteDir = dataDir
	}
	// Point the app's file-log sink at the managed logs dir (absolute, so it is
	// independent of the app's working directory at start time).
	draft.LogFileDir = a.Paths.LogsDir()

	// Headless path (impl/9.0 §9.2): --defaults/--answers skip the TTY wizard.
	// --answers overlays a YAML file on top of NewDraft(); both run the same
	// field validators the wizard enforces so a headless draft can't be one the
	// wizard would have rejected.
	if opts.defaults || opts.answersFile != "" {
		if opts.answersFile != "" {
			answers, aerr := install.LoadAnswers(opts.answersFile)
			if aerr != nil {
				return aerr
			}
			answers.Apply(draft)
		}
		if verr := install.ValidateDraft(draft); verr != nil {
			return fmt.Errorf("invalid install answers: %w", verr)
		}
		return a.finishInstall(cmd, opts, draft)
	}

	confirmed, err := install.RunWizard(draft, a.installHeader())
	if err != nil {
		return err
	}
	if !confirmed {
		fmt.Fprintln(a.Out, theme.Dim.Render("install cancelled (no config written)"))
		return nil
	}

	return a.finishInstall(cmd, opts, draft)
}

// finishInstall runs everything after the deployment decision is made — whether
// that came from the interactive wizard or the headless --defaults/--answers
// path (impl/9.0 §9.2). It reuses/fills secrets, gates telemetry consent,
// renders + overlays the config, writes it, then builds/migrates/starts the
// stack and prints the summary. Splitting it out lets the headless path share
// the exact same post-decision behaviour as the wizard.
func (a *app) finishInstall(cmd *cobra.Command, opts *installOptions, draft *install.Draft) error {
	// Carry secrets over from an existing config (or its uninstall backup)
	// before FillSecrets runs, so a reinstall doesn't silently rotate the
	// encryption key underneath still-present ciphertexts. FillSecrets is
	// fill-only-empty (see install/secrets.go), so pre-seeded fields survive.
	// --fresh-secrets skips this step for deliberate rotation. Malformed
	// prior configs warn and fall through to fresh generation rather than
	// blocking install.
	if !opts.freshSecrets {
		reuseInstallSecrets(a, draft, opts.out)
	}

	if err := draft.FillSecrets(); err != nil {
		return err
	}

	// Telemetry consent gate: asked once, after the user has confirmed their
	// configuration, before the config is rendered so the decision lands in the
	// generated jentic-one.yaml (the file the app actually reads). Persisted so
	// re-installs skip it. Non-interactive (CI / no TTY) first run defaults OFF.
	// The headless --defaults/--answers path never prompts even on a TTY — its
	// contract is "no interaction", so it keeps any saved choice / defaults OFF.
	proceed, enabled, err := a.ensureTelemetryConsent(consentInteractive(opts, prompt.Promptable()))
	if err != nil {
		return err
	}
	if !proceed {
		// The user aborted the consent prompt (the gate already printed a
		// friendly cancel message). Exit non-zero so chained shell scripts see
		// the install did not complete, without an extra "error:" line.
		return &exitCodeError{Code: 1}
	}
	// Stamp the decision onto the draft so the generated config's telemetry gate
	// reflects the user's choice.
	stampTelemetryDecision(draft, enabled)

	out, err := a.writeInstallConfig(cmd, opts, draft)
	if err != nil {
		return err
	}

	// Establish the ~/.jentic/logs convention alongside config and data.
	logsDir, _ := a.Paths.Ensure(a.Paths.LogsDir())

	local := !draft.IsDocker() && !opts.skipBuild
	docker := draft.IsDocker() && !opts.skipBuild

	// Pin the banner at the top of the terminal so the build output (CA,
	// preflight, build, migrate, start) scrolls beneath it.
	var banner *install.PinnedBanner
	if local || docker {
		banner = install.StartPinnedBanner(os.Stdout)
	}

	// For the local path, perform the real install under ~/.jentic: build the
	// venv (from the local checkout if we're inside the repo, otherwise clone
	// from GitHub first) and apply migrations.
	if local {
		if err := installLocal(cmd.Context(), a, draft, out, opts.freshVenv, opts.ref); err != nil {
			banner.Stop()
			return err
		}
		// Migrations applied: optionally bring the app up in the background.
		// startAppBackground records the true outcome on draft.AppStarted (it
		// stays false if launch fails non-fatally).
		if draft.MigrationsDone && !opts.noStart {
			a.startAppBackground(draft, out, logsDir)
		}
		banner.Stop()
	}

	// For the Docker path, build the app image, write the compose stack, migrate
	// in a one-shot container, and (unless --no-start) bring the stack up.
	// installDocker records the true outcome on draft.AppStarted (a failed
	// `compose up` is non-fatal and leaves it false).
	if docker {
		if err := a.installDocker(cmd.Context(), draft, out, logsDir, opts); err != nil {
			banner.Stop()
			return err
		}
		banner.Stop()
	}

	a.recordManifest(draft)
	a.writeCLIConfig(draft)

	// Probe the live stack so the summary's first-admin guidance matches the
	// real DB state. A re-install over a database that uninstall left behind
	// already has an admin, so unconditionally printing "no users exist yet"
	// would contradict the wizard's own check moments later. Use the real
	// startup outcome (draft.AppStarted), not install intent — a non-fatal
	// `compose up` / local-launch failure must not claim the stack is up.
	setup := a.resolveSetupState(draft.AppStarted, draft.BaseURL())

	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderSummary(draft, out, draft.SQLiteDir, logsDir, setup))

	// Flow straight into the guided first-run wizard so install → first admin →
	// agent is one continuous experience. Only when the stack is actually up and
	// we have an interactive terminal; --no-wizard or a non-TTY (CI) falls back
	// to the printed next-steps the summary already shows.
	a.offerWizard(cmd, opts, draft.AppStarted)
	return nil
}

// resolveSetupState probes the freshly started stack to learn whether it still
// needs its first admin account, mapping the live /health signal onto the
// install summary's tri-state. When the stack was not started (or the probe
// fails / never resolves the signal) it returns SetupUnknown so the summary
// falls back to the generic first-run guidance rather than asserting a state it
// cannot verify.
func (a *app) resolveSetupState(started bool, baseURL string) install.SetupState {
	if !started {
		return install.SetupUnknown
	}
	ctx, cancel := context.WithTimeout(context.Background(), installSetupProbeTimeout)
	defer cancel()
	required, err := setupRequired(ctx, baseURL)
	if err != nil {
		return install.SetupUnknown
	}
	if required {
		return install.SetupRequired
	}
	return install.SetupComplete
}

// offerWizard prompts the operator to continue into `jenticctl wizard` after a
// successful install. It is a no-op unless the stack started, the user did not
// pass --no-wizard, and we have a real terminal to prompt and drive the wizard.
func (a *app) offerWizard(cmd *cobra.Command, opts *installOptions, started bool) {
	// A headless install (--defaults/--answers) asked for no prompts; don't
	// end an unattended run with one, even when a TTY happens to be attached.
	headless := opts.defaults || opts.answersFile != ""
	if opts.noWizard || headless || !started || !wantsInteractive(cmd, false) {
		return
	}

	cont := true
	if err := install.RunConfirm(huh.NewConfirm().
		Title("Continue to guided setup?").
		Description("Creates your first admin account, connects your AI operator, and gets you to a first call.").
		Affirmative("Yes, guide me").
		Negative("I'll do it myself").
		Value(&cont)); err != nil || !cont {
		fmt.Fprintln(a.Out, theme.Dim.Render("Skipping the wizard. Run `jenticctl wizard` whenever you're ready."))
		return
	}

	baseURL := config.DefaultBaseURL
	if cfg, err := config.Load(a.Paths); err == nil {
		baseURL = cfg.ResolvedBaseURL()
	}
	wopts := &wizardOptions{baseURL: baseURL, timeout: 15 * time.Minute}
	if err := a.wizardE(cmd.Context(), wopts); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("wizard: %v", err))
		fmt.Fprintln(a.Out, theme.Dim.Render("Re-run it any time with `jenticctl wizard`."))
	}
}

// resolveStackBuildRef resolves which git ref a --build-local managed-clone
// build should target. An explicit --ref wins (pinned). Otherwise fall back to
// the ref this CLI was installed from (the install manifest, written by
// tools/install.sh), then the CLI version — the same chain `update` uses. The
// fallback is what keeps a from-source install coherent: without it the
// managed clone hard-resets to the remote's default branch, which can be a
// different generation than this CLI's compose/migration expectations (e.g. a
// compose without the schema-bootstrap init script driving an app image whose
// migrations still require it).
func (a *app) resolveStackBuildRef(explicit string) (ref string, pinned bool) {
	if explicit != "" {
		return explicit, true
	}
	if m, _, err := config.LoadManifest(a.Paths); err == nil && m.Ref != "" {
		return m.Ref, false
	}
	return defaultRef(version), false
}

// refIgnoredError explains why a pinned --ref cannot be honoured when the
// build reads a local checkout: syncing the operator's working tree to a ref
// would clobber their work, so refuse rather than build something other than
// what was asked for. Mirrors `update --ref`'s refusal.
func refIgnoredError(ref, sourceDir string) error {
	return fmt.Errorf("--ref %s cannot be applied: the stack builds from the local checkout at %s, "+
		"so the ref would be ignored. Check out %s there yourself and re-run without --ref, "+
		"or unset %s to build from the managed clone", ref, sourceDir, ref, install.SrcEnv)
}

// recordManifest persists what was installed (deploy mode, db, and the CLI's
// own ref/commit/version) so `jenticctl update` knows what to track and how to
// refresh it. A failure here is non-fatal: the install succeeded regardless.
func (a *app) recordManifest(draft *install.Draft) {
	mode := config.ModeLocal
	if draft.IsDocker() {
		mode = config.ModeDocker
	}
	db := "sqlite"
	if draft.IsPostgres() {
		db = "postgres"
	}

	m, _, err := config.LoadManifest(a.Paths)
	if err != nil {
		m = &config.Manifest{}
	}
	if m.BinaryPath == "" {
		if exe, err := os.Executable(); err == nil {
			// Record the real file, not a PATH symlink to it (e.g. Homebrew's
			// bin link), so later consumers of BinaryPath see the actual
			// install location.
			if resolved, err := filepath.EvalSymlinks(exe); err == nil {
				exe = resolved
			}
			m.BinaryPath = exe
		}
	}
	// Record the ref the stack was really built from (a managed-clone
	// build-local install), so the next `install`/`update` keeps tracking it
	// rather than snapping back to the CLI version. Pull-path and
	// local-checkout installs keep recording the CLI version as before.
	stackRef := firstNonEmpty(draft.StackRef, version)
	if err := m.MergeStack(a.Paths, mode, db, draft.BrokerPort, stackRef, commit, version); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("warning: could not record install manifest: %v", err))
	}
}

// writeCLIConfig points the `jentic` CLI at the freshly installed local stack by
// persisting the control-plane base URL and the local broker target into
// ~/.jentic/config.yaml. Without this, `jentic execute` and friends fall back
// to the built-in defaults (https://127.0.0.1:8100), which may not match this
// install's broker scheme/port. Existing values are preserved (so a re-install or a
// hand-edited config is not clobbered); only unset fields are filled in. A
// failure here is non-fatal: the stack is installed regardless, and the user can
// set these by hand.
func (a *app) writeCLIConfig(draft *install.Draft) {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("warning: could not read CLI config: %v", err))
		return
	}

	changed := false
	if cfg.BaseURL == "" {
		cfg.BaseURL = draft.BaseURL()
		changed = true
	}
	// The local broker is plain HTTP on its own port, reachable on loopback.
	if cfg.Broker.Scheme == "" {
		cfg.Broker.Scheme = "http"
		changed = true
	}
	if cfg.Broker.Host == "" {
		port := draft.BrokerPort
		if port == "" {
			port = install.DefaultBrokerPort
		}
		cfg.Broker.Host = "127.0.0.1:" + port
		changed = true
	}

	if !changed {
		return
	}
	if err := cfg.Save(a.Paths); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("warning: could not write CLI config: %v", err))
		return
	}
	fmt.Fprintln(a.Out, theme.Dimf("Pointed the jentic CLI at the local broker (%s://%s).", cfg.Broker.Scheme, cfg.Broker.Host))
}

// stampTelemetryDecision records the consent decision on the draft. An
// opted-in install gets a stable opaque instance id (seeds the durable
// admin-DB identity row on first boot); an id pre-seeded from a prior config
// (reuseInstallSecrets) is kept so re-consenting preserves the same telemetry
// identity — its stability contract. An opted-out install writes an explicit
// `enabled: false` and CLEARS any pre-seeded id: the user declined, so the
// identifier they declined under must not be written back to disk.
func stampTelemetryDecision(draft *install.Draft, enabled bool) {
	draft.TelemetryEnabled = enabled
	if !enabled {
		draft.TelemetryInstanceID = ""
		return
	}
	if draft.TelemetryInstanceID == "" {
		draft.TelemetryInstanceID = uuid.NewString()
	}
}

// consentInteractive reports whether the telemetry consent prompt may render.
// The headless --defaults/--answers path must never prompt (its contract is
// "non-interactive", and a TTY-attached `install --defaults` would otherwise
// hang on the confirm); outside it, prompt only when stdin is a real TTY.
func consentInteractive(opts *installOptions, stdinIsTTY bool) bool {
	headless := opts.defaults || opts.answersFile != ""
	return !headless && stdinIsTTY
}

// reuseInstallSecrets pre-seeds draft with the secret fields from an existing
// jentic-one.yaml (or its uninstall backup) so a reinstall over live data
// keeps stored ciphertexts readable. Best-effort by design: a missing file
// is a silent no-op (fresh install); a malformed file warns and falls
// through so an aborted prior install can't block this one. The out param
// is the wizard's target config path; we resolve the backup next to it so a
// non-default --out still reuses when the operator has moved things.
func reuseInstallSecrets(a *app, draft *install.Draft, out string) {
	candidates := []string{out, config.BackupNextTo(out)}
	for _, path := range candidates {
		if path == "" {
			continue
		}
		reused, err := install.ReuseSecrets(draft, path)
		if err != nil {
			fmt.Fprintln(a.Out, theme.Warnf("could not read prior config %s (continuing with fresh secrets): %v", path, err))
			continue
		}
		if reused {
			fmt.Fprintln(a.Out, theme.Dimf(
				"Reusing secrets from %s so existing encrypted data stays readable "+
					"(use --fresh-secrets to rotate instead).", path))
			return
		}
	}
}
