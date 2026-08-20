package ctlcmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/google/uuid"
	"github.com/jentic/jentic-one/cli/internal/cli/binder"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ctl"
	"github.com/jentic/jentic-one/cli/internal/cli/ctl/generated"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/jentic/jentic-one/cli/internal/update"
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
	proceed, enabled, err := a.ensureTelemetryConsent(consentInteractive(opts, term.IsTerminal(os.Stdin.Fd())))
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

	data, err := draft.Render()
	if err != nil {
		return err
	}

	// Additive schema-driven layer (plan.md Phase 6, impl/6.0 §3–§4): overlay the
	// resolved preset + explicit --<section>-<field> flags onto the wizard-rendered
	// config. This is a no-op unless the operator selected a preset or set at least
	// one schema flag, so a plain `install` stays byte-identical (golden contract).
	// The overlay preserves unknown keys/comments (yaml.Node merge), so an
	// enterprise-extended or hand-edited config survives.
	data, err = a.applySchemaOverlay(cmd, opts, data)
	if err != nil {
		return err
	}

	out, err := filepath.Abs(opts.out)
	if err != nil {
		return err
	}
	if dir := filepath.Dir(out); dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil { //nolint:gosec // config dir may be outside ~/.jentic by user choice
			return fmt.Errorf("create %s: %w", dir, err)
		}
	}
	// Config contains freshly generated secrets; restrict permissions. The
	// Docker path relaxes to 0644 because the file is bind-mounted into
	// containers running as the unprivileged uid 999 (#992) — host-side
	// protection comes from ~/.jentic being 0700. Only relax under the managed
	// state dir: a user-chosen --out elsewhere has no such protective parent.
	mode := os.FileMode(0o600)
	if draft.IsDocker() && strings.HasPrefix(out, a.Paths.Dir()+string(filepath.Separator)) {
		mode = 0o644
	}
	if err := os.WriteFile(out, data, mode); err != nil {
		return fmt.Errorf("write %s: %w", out, err)
	}
	// WriteFile does not chmod an existing file, and a prior install wrote it
	// 0600 — set the mode explicitly so a reinstall heals old installs too.
	if err := os.Chmod(out, mode); err != nil {
		return fmt.Errorf("chmod %s: %w", out, err)
	}
	if draft.IsDocker() && mode == 0o600 {
		fmt.Fprintln(a.Out, theme.Warnf(
			"config written outside %s with mode 0600 — the app container (uid 999) "+
				"may not be able to read it; chmod 644 %s if the stack fails to start",
			a.Paths.Dir(), out))
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

// applySchemaOverlay layers the schema-driven config (preset + explicit
// --<section>-<field> flags) onto the wizard-rendered YAML (impl/6.0 §3–§4). It
// returns rendered UNCHANGED when neither a preset nor any schema flag was
// supplied, so the default install path is byte-for-byte what it was before this
// feature — the golden CLI contract depends on that. When something is supplied,
// it resolves the defaults<preset<flags ladder and merges only the resulting
// leaves into the document via a yaml.Node overlay that preserves unknown keys
// and comments (enterprise extension sections, hand edits).
//
// NOTE the overlay deliberately does NOT re-inject schema DEFAULTS: the wizard's
// Render() already emits the values the backend needs, and blanketing every
// schema default on top would fight the wizard's own choices. Only the preset and
// the operator's explicit flags — the layers that express INTENT beyond the
// wizard — are overlaid.
func (a *app) applySchemaOverlay(cmd *cobra.Command, opts *installOptions, rendered []byte) ([]byte, error) {
	overrides, err := binder.ChangedOverrides(cmd, opts.backendCfg)
	if err != nil {
		return nil, err
	}

	// Optional schema-derived interactive config screen (impl/6.1), opt-in and
	// interactive-only so the shipped wizard flow is untouched by default. Its
	// answers become the highest-precedence override layer.
	formOverrides, err := a.runConfigForm(cmd, opts)
	if err != nil {
		return nil, err
	}

	if opts.preset == "" && len(overrides) == 0 && len(formOverrides) == 0 {
		return rendered, nil // fast path: nothing schema-driven requested
	}

	// Compose the intentful layers only (preset < flags < form), NOT schema
	// defaults — the wizard's Render() already emits the backend's needed values,
	// and blanketing every default on top would fight the wizard's own choices.
	settings := ctl.Settings{}
	if opts.preset != "" {
		p, err := ctl.PresetSettings(opts.preset)
		if err != nil {
			return nil, err
		}
		settings = p
	}
	if len(overrides) > 0 {
		ctl.MergeSettings(settings, overrides)
	}
	if len(formOverrides) > 0 {
		ctl.MergeSettings(settings, formOverrides)
	}

	merged, err := ctl.OverlaySettings(rendered, settings)
	if err != nil {
		return nil, err
	}
	fmt.Fprintln(a.Out, theme.Dimf("Applied schema-driven config overlay (preset=%q, %d flag override(s)).", opts.preset, countLeaves(overrides)+countLeaves(formOverrides)))
	return merged, nil
}

// runConfigForm shows the schema-derived interactive config screen when
// --config-form was passed and we have a TTY (impl/6.1). The form binds a fresh
// BackendConfig; the operator fills in any sections they want to override, and
// the non-sensitive, non-empty leaves are returned as a nested override map that
// joins the overlay at the highest precedence. A no-op returning nil when the
// flag is off or there is no terminal.
//
// The form is NOT pre-filled from the resolved defaults: the generated struct's
// UnmarshalJSON enforces required sections, so hydrating it from a partial
// settings map is not reliable — instead the operator sees empty inputs and only
// what they type becomes an override, which is also the least surprising
// "review/adjust" semantics (blank = leave the wizard's value alone).
func (a *app) runConfigForm(cmd *cobra.Command, opts *installOptions) (ctl.Settings, error) {
	if !opts.configForm || !wantsInteractive(cmd, false) {
		return nil, nil
	}
	sensitive, err := ctl.SensitivePaths()
	if err != nil {
		return nil, err
	}
	formCfg := &generated.BackendConfig{}
	excluded := excludeConsentOwned(sensitive, formCfg)
	form := binder.BuildDynamicForm(formCfg, binder.FormOptions{Exclude: excluded})
	if err := install.RunForm(form); err != nil {
		return nil, fmt.Errorf("config form: %w", err)
	}
	// Only leaves the operator actually set (non-zero) become overrides.
	return ctl.Settings(binder.NonZeroOverrides(formCfg, excluded)), nil
}

// excludeConsentOwned extends the sensitive-path Exclude set with every leaf of
// the telemetry section. The telemetry block is owned end-to-end by the
// dedicated consent flow (prompt → stampTelemetryDecision → render, which also
// stamps instance_id and host_os): a schema-overlay flag or form field that
// flipped `telemetry.enabled` after rendering would bypass consent and ship an
// enabled config with no instance id and no host_os stamp — under the
// recommended Docker install the backend would then misreport the container's
// OS, exactly the failure the host_os stamp exists to prevent.
func excludeConsentOwned(sensitive map[string]bool, target interface{}) map[string]bool {
	out := make(map[string]bool, len(sensitive)+4)
	for path := range sensitive {
		out[path] = true
	}
	for _, path := range binder.LeafPaths(target) {
		if path == "telemetry" || strings.HasPrefix(path, "telemetry.") {
			out[path] = true
		}
	}
	return out
}

// countLeaves returns the number of scalar leaves in a nested override map, for a
// human-friendly overlay summary line.
func countLeaves(m map[string]any) int {
	n := 0
	for _, v := range m {
		if sub, ok := v.(map[string]any); ok {
			n += countLeaves(sub)
			continue
		}
		n++
	}
	return n
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

// shouldBuildNonRelease reports whether the Docker path should build the server
// image from source because the CLI is a NON-RELEASE build (dev / main / a
// branch / a commit) and the operator has not pinned a specific image. Such a
// ref has no matching published server image, and pulling the last release
// would pair this (newer) CLI with an OLDER server; building from the same ref
// keeps the stack coherent. An explicit image pin (--image-tag /
// $JENTIC_APP_IMAGE_TAG) means "pull exactly this", so it disables the build.
func shouldBuildNonRelease(version string, imagePinned bool) bool {
	return !imagePinned && !install.IsReleaseVersion(version)
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
// ~/.jentic/config.yaml. Without this, `jentic execute` / `jentic run` fall back
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

// installDocker performs the real containerized install under ~/.jentic. By
// default it PULLS the published, signed app image
// (ghcr.io/jentic/jentic-one-app:<version>) and threads that ref into the
// generated compose stack; --build-local (or running inside a source checkout /
// $JENTIC_SRC) builds the image locally instead. It then writes the compose
// stack, applies migrations in a one-shot container, and (unless --no-start)
// brings the stack up. Mirrors installLocal for the Docker path.
func (a *app) installDocker(ctx context.Context, draft *install.Draft, configPath, logsDir string, opts *installOptions) error {
	results := install.Preflight(ctx, draft)
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderPreflight(results))
	if missing := install.Missing(results); len(missing) > 0 {
		return install.MissingError(missing)
	}
	// The docker binary is present but both paths also need a live daemon; fail
	// fast here with a "start Docker" message rather than crashing mid-build or
	// mid-pull (#653).
	if check, down := install.UnhealthyDaemon(results); down {
		return install.DaemonError(check)
	}

	// Build locally when explicitly asked (--build-local / --ref), or when a
	// source checkout is in play (cwd repo-root walk / $JENTIC_SRC) — a
	// contributor iterating on local changes should not silently run a
	// published image. Also build locally for a NON-RELEASE build (dev / main /
	// a branch / a commit) with no explicit image pin: there is no matching
	// published server image for such a ref, and pulling the last release would
	// pair this (newer) CLI with an OLDER server — so build the server from the
	// same ref the CLI came from, keeping the stack coherent. An explicit
	// --image-tag / $JENTIC_APP_IMAGE_TAG always means "pull exactly this", so
	// it still takes the pull path even for a non-release build.
	_, haveSrc := install.RepoRoot()
	imagePinned := opts.imageTag != "" || os.Getenv(install.AppImageTagEnv) != ""
	nonReleaseNeedsBuild := shouldBuildNonRelease(cmdcore.Version(), imagePinned)
	buildLocal := opts.buildLocal || haveSrc || opts.ref != "" || nonReleaseNeedsBuild

	var appImage string
	if buildLocal {
		if nonReleaseNeedsBuild && !opts.buildLocal && !haveSrc && opts.ref == "" {
			fmt.Fprintln(a.Out)
			fmt.Fprintln(a.Out, theme.Dimf(
				"note: non-release build (%s) — no published server image matches this ref, "+
					"so the server is built from source to match this CLI. "+
					"Pin a published image with --image-tag <tag> to pull instead.", cmdcore.Version()))
		}
		ref, pinned := a.resolveStackBuildRef(opts.ref)
		plan := install.PlanLocalBuild(a.Paths.VenvPath(), a.Paths.SrcPath()).AtRef(ref, pinned)
		if plan.PinnedRefIgnored() {
			return refIgnoredError(ref, plan.SourceDir)
		}
		fmt.Fprintln(a.Out)
		fmt.Fprint(a.Out, plan.RenderDockerBuildHeader())
		if err := plan.BuildImages(a.Out); err != nil {
			return fmt.Errorf("image build failed: %w", err)
		}
		if plan.FromGit {
			draft.StackRef = ref
		}
	} else {
		appImage = install.ResolveAppImage(cmdcore.Version(), opts.imageTag)
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Headingf("Pull app image"))
		fmt.Fprintln(a.Out, theme.Dimf("  image: %s", appImage))
		if err := install.PullAppImage(a.Out, appImage); err != nil {
			return err
		}
	}

	cfg := install.ComposeConfig{
		ComposePath:    a.Paths.ComposePath(),
		ConfigHostPath: configPath,
		LogsHostDir:    logsDir,
		AppImage:       appImage,
	}
	if err := install.WriteComposeArtifacts(draft, cfg); err != nil {
		return err
	}
	draft.ComposePath = cfg.ComposePath

	// Record whether the data volume exists BEFORE migrations run: the first
	// `compose run` creates (and for Postgres initdb's) it, so this is the
	// only moment "fresh install" vs "reinstall over live data" can be told
	// apart. The answer decides whether a failed first migration may discard
	// the volume (#992 item 3 — see install/recover.go).
	dataVolumes := install.DataVolumeNames(draft.IsPostgres())
	freshVolumes := true
	for _, v := range dataVolumes {
		exists, err := install.VolumeExists(v)
		if err != nil || exists {
			// "Could not tell" must count as pre-existing: guessing "fresh"
			// here would let the recovery path destroy a real database.
			freshVolumes = false
			break
		}
	}

	// Apply migrations via a one-shot app container. For Postgres the app's
	// depends_on makes compose start (and health-wait) the db automatically.
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderMigrateHeader(configPath))
	if err := install.RunComposeMigrations(a.Out, cfg.ComposePath); err != nil {
		return a.migrationFailure(cfg.ComposePath, dataVolumes, freshVolumes, err)
	}
	draft.MigrationsDone = true

	if opts.noStart {
		return nil
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, install.RenderStartHeader())
	if err := install.ComposeUp(a.Out, cfg.ComposePath); err != nil {
		// Non-fatal: the stack is built and configured; the user can bring it up
		// with `jenticctl start` from the printed next steps.
		fmt.Fprintln(a.Out, install.RenderStartWarning(err))
		return nil
	}
	draft.AppStarted = true
	fmt.Fprintln(a.Out, theme.Successf("  Stack started (compose: %s)", cfg.ComposePath))
	return nil
}

