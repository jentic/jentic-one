package ctlcmd

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/cli/localagentcmd"
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

func newUpdateCmd(app *app) *cobra.Command {
	opts := &updateOptions{}
	cmd := &cobra.Command{
		Use:   "update",
		Short: "Update the jentic CLIs (and check the stack) to the latest release",
		Long: "update reports the installed CLI and server versions, compares the\n" +
			"installed version against the latest release tag on GitHub, and (unless\n" +
			"--check) rebuilds and replaces the jenticctl and jentic binaries in place,\n" +
			"then rebuilds the installed stack. Use --cli-only or --stack-only to update\n" +
			"just one half.\n\n" +
			"A Homebrew-installed CLI is never swapped in place: the CLI half runs\n" +
			"`brew upgrade jentic` instead, so brew's bookkeeping stays consistent\n" +
			"(--ref cannot pin a Homebrew-managed CLI).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.updateE(cmd.Context(), opts)
		},
	}
	cmd.Flags().BoolVar(&opts.check, "check", false, "only report status; don't apply any update")
	cmd.Flags().BoolVar(&opts.cliOnly, "cli-only", false, "update only the CLI binary")
	cmd.Flags().BoolVar(&opts.stackOnly, "stack-only", false, "update only the stack (not the CLI binary)")
	cmd.Flags().BoolVarP(&opts.yes, "yes", "y", false, "skip the confirmation prompt")
	cmd.Flags().StringVar(&opts.ref, "ref", "", "git ref to update to, pinning a specific tag/branch/commit (default: the latest release tag)")
	cmd.Flags().StringVar(&opts.baseURL, "base-url", "", "Jentic control-plane base URL (for the server probe)")
	return cmd
}

func (a *app) updateE(ctx context.Context, opts *updateOptions) error {
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

	fmt.Fprint(a.Out, a.BrandHeader(ctx, opts.baseURL, cliVersion))
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Field("cli", cliLine(cliVersion, installed)))
	// Surface the stack's own recorded build separately from the CLI's. They
	// advance independently, and a silent divergence is exactly what left users
	// on a stale stack with no visible cause (#943).
	if stackRef := manifest.ResolvedStackRef(); stackRef != "" {
		fmt.Fprintln(a.Out, theme.Field("stack", stackRef))
	}
	if !found {
		fmt.Fprintln(a.Out, theme.Dim.Render("  (no install manifest; using build-time metadata)"))
	}
	if brewManaged {
		fmt.Fprintln(a.Out, theme.Field("managed by", "Homebrew — CLI updates delegate to `"+update.BrewUpgradeCommand+"`"))
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

	// A brew-managed CLI is never swapped by us — the CLI half delegates to
	// `brew upgrade jentic` (flyctl-style) so brew's bookkeeping stays
	// consistent. brew can only ship the latest release, so an explicit --ref
	// cannot be honored for the CLI half and is refused rather than silently
	// updating to something else.
	if brewManaged && doCLI && pinned {
		return errors.New("--ref cannot pin a Homebrew-managed CLI (brew only ships the latest release); use --stack-only, or reinstall from source via tools/install.sh")
	}

	// A pinned ref cannot be applied to a local checkout: the stack builds from
	// the operator's own working tree (cwd walk or $JENTIC_SRC), and syncing that
	// to a ref would clobber their work. Refuse rather than build something other
	// than what was asked for — silently ignoring the pin is the bug being fixed
	// here (#949).
	if doStack && pinned {
		if plan := install.PlanLocalBuild(a.Paths.VenvPath(), a.Paths.SrcPath()).AtRef(ref, pinned); plan.PinnedRefIgnored() {
			return fmt.Errorf("--ref %s cannot be applied: the stack builds from the local checkout at %s, "+
				"so the ref would be ignored. Check out %s there yourself and re-run without --ref, "+
				"or unset %s to build from a managed clone",
				ref, plan.SourceDir, ref, install.SrcEnv)
		}
	}

	// When the latest release is not newer than what's installed there's
	// nothing to rebuild. Each requested half is gated on its own recorded
	// version (see updateNeeded): they normally move in lockstep, but a
	// brew-managed CLI is refreshed out-of-band by `brew upgrade` while the
	// stack may lag behind, so the stack half must not key off the CLI binary.
	// A --ref override always proceeds (the user asked for a specific build);
	// re-run with --ref to force a rebuild at a pinned version.
	stackVersion := firstNonEmpty(manifest.ResolvedStackRef(), cliVersion)
	if !pinned && latestKnown && !updateNeeded(doCLI, doStack, cliVersion, stackVersion, latest) {
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Successf("Already up to date (%s); nothing to rebuild.", latest))
		return nil
	}

	// Only the stack lags: don't invoke brew for a CLI that is already at the
	// latest release (brew would just report "already up to date").
	if brewManaged && doCLI && latestKnown && !update.NewerAvailable(cliVersion, latest) {
		doCLI = false
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Dim.Render("  CLI already at the latest release; updating the stack only."))
	}

	if !opts.yes {
		ok, err := confirmApply(doCLI, doStack, brewManaged, repo, ref)
		if err != nil {
			return err
		}
		if !ok {
			fmt.Fprintln(a.Out, theme.Dim.Render("update cancelled"))
			return nil
		}
	}

	// In a *combined* docker-mode run, probe the daemon here — before updateCLI
	// swaps the binaries — so a stopped daemon fails fast instead of leaving new
	// binaries against an old, un-rebuilt stack. Scoped to doCLI so we don't
	// probe/announce twice on the stack-only path (updateStackDocker guards
	// itself); the daemonChecked flag then tells the stack step to skip its own
	// probe here. The probe may poll (~30s) for a cold-starting daemon.
	daemonChecked := false
	if doCLI && doStack && manifest.Mode == config.ModeDocker && proc.FileExists(a.Paths.ComposePath()) {
		announceDaemonCheck(a.Out)
		if err := requireDockerDaemon(ctx, "jenticctl update"); err != nil {
			return err
		}
		daemonChecked = true
	}

	if doCLI {
		if brewManaged {
			if err := a.brewUpgradeCLI(ctx, latest, latestKnown); err != nil {
				return err
			}
		} else if err := a.updateCLI(ctx, repo, ref, ctlTarget); err != nil {
			return err
		}
		// A new CLI can ship a new skill body (transport-error recovery, envelope
		// parsing, per-BC guidance — plan.md Phase 6 "skill lifecycle"). Refresh
		// every INSTALLED skill so old-CLI guidance can't linger, respecting the
		// user-edit guard (edited blocks are skipped + warned, never clobbered
		// without --force). Best-effort: the binaries are already swapped, so a
		// skill-refresh failure must not fail the update — it only leaves skills
		// a `jentic skill update` away.
		a.refreshSkillsAfterUpdate()
	}
	if doStack {
		if err := a.updateStack(ctx, manifest.Mode, manifest.DB, ref, pinned, daemonChecked); err != nil {
			return err
		}
		// Only now is the stack genuinely at `ref`. Recording it earlier (or
		// alongside the CLI half) is what let a failed/skipped stack rebuild
		// advertise itself as current and wedge every later update (#943).
		a.recordStackBuild(ref)
	}
	return nil
}

