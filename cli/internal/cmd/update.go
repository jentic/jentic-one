package cmd

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/jentic/jentic-one/cli/internal/update"
	"github.com/spf13/cobra"
)

type updateOptions struct {
	ref       string
	baseURL   string
	check     bool
	cliOnly   bool
	stackOnly bool
	yes       bool
}

func newUpdateCmd(app *App) *cobra.Command {
	opts := &updateOptions{}
	cmd := &cobra.Command{
		Use:   "update",
		Short: "Update the jentic CLIs (and check the stack) to the latest release",
		Long: "update reports the installed CLI and server versions, compares the\n" +
			"installed version against the latest release tag on GitHub, and (unless\n" +
			"--check) rebuilds and replaces the jenticctl and jentic binaries in place,\n" +
			"then rebuilds the installed stack. Use --cli-only or --stack-only to update\n" +
			"just one half.\n\n" +
			"A Homebrew-installed CLI is never swapped in place: update it with\n" +
			"`brew upgrade jentic` (a combined update then refreshes only the stack,\n" +
			"and --cli-only fails).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.updateE(cmd.Context(), opts)
		},
	}
	cmd.Flags().BoolVar(&opts.check, "check", false, "only report status; don't apply any update")
	cmd.Flags().BoolVar(&opts.cliOnly, "cli-only", false, "update only the CLI binary")
	cmd.Flags().BoolVar(&opts.stackOnly, "stack-only", false, "update only the stack (not the CLI binary)")
	cmd.Flags().BoolVar(&opts.yes, "yes", false, "skip the confirmation prompt")
	cmd.Flags().StringVar(&opts.ref, "ref", "", "git ref to update to, pinning a specific tag/branch/commit (default: the latest release tag)")
	cmd.Flags().StringVar(&opts.baseURL, "base-url", "", "Jentic control-plane base URL (for the server probe)")
	return cmd
}

func (a *App) updateE(ctx context.Context, opts *updateOptions) error {
	manifest, found, err := config.LoadManifest(a.Paths)
	if err != nil {
		return err
	}

	repo := manifest.ResolvedRepo()
	installed := firstNonEmpty(manifest.Commit, commit)
	cliVersion := firstNonEmpty(manifest.CLIVersion, version)

	ctlTarget, err := resolveCtlTarget(manifest)
	if err != nil {
		return err
	}
	brewManaged := update.BrewManaged(ctlTarget)

	fmt.Fprint(a.Out, a.brandHeader(opts.baseURL, cliVersion))
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Field("cli", cliLine(cliVersion, installed)))
	if !found {
		fmt.Fprintln(a.Out, theme.Dim.Render("  (no install manifest; using build-time metadata)"))
	}
	if brewManaged {
		fmt.Fprintln(a.Out, theme.Field("managed by", "Homebrew — update the CLI with `"+update.BrewUpgradeCommand+"`"))
	}

	// Resolve the update target. By default we track the latest release tag; an
	// explicit --ref pins a specific tag/branch/commit instead.
	latest, latestErr := update.LatestReleaseTag(ctx, repo, os.Getenv("GITHUB_TOKEN"))
	latestKnown := latestErr == nil

	ref := opts.ref
	pinned := ref != ""
	if !pinned && latestKnown {
		ref = latest
	}
	if ref == "" {
		ref = firstNonEmpty(manifest.Ref, defaultRef(version))
	}
	fmt.Fprintln(a.Out, theme.Field("tracking", repo+"@"+ref))

	if latestKnown {
		fmt.Fprintln(a.Out, theme.Field("latest", latest))
		a.printVerdict(cliVersion, latest)
	} else {
		fmt.Fprintln(a.Out, theme.Field("latest", "unknown"))
		fmt.Fprintln(a.Out, theme.Warnf("  %v", latestErr))
	}

	if opts.check {
		return nil
	}

	doCLI := !opts.stackOnly
	doStack := !opts.cliOnly

	// A brew-managed CLI is never swapped by us: brew's bookkeeping would
	// desync and the next `brew upgrade` would clobber the swapped binaries.
	// The stack (in ~/.jentic) is ours regardless of how the CLI arrived, so a
	// combined update degrades to stack-only rather than failing.
	if brewManaged && doCLI {
		if opts.cliOnly {
			return fmt.Errorf("this CLI is managed by Homebrew; update it with `%s`", update.BrewUpgradeCommand)
		}
		doCLI = false
	}

	// When the latest release is not newer than what's installed there's
	// nothing to rebuild. Each requested half is gated on its own recorded
	// version (see updateNeeded): they normally move in lockstep, but a
	// brew-managed CLI is refreshed out-of-band by `brew upgrade` while the
	// stack may lag behind, so the stack half must not key off the CLI binary.
	// A --ref override always proceeds (the user asked for a specific build);
	// re-run with --ref to force a rebuild at a pinned version.
	stackVersion := firstNonEmpty(manifest.Ref, cliVersion)
	if !pinned && latestKnown && !updateNeeded(doCLI, doStack, cliVersion, stackVersion, latest) {
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Successf("Already up to date (%s); nothing to rebuild.", latest))
		return nil
	}

	// Announce the brew degrade only when something will actually run; a
	// brew-managed install that is already up to date returns above without a
	// spurious "skipping" warning.
	if brewManaged && !opts.stackOnly {
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Warnf("CLI is managed by Homebrew — skipping the CLI update; run `%s` instead.", update.BrewUpgradeCommand))
	}

	if !opts.yes {
		ok, err := confirmApply(doCLI, doStack, repo, ref)
		if err != nil {
			return err
		}
		if !ok {
			fmt.Fprintln(a.Out, theme.Dim.Render("update cancelled"))
			return nil
		}
	}

	if doCLI {
		if err := a.updateCLI(ctx, repo, ref, ctlTarget); err != nil {
			return err
		}
	}
	if doStack {
		if err := a.updateStack(manifest.Mode); err != nil {
			return err
		}
	}
	return nil
}