// migrationFailure turns a failed in-container migration into an actionable
// error. Historically the first failure left behind a half-initialized data
// volume that poisoned every retry (#992 item 3 — see install/recover.go).
// Fresh volumes (created by this very run) are discarded automatically so a
// re-run starts clean; pre-existing ones may hold real data, so the operator
// gets the manual reset command and a backup warning instead.
func (a *app) migrationFailure(composePath string, dataVolumes []string, fresh bool, cause error) error {
	if !fresh {
		fmt.Fprintln(a.Out, theme.Warnf(
			"The database volume pre-existed this install and was left untouched.\n"+
				"If its data matters, back it up before anything else. To discard it and\n"+
				"reinstall from scratch (DESTROYS the database), run:\n"+
				"  %s", install.ManualResetCommand(composePath)))
		return fmt.Errorf("migrations failed: %w", cause)
	}

	fmt.Fprintln(a.Out, theme.Dimf("Removing the freshly created database volume so the next attempt starts clean..."))
	if resetErr := install.ResetFreshDataVolumes(a.Out, composePath, dataVolumes); resetErr != nil {
		fmt.Fprintln(a.Out, theme.Warnf(
			"Could not remove the fresh database volume (%v).\n"+
				"Before retrying, discard it manually:\n"+
				"  %s", resetErr, install.ManualResetCommand(composePath)))
		return fmt.Errorf("migrations failed: %w", cause)
	}
	return fmt.Errorf("migrations failed: %w\n"+
		"The freshly created database volume was removed; fix the cause above and re-run `jenticctl install`", cause)
}

