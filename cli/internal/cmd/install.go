package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/google/uuid"
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
}

// installSetupProbeTimeout bounds the post-start /health probe that resolves
// the install summary's first-admin guidance. On timeout the summary falls back
// to SetupUnknown rather than guessing.
const installSetupProbeTimeout = 5 * time.Second

func newInstallCmd(app *App) *cobra.Command {
	opts := &installOptions{}

	cmd := &cobra.Command{
		Use:   "install",
		Short: "Interactive wizard to configure and onboard jentic-one",
		Long: "install walks you through choosing a deployment path (from source or\n" +
			"Docker, SQLite or Postgres) and configuration, generates a jentic-one.yaml,\n" +
			"then builds the stack (local venv or Docker image), applies migrations, and\n" +
			"starts the app. Use --skip-build to only generate the config, or --no-start\n" +
			"to build without launching.",
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

	return cmd
}

// installHeader builds the version metadata shown in the wizard's top-right
// panel: the CLI version, plus the server version if one is already running at
// the configured base URL (probed best-effort with a short timeout).
func (a *App) installHeader() install.Header {
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

func (a *App) runInstall(cmd *cobra.Command, opts *installOptions) error {
	draft := install.NewDraft()
	// Root all local state under ~/.jentic so SQLite databases land beside the
	// generated config and logs.
	if dataDir, err := a.Paths.Ensure(a.Paths.DataDir()); err == nil {
		draft.SQLiteDir = dataDir
	}
	// Point the app's file-log sink at the managed logs dir (absolute, so it is
	// independent of the app's working directory at start time).
	draft.LogFileDir = a.Paths.LogsDir()

	confirmed, err := install.RunWizard(draft, a.installHeader())
	if err != nil {
		return err
	}
	if !confirmed {
		fmt.Fprintln(a.Out, theme.Dim.Render("install cancelled (no config written)"))
		return nil
	}

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
	proceed, enabled, err := a.ensureTelemetryConsent(term.IsTerminal(os.Stdin.Fd()))
	if err != nil {
		return err
	}
	if !proceed {
		// The user aborted the consent prompt (the gate already printed a
		// friendly cancel message). Exit non-zero so chained shell scripts see
		// the install did not complete, without an extra "error:" line.
		return &exitCodeError{code: 1}
	}
	// Stamp the decision onto the draft so the generated config's telemetry gate
	// reflects the user's choice. An opted-in install gets a stable opaque
	// instance id (seeds the durable admin-DB identity row on first boot); an
	// opted-out install writes an explicit `enabled: false` (never a leftover id).
	draft.TelemetryEnabled = enabled
	if enabled {
		draft.TelemetryInstanceID = uuid.NewString()
	}

	data, err := draft.Render()
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
	// Config contains freshly generated secrets; restrict permissions.
	if err := os.WriteFile(out, data, 0o600); err != nil {
		return fmt.Errorf("write %s: %w", out, err)
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
		if err := installLocal(a, draft, out); err != nil {
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
		if err := a.installDocker(draft, out, logsDir, opts.noStart); err != nil {
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
func (a *App) resolveSetupState(started bool, baseURL string) install.SetupState {
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
func (a *App) offerWizard(cmd *cobra.Command, opts *installOptions, started bool) {
	if opts.noWizard || !started || !wantsInteractive(cmd, false) {
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

// recordManifest persists what was installed (deploy mode, db, and the CLI's
// own ref/commit/version) so `jenticctl update` knows what to track and how to
// refresh it. A failure here is non-fatal: the install succeeded regardless.
func (a *App) recordManifest(draft *install.Draft) {
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
			m.BinaryPath = exe
		}
	}
	if err := m.MergeStack(a.Paths, mode, db, draft.BrokerPort, version, commit, version); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("warning: could not record install manifest: %v", err))
	}
}

// writeCLIConfig points the `jentic` CLI at the freshly installed local stack by
// persisting the control-plane base URL and the local broker target into
// ~/.jentic/config.yaml. Without this, `jentic execute` / `jentic run` fall back
// to the built-in cloud defaults (https://broker.jentic.ai) and every brokered
// call leaves the machine. Existing values are preserved (so a re-install or a
// hand-edited config is not clobbered); only unset fields are filled in. A
// failure here is non-fatal: the stack is installed regardless, and the user can
// set these by hand.
func (a *App) writeCLIConfig(draft *install.Draft) {
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

// installDocker performs the real containerized install under ~/.jentic: build
// the combined app image (from the local checkout or a fresh clone), write the
// generated docker-compose stack, apply migrations in a one-shot container, and
// optionally bring the stack up. Mirrors installLocal for the Docker path.
func (a *App) installDocker(draft *install.Draft, configPath, logsDir string, noStart bool) error {
	results := install.Preflight(draft)
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderPreflight(results))
	if missing := install.Missing(results); len(missing) > 0 {
		return install.MissingError(missing)
	}
	// The docker binary is present but the build path also needs a live daemon;
	// fail fast here with a "start Docker" message rather than crashing mid-build
	// (#653).
	if check, down := install.UnhealthyDaemon(results); down {
		return install.DaemonError(check)
	}

	plan := install.PlanLocalBuild(a.Paths.VenvPath(), a.Paths.SrcPath())
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, plan.RenderDockerBuildHeader())
	if err := plan.BuildImages(a.Out); err != nil {
		return fmt.Errorf("image build failed: %w", err)
	}

	cfg := install.ComposeConfig{
		ComposePath:    a.Paths.ComposePath(),
		ConfigHostPath: configPath,
		LogsHostDir:    logsDir,
	}
	if err := install.WriteComposeArtifacts(draft, cfg); err != nil {
		return err
	}
	draft.ComposePath = cfg.ComposePath

	// Apply migrations via a one-shot app container. For Postgres the app's
	// depends_on makes compose start (and health-wait) the db automatically.
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderMigrateHeader(configPath))
	if err := install.RunComposeMigrations(a.Out, cfg.ComposePath); err != nil {
		return fmt.Errorf("migrations failed: %w", err)
	}
	draft.MigrationsDone = true

	if noStart {
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

// startAppBackground launches the freshly installed app (and the broker, on its
// own port) in the background and records the results on the draft for the
// summary. A failure to start is non-fatal: the install is otherwise complete
// and the user can start things manually from the printed next steps.
func (a *App) startAppBackground(draft *install.Draft, configPath, logsDir string) {
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

func installLocal(a *App, draft *install.Draft, configPath string) error {
	venvDir := a.Paths.VenvPath()
	srcDir := a.Paths.SrcPath()

	// uv drives the local build; bootstrap it when missing so onboarding does
	// not dead-end on a tool the installer can provide itself.
	install.EnsureUv(a.Out)

	// Preflight: confirm required tools are available before doing any work.
	results := install.Preflight(draft)
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderPreflight(results))
	if missing := install.Missing(results); len(missing) > 0 {
		return install.MissingError(missing)
	}

	plan := install.PlanLocalBuild(venvDir, srcDir)
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, plan.RenderHeader())

	if err := plan.Execute(a.Out); err != nil {
		return fmt.Errorf("build failed: %w", err)
	}
	draft.VenvPython = plan.VenvPython()

	// Apply migrations for real.
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderMigrateHeader(configPath))
	if err := install.RunMigrations(a.Out, draft.VenvPython, configPath); err != nil {
		// Postgres may simply not be running yet — keep the install (config + venv
		// are valid) and leave the migrate command in the printed next steps.
		if draft.IsPostgres() {
			fmt.Fprintln(a.Out, install.RenderMigrateWarning(err))
			return nil
		}
		return fmt.Errorf("migrations failed: %w", err)
	}
	draft.MigrationsDone = true
	return nil
}

// reuseInstallSecrets pre-seeds draft with the secret fields from an existing
// jentic-one.yaml (or its uninstall backup) so a reinstall over live data
// keeps stored ciphertexts readable. Best-effort by design: a missing file
// is a silent no-op (fresh install); a malformed file warns and falls
// through so an aborted prior install can't block this one. The out param
// is the wizard's target config path; we resolve the backup next to it so a
// non-default --out still reuses when the operator has moved things.
func reuseInstallSecrets(a *App, draft *install.Draft, out string) {
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