// updateNeeded reports whether any requested update half is behind latest.
// Each half is compared against its own recorded version: cliVersion is what
// the installed binary reports, stackVersion is the ref the stack was last
// built from (the two normally match, but a Homebrew-managed CLI is updated
// out-of-band while the stack in ~/.jentic may lag behind).
func updateNeeded(doCLI, doStack bool, cliVersion, stackVersion, latest string) bool {
	return (doCLI && update.NewerAvailable(cliVersion, latest)) ||
		(doStack && update.NewerAvailable(stackVersion, latest))
}

// resolveCtlTarget locates the installed jenticctl binary that an update would
// replace: the manifest's recorded path when present, else the running
// executable. Symlinks are resolved in both cases so the swap replaces the real
// file rather than a PATH symlink pointing at it (e.g. Homebrew's bin link or
// the link tools/install.sh drops into /usr/local/bin) — renaming over the
// symlink would orphan the real install.
func resolveCtlTarget(manifest *config.Manifest) (string, error) {
	target := manifest.BinaryPath
	if target == "" {
		exe, err := os.Executable()
		if err != nil {
			return "", fmt.Errorf("locate current binary: %w", err)
		}
		target = exe
	}
	// A dangling/missing path fails EvalSymlinks; keep it verbatim and let the
	// swap create it fresh.
	if resolved, err := filepath.EvalSymlinks(target); err == nil {
		target = resolved
	}
	return target, nil
}

// updateCLI rebuilds and replaces both CLI binaries (jenticctl and jentic) by
// delegating the build to tools/install.sh (single source of truth) into a
// staging dir, then atomically swapping each into place with a .bak rollback
// copy. jenticctl update is the sole updater for both binaries; they are
// assumed co-located (install.sh installs both into the same dir). ctlTarget
// is the symlink-resolved jenticctl location (see resolveCtlTarget); callers
// must have already ruled out package-manager-owned locations.
func (a *App) updateCLI(ctx context.Context, repo, ref, ctlTarget string) error {
	// The sibling jentic binary lives next to jenticctl (install.sh co-locates
	// both in JENTIC_INSTALL_DIR).
	installDir := filepath.Dir(ctlTarget)

	staged, cleanup, err := a.stageCLIBuild(ctx, repo, ref)
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		return err
	}

	newVersion, err := binaryVersion(staged[ctlBinaryName])
	if err != nil {
		return fmt.Errorf("verify built binary: %w", err)
	}

	// Swap each staged binary over its installed counterpart. jenticctl is
	// resolved from the manifest/exe; jentic is co-located in the same dir.
	targets := map[string]string{
		ctlBinaryName: ctlTarget,
		apiBinaryName: filepath.Join(installDir, apiBinaryName),
	}

	fmt.Fprintln(a.Out)
	var swapped []string
	for _, name := range []string{ctlBinaryName, apiBinaryName} {
		src, ok := staged[name]
		if !ok {
			continue
		}
		target := targets[name]
		backup, err := update.ReplaceBinary(target, src)
		if err != nil {
			return fmt.Errorf("replace %s: %w", name, err)
		}
		swapped = append(swapped, name)
		fmt.Fprintln(a.Out, theme.Field(name, target))
		if backup != "" {
			fmt.Fprintln(a.Out, theme.Dimf("  previous %s backed up at %s", name, backup))
		}
	}

	a.refreshManifestBinaryPath(ctlTarget)

	fmt.Fprintln(a.Out, theme.Successf("Updated %s -> %s", strings.Join(swapped, " + "), strings.TrimSpace(newVersion)))
	return nil
}