// startAppBackground launches the freshly installed app (and the broker, on its
// own port) in the background and records the results on the draft for the
// summary. A failure to start is non-fatal: the install is otherwise complete
// and the user can start things manually from the printed next steps.
func (a *app) startAppBackground(draft *install.Draft, configPath, logsDir string) {
	pidPath := a.Paths.AppPIDPath()
	logPath := filepath.Join(logsDir, "app.log")

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, install.RenderStartHeader())
	pid, err := install.StartApp(draft.VenvPython, configPath, logPath, pidPath)
	if err != nil {
		fmt.Fprintln(a.Out, install.RenderStartWarning(err))
		return
	}
	draft.AppStarted = true
	draft.AppPID = pid
	fmt.Fprintln(a.Out, theme.Successf("  App started (pid %d)", pid))

	// The broker runs as its own process on its dedicated port.
	brokerPID, err := install.StartBroker(
		draft.VenvPython, configPath,
		filepath.Join(logsDir, "broker.log"), a.Paths.BrokerPIDPath(), draft.BrokerPort,
	)
	if err != nil {
		fmt.Fprintln(a.Out, install.RenderStartWarning(err))
		return
	}
	draft.BrokerStarted = true
	draft.BrokerPID = brokerPID
	fmt.Fprintln(a.Out, theme.Successf("  Broker started (pid %d, port %s)", brokerPID, draft.BrokerPort))
}