// refreshSkillsAfterUpdate re-renders every installed skill with the new CLI's
// bundled bodies, delegating to the same engine as `jentic skill update` (all
// operators, installed scopes only, edit guard on). It reuses that command's
// runner so there is exactly one skill-refresh implementation. Best-effort by
// contract: the update already succeeded, so a refresh error is warned, not
// fatal (see caller). Manually-edited skill blocks are skipped and reported —
// never overwritten without an explicit `jentic skill update --force`.
func (a *app) refreshSkillsAfterUpdate() {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("Refreshing installed skills for the new CLI ..."))
	if err := localagentcmd.New(a.App).SkillUpdateDefault(nil); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("could not refresh skills (run `jentic skill update`): %v", err))
	}
}

// recordStackBuild persists the ref the stack was just built from, so the next
// `update` gates the stack half on what was actually built. Best-effort: the
// rebuild already succeeded, so a manifest write failure must not fail the
// command — it only costs a redundant rebuild next time.
func (a *app) recordStackBuild(ref string) {
	m, _, err := config.LoadManifest(a.Paths)
	if err != nil {
		return
	}
	if err := m.RecordStackBuild(a.Paths, ref); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("warning: could not record stack version: %v", err))
	}
}

// brewUpgradeCLI refreshes a Homebrew-managed CLI by delegating to
// `brew upgrade jentic`, streaming brew's output. brew swaps both binaries
// (they ship in one cask) and its bookkeeping stays consistent.
//
// brew can only install what the cask currently ships, and the cask bump lags
// the GitHub release tag by a bit; inside that window `brew upgrade` is a
// clean no-op, so success is only claimed after verifying the installed cask
// actually reached latest.
func (a *app) brewUpgradeCLI(ctx context.Context, latest string, latestKnown bool) error {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Heading.Render("Updating CLI via Homebrew"))
	fmt.Fprintln(a.Out, theme.Dimf("  running: %s", update.BrewUpgradeCommand))
	if err := update.BrewUpgrade(ctx, a.Out, a.Err); err != nil {
		return err
	}
	if caskVersion := update.BrewCaskVersion(ctx); latestKnown && caskVersion != "" && update.NewerAvailable(caskVersion, latest) {
		fmt.Fprintln(a.Out, theme.Warnf("Homebrew's jentic cask is still at %s (release %s not yet published to the tap) — re-run `%s` in a while.", caskVersion, latest, update.BrewUpgradeCommand))
		return nil
	}
	fmt.Fprintln(a.Out, theme.Successf("CLI updated via Homebrew."))
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
func (a *app) updateCLI(ctx context.Context, repo, ref, ctlTarget string) error {
	// The sibling jentic binary lives next to jenticctl (install.sh co-locates
	// both in JENTIC_INSTALL_DIR).
	installDir := filepath.Dir(ctlTarget)

	// Prefer a DOWNLOAD-AND-SWAP when `ref` resolves to a real published release
	// (no compiler, no clone). Fall back to the from-source rebuild for forks /
	// dev refs / a tag without published assets — unchanged behaviour.
	staged, cleanup, fromDownload, err := a.acquireCLIBinaries(ctx, repo, ref)
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
	// Track (target, backup) so a mid-loop failure (e.g. the second binary) can
	// roll BOTH back from their .bak, never leaving a half-swapped pair.
	type swap struct{ target, backup string }
	var done []swap
	restore := func() {
		for _, s := range done {
			if s.backup != "" {
				_ = update.RestoreBinary(s.target, s.backup)
			}
		}
	}
	var swapped []string
	for _, name := range []string{ctlBinaryName, apiBinaryName} {
		src, ok := staged[name]
		if !ok {
			continue
		}
		target := targets[name]
		backup, err := update.ReplaceBinary(target, src)
		if err != nil {
			restore()
			return fmt.Errorf("replace %s: %w", name, err)
		}
		done = append(done, swap{target: target, backup: backup})
		swapped = append(swapped, name)
		fmt.Fprintln(a.Out, theme.Field(name, target))
		if backup != "" {
			fmt.Fprintln(a.Out, theme.Dimf("  previous %s backed up at %s", name, backup))
		}
	}

	a.refreshManifestBinaryPath(ctlTarget)

	how := "built"
	if fromDownload {
		how = "downloaded"
	}
	fmt.Fprintln(a.Out, theme.Successf("Updated %s -> %s (%s)", strings.Join(swapped, " + "), strings.TrimSpace(newVersion), how))
	return nil
}