// ctlBinaryName and apiBinaryName are the two binaries this CLI ships as.
const (
	ctlBinaryName = "jenticctl"
	apiBinaryName = "jentic"
)

// stageCLIBuild downloads tools/install.sh for ref and runs it, installing the
// freshly built binaries into a temp staging dir (not over the running
// binaries). It returns a map of binary name -> staged path and a cleanup func
// that removes the stage.
func (a *App) stageCLIBuild(ctx context.Context, repo, ref string) (map[string]string, func(), error) {
	token := os.Getenv("GITHUB_TOKEN")

	script, err := update.FetchInstaller(ctx, repo, ref, token)
	if err != nil {
		return nil, nil, err
	}

	stage, err := os.MkdirTemp("", "jentic-update-*")
	if err != nil {
		return nil, nil, err
	}
	cleanup := func() { _ = os.RemoveAll(stage) }

	scriptPath := filepath.Join(stage, "install.sh")
	if err := os.WriteFile(scriptPath, script, 0o700); err != nil { //nolint:gosec // executable installer we just fetched.
		return nil, cleanup, err
	}

	stageBin := filepath.Join(stage, "bin")
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Heading.Render("Building CLIs"))
	fmt.Fprintln(a.Out, theme.Dimf("  source: %s@%s", repo, ref))

	cmd := exec.CommandContext(ctx, "bash", scriptPath) //nolint:gosec // scriptPath is the installer we just fetched and wrote to a temp dir; running it is the point of `jenticctl update`.
	// Install into the stage; putting stageBin on PATH makes install.sh treat its
	// install dir as already-on-PATH, so it won't symlink the temp path into
	// /usr/local/bin (which we're about to delete).
	cmd.Env = append(os.Environ(),
		"JENTIC_INSTALL_DIR="+stageBin,
		"JENTIC_REPO="+repo,
		"JENTIC_REF="+ref,
		"JENTIC_NO_INSTALL=1",
		"PATH="+stageBin+string(os.PathListSeparator)+os.Getenv("PATH"),
	)
	cmd.Stdout = a.Out
	cmd.Stderr = a.Err
	if err := cmd.Run(); err != nil {
		return nil, cleanup, fmt.Errorf("build via installer failed: %w", err)
	}

	staged := map[string]string{}
	for _, name := range []string{ctlBinaryName, apiBinaryName} {
		path := filepath.Join(stageBin, name)
		if _, err := os.Stat(path); err != nil {
			return nil, cleanup, fmt.Errorf("installer did not produce %s", path)
		}
		staged[name] = path
	}
	return staged, cleanup, nil
}

// refreshManifestBinaryPath corrects the binary path in the manifest after a
// swap. The staged install.sh run rewrote install.json with the (now-correct)
// ref/commit but a stage-relative binary path; point it back at the real
// install location. Best-effort: a failure does not undo the update.
func (a *App) refreshManifestBinaryPath(target string) {
	m, _, err := config.LoadManifest(a.Paths)
	if err != nil {
		return
	}
	m.BinaryPath = target
	_ = m.Save(a.Paths)
}

// binaryVersion runs `<path> --version` and returns its first output line.
func binaryVersion(path string) (string, error) {
	out, err := exec.Command(path, "--version").Output() //nolint:gosec // path is a CLI-internal staged build artifact we just wrote to a temp dir, not user input.
	if err != nil {
		return "", err
	}
	line := strings.SplitN(strings.TrimSpace(string(out)), "\n", 2)[0]
	return line, nil
}

// updateStack rebuilds and restarts the installed server in place, reusing the
// existing jentic-one.yaml (no wizard). It dispatches on the recorded deploy
// mode; an empty/unknown mode is treated as a local install.
func (a *App) updateStack(mode string) error {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("Stack update runs forward-only migrations — back up your data first"))
	fmt.Fprintln(a.Out, theme.Dim.Render("  SQLite: copy ~/.jentic/data/*.db · Postgres: pg_dump your database"))

	if mode == config.ModeDocker {
		return a.updateStackDocker()
	}
	return a.updateStackLocal()
}