func installLocal(ctx context.Context, a *app, draft *install.Draft, configPath string, freshVenv bool, explicitRef string) error {
	venvDir := a.Paths.VenvPath()
	srcDir := a.Paths.SrcPath()

	// --fresh-venv: wipe a wedged/half-populated venv from a prior failed
	// install so this build starts clean, rather than reusing a partial one
	// (P1-E). Explicit, never implicit — a normal reinstall reuses the venv.
	if freshVenv {
		if err := os.RemoveAll(venvDir); err != nil {
			return fmt.Errorf("wipe existing venv for --fresh-venv: %w", err)
		}
		fmt.Fprintln(a.Out, theme.Dim.Render("  wiped existing venv (--fresh-venv)"))
	}

	// uv drives the local build; bootstrap it when missing so onboarding does
	// not dead-end on a tool the installer can provide itself.
	install.EnsureUv(a.Out)

	// Preflight: confirm required tools are available before doing any work.
	results := install.Preflight(ctx, draft)
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderPreflight(results))
	if missing := install.Missing(results); len(missing) > 0 {
		return install.MissingError(missing)
	}

	plan := install.PlanLocalBuild(venvDir, srcDir).AtRef(a.resolveStackBuildRef(explicitRef))
	if plan.PinnedRefIgnored() {
		return refIgnoredError(plan.Ref, plan.SourceDir)
	}
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, plan.RenderHeader())

	if err := plan.Execute(a.Out); err != nil {
		// A failed build can leave a half-populated venv the next run would
		// reuse as-is. Point at the explicit clean-rebuild rather than silently
		// deleting (P1-E) — mirrors the Docker fresh-volume recovery hint.
		fmt.Fprintln(a.Out, theme.Warnf("if the venv is wedged, re-run with --fresh-venv to rebuild it clean"))
		return fmt.Errorf("build failed: %w", err)
	}
	draft.VenvPython = plan.VenvPython()
	if plan.FromGit {
		draft.StackRef = plan.Ref
	}

	// Apply migrations for real. On the SQLite path wrap them in the shared
	// snapshot/rollback net so a failed first-install migration restores the
	// pre-migration bytes instead of stranding a half-applied schema (P1-E) —
	// the same helper `update` uses. Postgres stays the non-fatal keep-install
	// path (the DB may simply not be up yet), so it does not go through the net.
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderMigrateHeader(configPath))
	if draft.IsPostgres() {
		if err := install.RunMigrations(ctx, a.Out, draft.VenvPython, configPath); err != nil {
			fmt.Fprintln(a.Out, install.RenderMigrateWarning(err))
			return nil
		}
		draft.MigrationsDone = true
		return nil
	}
	if err := update.MigrateWithRollback(
		a.Paths.DataDir(),
		true, // local install is SQLite here (Postgres handled above)
		func() error { return install.RunMigrations(ctx, a.Out, draft.VenvPython, configPath) },
		func() { fmt.Fprintln(a.Out, theme.Dim.Render("  snapshotted SQLite data for rollback")) },
		func() {
			fmt.Fprintln(a.Out, theme.Warnf("migrations failed; rolled the SQLite database back to its pre-install state"))
		},
	); err != nil {
		return err
	}
	draft.MigrationsDone = true
	return nil
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