// acquireCLIBinaries stages the two CLI binaries for `ref`, preferring a
// verified release download and falling back to a from-source build. It returns
// the staged paths, a cleanup func, and whether the download path was taken.
func (a *app) acquireCLIBinaries(ctx context.Context, repo, ref string) (map[string]string, func(), bool, error) {
	if tag := resolvedReleaseTag(ref); tag != "" {
		staged, cleanup, err := a.downloadCLIBinaries(ctx, repo, tag)
		if err == nil {
			return staged, cleanup, true, nil
		}
		// A download failure that is a fail-closed VERIFICATION error must NOT
		// silently fall back to building unverified — surface it. Any other
		// failure (asset missing for this platform, transport) drops to source.
		if isVerificationError(err) {
			if cleanup != nil {
				cleanup()
			}
			return nil, nil, false, err
		}
		fmt.Fprintln(a.Out, theme.Dimf("  no usable release download (%v); building from source", err))
		if cleanup != nil {
			cleanup()
		}
	}
	staged, cleanup, err := a.stageCLIBuild(ctx, repo, ref)
	return staged, cleanup, false, err
}

// downloadCLIBinaries downloads + verifies both binaries for tag into a staging
// dir, returning their paths and a cleanup func. Any verification failure is
// returned as a verificationError so the caller refuses to fall back to an
// unverified source build.
func (a *app) downloadCLIBinaries(ctx context.Context, repo, tag string) (map[string]string, func(), error) {
	stage, err := os.MkdirTemp("", "jentic-cli-download-*")
	if err != nil {
		return nil, nil, err
	}
	cleanup := func() { _ = os.RemoveAll(stage) }
	token := os.Getenv("GITHUB_TOKEN")
	staged := map[string]string{}
	for _, name := range []string{ctlBinaryName, apiBinaryName} {
		res, derr := update.DownloadAndVerify(ctx, repo, tag, name, runtime.GOOS, runtime.GOARCH, token, stage)
		if derr != nil {
			cleanup()
			if update.IsVerificationError(derr) {
				return nil, nil, verificationError{derr}
			}
			return nil, nil, derr
		}
		if res.Warning != "" {
			fmt.Fprintln(a.Out, theme.Warnf("%s", res.Warning))
		}
		staged[name] = res.BinaryPath
	}
	return staged, cleanup, nil
}