// updateStackLocal pulls the source, reinstalls into the existing venv, applies
// migrations, and restarts the app if it was running.
func (a *App) updateStackLocal() error {
	configPath := a.Paths.InstallConfigPath()
	if !proc.FileExists(configPath) {
		return fmt.Errorf("not configured: %s not found — run `jenticctl install` first", configPath)
	}

	install.EnsureUv(a.Out)
	plan := install.PlanLocalBuild(a.Paths.VenvPath(), a.Paths.SrcPath())
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, plan.RenderHeader())
	if err := plan.Execute(a.Out); err != nil {
		return fmt.Errorf("rebuild failed: %w", err)
	}

	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderMigrateHeader(configPath))
	if err := install.RunMigrations(a.Out, plan.VenvPython(), configPath); err != nil {
		return fmt.Errorf("migrations failed: %w", err)
	}

	a.restartLocalIfRunning()
	fmt.Fprintln(a.Out, theme.Successf("Stack updated (local)."))
	return nil
}

// updateStackDocker rebuilds the app image, applies migrations in a one-shot
// container, and recreates the running stack with the new image.
func (a *App) updateStackDocker() error {
	composePath := a.Paths.ComposePath()
	if !proc.FileExists(composePath) {
		return fmt.Errorf("no compose stack at %s — run `jenticctl install` first", composePath)
	}

	plan := install.PlanLocalBuild(a.Paths.VenvPath(), a.Paths.SrcPath())
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, plan.RenderDockerBuildHeader())
	if err := plan.BuildImages(a.Out); err != nil {
		return fmt.Errorf("image build failed: %w", err)
	}

	configPath := a.Paths.InstallConfigPath()
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderMigrateHeader(configPath))
	if err := install.RunComposeMigrations(a.Out, composePath); err != nil {
		return fmt.Errorf("migrations failed: %w", err)
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, install.RenderStartHeader())
	if err := install.ComposeUp(a.Out, composePath); err != nil {
		return fmt.Errorf("docker compose up: %w", err)
	}
	fmt.Fprintln(a.Out, theme.Successf("Stack updated (docker)."))
	return nil
}

// restartLocalIfRunning bounces the background app so the rebuilt code takes
// effect, but only when it was already running; otherwise it leaves it stopped.
func (a *App) restartLocalIfRunning() {
	_, running, _ := proc.LivePID(a.Paths.AppPIDPath())
	if !running {
		fmt.Fprintln(a.Out, theme.Dim.Render("  app not running — start it with `jenticctl start`"))
		return
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Infof("Restarting app ..."))
	_ = a.stopE(&stopOptions{timeout: 10 * time.Second})
	_ = a.startE(&startOptions{})
}

func confirmApply(doCLI, doStack bool, repo, ref string) (bool, error) {
	var what string
	switch {
	case doCLI && doStack:
		what = "the CLI and the stack"
	case doCLI:
		what = "the CLI"
	default:
		what = "the stack"
	}
	// This prompt is only reached when an update is available, so default the
	// focused selection to "Yes, update": the user already invoked `update`, so
	// a reflexive Enter should proceed rather than cancel (#765).
	confirm := true
	if err := install.RunConfirm(
		huh.NewConfirm().
			Title(fmt.Sprintf("Update %s to %s@%s?", what, repo, ref)).
			Affirmative("Yes, update").
			Negative("Cancel").
			Value(&confirm),
	); err != nil {
		if errors.Is(err, huh.ErrUserAborted) {
			return false, nil
		}
		return false, err
	}
	return confirm, nil
}

// printVerdict reports up-to-date / update-available based on the installed CLI
// version and the latest release tag, compared as semver.
func (a *App) printVerdict(installed, latest string) {
	switch {
	case installed == "" || installed == "none":
		fmt.Fprintln(a.Out, theme.Warn.Render("Installed version is unknown; cannot compare. Latest is "+latest+"."))
	case update.NewerAvailable(installed, latest):
		fmt.Fprintln(a.Out, theme.Accent.Render(fmt.Sprintf("Update available: %s → %s", installed, latest)))
	default:
		fmt.Fprintln(a.Out, theme.Successf("Up to date (%s).", latest))
	}
}

// cliLine formats the CLI version with its commit, e.g. "feat/cli (4ee3bd3)".
func cliLine(cliVersion, commitSHA string) string {
	if commitSHA == "" || commitSHA == "none" {
		return cliVersion
	}
	return fmt.Sprintf("%s (%s)", cliVersion, commitSHA)
}

// defaultRef falls back to "main" when the build-time version is the plain
// `go build` placeholder rather than a real ref.
func defaultRef(v string) string {
	if v == "" || v == "dev" {
		return "main"
	}
	return v
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}