// verificationError marks a fail-closed download failure (bad checksum/sig) so
// the updater refuses to fall back to an unverified from-source build.
type verificationError struct{ err error }

func (e verificationError) Error() string { return e.err.Error() }
func (e verificationError) Unwrap() error { return e.err }

func isVerificationError(err error) bool {
	var ve verificationError
	return errors.As(err, &ve)
}

// resolvedReleaseTag returns ref if it is a canonical published-release tag
// (so download-and-swap can engage), else "". A non-release ref (branch, SHA,
// dev) has no published assets and must build from source.
func resolvedReleaseTag(ref string) string {
	if update.IsReleaseTag(ref) {
		return ref
	}
	return ""
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
func (a *app) stageCLIBuild(ctx context.Context, repo, ref string) (map[string]string, func(), error) {
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
func (a *app) refreshManifestBinaryPath(target string) {
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
// mode; an empty/unknown mode is treated as a local install. ref is the git ref
// the stack is built from — the same one the CLI half targets, so the two halves
// stay in lockstep. daemonChecked is true when updateE already probed the Docker
// daemon up front (combined run), so the docker path skips a redundant probe.
func (a *app) updateStack(ctx context.Context, mode, db, ref string, pinned, daemonChecked bool) error {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("Stack update runs forward-only migrations — back up your data first"))
	fmt.Fprintln(a.Out, theme.Dim.Render("  SQLite: copy ~/.jentic/data/*.db · Postgres: pg_dump your database"))

	if mode == config.ModeDocker {
		return a.updateStackDocker(ctx, ref, pinned, daemonChecked)
	}
	return a.updateStackLocal(ctx, db, ref, pinned)
}

// updateStackLocal pulls the source, reinstalls into the existing venv, applies
// migrations, and restarts the app if it was running. For the SQLite backend it
// snapshots the *.db files first and rolls them back if the migration fails, so
// a broken forward-only migration can't leave the operator with a half-migrated
// database and no recovery (CLI-V2 Phase 6 lifecycle hardening). Postgres keeps
// the documented pg_dump warning above — an in-CLI dump of an operator-managed
// server is out of scope.
func (a *app) updateStackLocal(ctx context.Context, db, ref string, pinned bool) error {
	configPath := a.Paths.InstallConfigPath()
	if !proc.FileExists(configPath) {
		return fmt.Errorf("not configured: %s not found — run `jenticctl install` first", configPath)
	}

	install.EnsureUv(a.Out)
	plan := install.PlanLocalBuild(a.Paths.VenvPath(), a.Paths.SrcPath()).AtRef(ref, pinned)
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, plan.RenderHeader())
	if err := plan.Execute(a.Out); err != nil {
		return fmt.Errorf("rebuild failed: %w", err)
	}

	// Stop the app BEFORE snapshotting + migrating (F1, review round-3 #7). A
	// forward-only Alembic migration against a live, WAL-mode SQLite DB — with a
	// backup that couldn't cleanly capture the in-flight WAL — is the inconsistent
	// half-state the rollback net exists to prevent. Stopping first makes the
	// snapshot self-contained and the migration the sole writer. Restart happens
	// after a successful migration (or is left stopped on failure, having rolled
	// back). If it wasn't running, we don't start it — matching prior behaviour.
	wasRunning := a.stopLocalIfRunning(ctx)

	// Snapshot the SQLite files before the forward-only migration so a failure
	// rolls back to the pre-migration state rather than stranding a half-applied
	// schema. Shared with first-`install` via MigrateWithRollback so the two
	// rollback paths cannot drift (P1-E).
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderMigrateHeader(configPath))
	if err := update.MigrateWithRollback(
		a.Paths.DataDir(),
		db == install.BackendSQLite,
		func() error { return install.RunMigrations(ctx, a.Out, plan.VenvPython(), configPath) },
		func() { fmt.Fprintln(a.Out, theme.Dim.Render("  snapshotted SQLite data for rollback")) },
		func() {
			fmt.Fprintln(a.Out, theme.Warnf("migrations failed; rolled the SQLite database back to its pre-update state"))
		},
	); err != nil {
		return err
	}

	if wasRunning {
		a.startLocalAfterStop(ctx)
	} else {
		fmt.Fprintln(a.Out, theme.Dim.Render("  app not running — start it with `jenticctl start`"))
	}
	fmt.Fprintln(a.Out, theme.Successf("Stack updated (local)."))
	return nil
}

// updateStackDocker rebuilds the app image, applies migrations in a one-shot
// container, and recreates the running stack with the new image. daemonChecked
// is true when updateE already probed the daemon up front (combined run), so we
// skip a redundant second probe/announce; a standalone/stack-only call passes
// false and probes here.
func (a *app) updateStackDocker(ctx context.Context, ref string, pinned, daemonChecked bool) error {
	composePath := a.Paths.ComposePath()
	if !proc.FileExists(composePath) {
		return fmt.Errorf("no compose stack at %s — run `jenticctl install` first", composePath)
	}

	// Fail fast with an actionable message when the daemon is down, before the
	// long image build — otherwise the build/migrations/up sequence surfaces a
	// raw compose transport error deep into the run. The probe may poll (~30s)
	// for a cold-starting daemon, so announce it first (see start.go/stop.go).
	if !daemonChecked {
		announceDaemonCheck(a.Out)
		if err := requireDockerDaemon(ctx, "jenticctl update"); err != nil {
			return err
		}
	}

	plan := install.PlanLocalBuild(a.Paths.VenvPath(), a.Paths.SrcPath()).AtRef(ref, pinned)
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
	if err := composeUp(a.Out, composePath); err != nil {
		return fmt.Errorf("docker compose up: %w", err)
	}
	fmt.Fprintln(a.Out, theme.Successf("Stack updated (docker)."))
	return nil
}

// stopLocalIfRunning stops the local app if it is running and reports whether it
// was. Split out of the old restartLocalIfRunning (F1, review round-3 #7) so the
// update flow can stop the app BEFORE snapshotting + migrating the SQLite
// database — migrating a live, WAL-mode DB and then trying to roll back a .db
// whose committed pages still live in an uncheckpointed WAL is exactly the
// inconsistent half-state the rollback net is meant to prevent.
func (a *app) stopLocalIfRunning(ctx context.Context) bool {
	_, running, _ := proc.LivePID(a.Paths.AppPIDPath())
	if !running {
		return false
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Infof("Stopping app before migration ..."))
	_ = a.stopE(ctx, &stopOptions{timeout: 10 * time.Second})
	return true
}

// startLocalAfterStop restarts the local app after an update, used when
// stopLocalIfRunning reported it was running before the update began.
func (a *app) startLocalAfterStop(ctx context.Context) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Infof("Restarting app ..."))
	_ = a.startE(ctx, &startOptions{})
}

func confirmApply(doCLI, doStack, brewCLI bool, repo, ref string) (bool, error) {
	// This prompt is only reached when an update is available, so default the
	// focused selection to "Yes, update": the user already invoked `update`, so
	// a reflexive Enter should proceed rather than cancel (#765).
	confirm := true
	if err := install.RunConfirm(
		huh.NewConfirm().
			Title(applyPromptTitle(doCLI, doStack, brewCLI, repo, ref)).
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

// applyPromptTitle words the confirmation prompt for the halves about to run.
// A brew-managed CLI is updated by brew at brew's latest, not from repo@ref,
// so the prompt must not promise a ref it cannot honor.
func applyPromptTitle(doCLI, doStack, brewCLI bool, repo, ref string) string {
	switch {
	case brewCLI && doCLI && doStack:
		return fmt.Sprintf("Update the CLI (via `%s`) and the stack (from %s@%s)?", update.BrewUpgradeCommand, repo, ref)
	case brewCLI && doCLI:
		return fmt.Sprintf("Update the CLI via `%s`?", update.BrewUpgradeCommand)
	case doCLI && doStack:
		return fmt.Sprintf("Update the CLI and the stack to %s@%s?", repo, ref)
	case doCLI:
		return fmt.Sprintf("Update the CLI to %s@%s?", repo, ref)
	default:
		return fmt.Sprintf("Update the stack to %s@%s?", repo, ref)
	}
}

// printVerdict reports up-to-date / update-available based on the installed CLI
// version and the latest release tag, compared as semver.
func (a *app) printVerdict(installed, latest string) {
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
