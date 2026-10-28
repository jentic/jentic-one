package ctlcmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
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
	imageTag  string
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
			"then refreshes the installed stack. A Docker stack running a published\n" +
			"image pulls the image for the target release; one built from source is\n" +
			"rebuilt. Use --cli-only or --stack-only to update just one half. A CLI\n" +
			"already at the latest release is not swapped again, so a re-run after a\n" +
			"failed stack half refreshes only the stack.\n\n" +
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
	cmd.Flags().StringVar(&opts.imageTag, "image-tag", "", "Docker stack only: image tag, @digest, or full reference to move the stack to (a bare tag applies to the registry the stack already runs). Required to move a stack pinned with --image-tag or to a digest")
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
	brewManaged := isBrewManaged(ctlTarget)
	// brew upgrades the CLI out-of-band, so a recorded cli_version can lag the
	// binary brew installed; the running binary's stamped version is the truth.
	if brewManaged && isStampedVersion(version) {
		cliVersion = version
	}

	// A CLI-only install (tools/install.sh or brew, never `jenticctl install`)
	// has no stack: no deploy mode recorded and no install config on disk.
	stackInstalled := manifest.Mode != "" || proc.FileExists(a.Paths.InstallConfigPath())

	fmt.Fprint(a.Out, a.BrandHeader(ctx, opts.baseURL, cliVersion))
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Field("cli", cliLine(cliVersion, installed)))
	// Surface the stack's own recorded build separately from the CLI's. They
	// advance independently, and a silent divergence is exactly what left users
	// on a stale stack with no visible cause (#943).
	if !stackInstalled {
		fmt.Fprintln(a.Out, theme.Field("stack", "not installed"))
	} else if stackRef := manifest.ResolvedStackRef(); stackRef != "" {
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
	latest, latestErr := latestReleaseTag(ctx, repo, os.Getenv("GITHUB_TOKEN"))
	latestKnown := latestErr == nil
	stackVersion := firstNonEmpty(manifest.ResolvedStackRef(), cliVersion)

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
		verdictStack := stackVersion
		if !stackInstalled {
			verdictStack = "" // no stack to be behind
		}
		a.printVerdict(cliVersion, verdictStack, latest)
	} else {
		fmt.Fprintln(a.Out, theme.Field("latest", "unknown"))
		fmt.Fprintln(a.Out, theme.Warnf("  %v", latestErr))
	}

	if opts.check {
		return nil
	}

	doCLI := !opts.stackOnly
	doStack := !opts.cliOnly

	// With no stack installed there is nothing for the stack half to update.
	// An explicit --stack-only is refused; a combined run updates the CLI only,
	// so a missing stack is never reported as a CLI/stack divergence. The note
	// is printed only once an update will actually run (below).
	stackSkipped := false
	if doStack && !stackInstalled {
		if opts.stackOnly {
			return fmt.Errorf("no stack installed: %s not found — run `jenticctl install` first", a.Paths.InstallConfigPath())
		}
		doStack = false
		stackSkipped = true
	}

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
	//
	// A Docker stack whose refresh is a pull never reads the checkout, so the
	// pin IS honoured and the question does not apply. Only ask about the
	// checkout when the refresh would actually read it.
	if doStack {
		pulls, err := a.dockerStackPullsOnUpdate(manifest.Mode, ref, opts.imageTag)
		if err != nil {
			return fmt.Errorf("%w — fix it, or reinstall with `jenticctl install`", err)
		}
		if pinned && !pulls {
			if plan := install.PlanLocalBuild(a.Paths.VenvPath(), a.Paths.SrcPath()).AtRef(ref, pinned); plan.PinnedRefIgnored() {
				return fmt.Errorf("--ref %s cannot be applied: the stack builds from the local checkout at %s, "+
					"so the ref would be ignored. Check out %s there yourself and re-run without --ref, "+
					"or unset %s to build from a managed clone",
					ref, plan.SourceDir, ref, install.SrcEnv)
			}
		}
		// Refuse what the stack half cannot do BEFORE the CLI half swaps the
		// binaries. These are static preconditions — a digest-pinned or
		// tag-pinned image, or an unpinned ref with no published image — so a
		// refusal raised after the swap would leave the CLI on the new version
		// and the stack on the old one, with every retry hitting the same wall
		// and no flag to escape it.
		if err := a.checkDockerRefreshPossible(manifest.Mode, ref, pinned, opts.imageTag); err != nil {
			return err
		}
	}

	// When the latest release is not newer than what's installed there's
	// nothing to rebuild. Each requested half is gated on its own recorded
	// version (see updateNeeded): they normally move in lockstep, but a
	// brew-managed CLI is refreshed out-of-band by `brew upgrade` while the
	// stack may lag behind, so the stack half must not key off the CLI binary.
	// A --ref override always proceeds (the user asked for a specific build);
	// re-run with --ref to force a rebuild at a pinned version.
	// A Docker stack whose recorded version does not match the image it actually
	// runs is not up to date whatever the manifest says, so the recorded version
	// alone must not short-circuit the run. That is the state an update left
	// behind while it filed the new ref without moving the compose file
	// (#2011): the operators who hit the bug are exactly the ones whose
	// manifest now claims to be current.
	staleStack := doStack && a.dockerStackImageLagsRecord(manifest)
	if !pinned && latestKnown && !staleStack && !updateNeeded(doCLI, doStack, cliVersion, stackVersion, latest) {
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Successf("Already up to date (%s); nothing to rebuild.", latest))
		return nil
	}
	if staleStack {
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Warnf(
			"warning: the stack records %s but its compose file still runs an older image; refreshing it",
			stackVersion))
	}

	if stackSkipped {
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Dim.Render("  no stack installed; updating the CLI only (run `jenticctl install` to set one up)"))
	}

	// Only the stack lags: don't re-swap (or brew-upgrade) a CLI that is
	// already at the latest release. This is the re-run after a stack half
	// that failed once the CLI was already swapped (#976). Reaching here with
	// doCLI set and a current CLI implies doStack (updateNeeded above), and a
	// pinned ref keeps the CLI half: the operator asked for that exact build.
	if doCLI && !pinned && latestKnown && !update.NewerAvailable(cliVersion, latest) {
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

	// In a *combined* docker-mode run, preflight the stack here — before
	// updateCLI swaps the binaries — so a stopped daemon or a host port held by
	// another container fails fast instead of leaving new binaries against an
	// old, un-rebuilt stack (#976). Scoped to doCLI so we don't probe/announce
	// twice on the stack-only path (updateStackDocker preflights itself); the
	// preflighted flag then tells the stack step to skip its own checks. The
	// daemon probe may poll (~30s) for a cold-starting daemon.
	preflighted := false
	if doCLI && doStack && manifest.Mode == config.ModeDocker && proc.FileExists(a.Paths.ComposePath()) {
		if err := a.preflightDockerStack(ctx); err != nil {
			return err
		}
		preflighted = true
	}

	cliUpdated := false
	cliLanded := ""
	if doCLI {
		var landed config.CLIUpdate
		if brewManaged {
			caskVersion, err := upgradeBrewCLI(a, ctx, latest, latestKnown)
			if err != nil {
				return err
			}
			// brew is a clean no-op while the cask lags the release; only a cask
			// that actually moved past the recorded version counts as an update.
			// An unreadable cask version is assumed to have moved (brew succeeded).
			cliUpdated = caskVersion == "" || update.NewerAvailable(cliVersion, caskVersion)
			if cliUpdated && caskVersion != "" {
				landed = config.CLIUpdate{Version: "v" + strings.TrimPrefix(caskVersion, "v")}
			}
		} else {
			if landed, err = swapCLI(a, ctx, repo, ref, ctlTarget); err != nil {
				return err
			}
			cliUpdated = true
		}
		if cliUpdated {
			// Record what the CLI half installed before the stack half runs, so
			// a stack failure leaves the manifest naming the new CLI and the old
			// stack — the next run's verdict and CLI-skip read these (#976).
			a.recordCLIUpdate(landed)
			cliLanded = landed.Version
			// A new CLI can ship new skill bodies, so refresh every INSTALLED
			// skill to keep the old CLI's guidance from lingering, respecting the
			// user-edit guard (edited blocks are skipped + warned, never
			// clobbered without --force). Best-effort: the binaries are already
			// swapped, so a skill-refresh failure must not fail the update — it
			// only leaves skills a `jentic skill update` away.
			refreshSkills(a)
		}
	}
	if doStack {
		if err := rebuildStack(a, ctx, manifest.Mode, manifest.DB, ref, pinned, preflighted, opts.imageTag); err != nil {
			if cliUpdated {
				return newStackDivergedError(err, cliTargetLabel(brewManaged, cliLanded), ref, stackVersion, stackRetryCommand(ref, pinned))
			}
			return err
		}
		// Only now is the stack genuinely at `ref`. Recording it earlier (or
		// alongside the CLI half) is what let a failed/skipped stack rebuild
		// advertise itself as current and wedge every later update (#943).
		a.recordStackBuild(ref)
	}
	return nil
}

// Package-level seams over update's external effects (release lookup, CLI
// swap, skill refresh, stack rebuild, local build and migrations, Docker
// calls) so the update flow can be tested end to end without network,
// binaries, or a Docker daemon. Tests that swap these globals must not run
// with t.Parallel().
var (
	latestReleaseTag     = update.LatestReleaseTag
	swapCLI              = (*app).updateCLI
	acquireCLI           = (*app).acquireCLIBinaries
	upgradeBrewCLI       = (*app).brewUpgradeCLI
	brewUpgrade          = update.BrewUpgrade
	brewCaskVersion      = update.BrewCaskVersion
	isBrewManaged        = update.BrewManaged
	refreshSkills        = (*app).refreshSkillsAfterUpdate
	rebuildStack         = (*app).updateStack
	foreignPortHolders   = install.ForeignPortHolders
	buildStackImages     = install.BuildPlan.BuildImages
	pullAppImage         = install.PullAppImage
	runComposeMigrations = install.RunComposeMigrations
	ensureUv             = install.EnsureUv
	buildLocalStack      = install.BuildPlan.Execute
	runLocalMigrations   = install.RunMigrations
)

// preflightDockerStack checks what the Docker stack rebuild needs before any
// part of the update is applied: a responsive daemon, and no other container
// already publishing one of the stack's host ports (the db port 5432 held by a
// leftover dev stack is the classic case, #976). The port check is
// best-effort — if the lookup itself fails it is skipped, and compose's own
// bind failure is still translated by updateStackDocker.
func (a *app) preflightDockerStack(ctx context.Context) error {
	announceDaemonCheck(a.Out)
	if err := requireDockerDaemon(ctx, "jenticctl update"); err != nil {
		return err
	}
	holders, err := foreignPortHolders(ctx, a.Paths.ComposePath())
	if err != nil {
		fmt.Fprintln(a.Out, theme.Dimf("  skipped the host-port check: %v", err))
		return nil
	}
	if len(holders) == 0 {
		return nil
	}
	lines := make([]string, 0, len(holders))
	for _, h := range holders {
		lines = append(lines, "  - "+h.String()+" — stop it with `docker stop "+h.Container+"`")
	}
	return fmt.Errorf("the jentic stack cannot start because another container holds a port it publishes:\n%s\n"+
		"nothing was updated; free the port(s), then re-run `jenticctl update`", strings.Join(lines, "\n"))
}

// stackDivergedError reports a stack half that failed after the CLI half
// already succeeded, leaving the two at different versions. It names the CLI
// target, the stack target it did not reach, the last stack build that did
// succeed, and the exact command that finishes the update, so the skew is
// never silent (#976). It does not claim the old stack is intact: the stack
// half is not atomic, and the cause carries a stackStageError hint when a
// step already changed the stack's on-disk state.
type stackDivergedError struct {
	cliTarget    string
	stackTarget  string
	stackVersion string
	retry        string
	err          error
}

func newStackDivergedError(err error, cliTarget, stackTarget, stackVersion, retry string) *stackDivergedError {
	return &stackDivergedError{cliTarget: cliTarget, stackTarget: stackTarget, stackVersion: stackVersion, retry: retry, err: err}
}

func (e *stackDivergedError) Error() string {
	return fmt.Sprintf("the CLI was updated %s but the stack was not updated to %s (last successful stack build: %s): %v\n"+
		"fix the cause above, then finish the update with `%s`", e.cliTarget, e.stackTarget, e.stackVersion, e.err, e.retry)
}

func (e *stackDivergedError) Unwrap() error { return e.err }

// stackStageError is a stack-half failure after a step that already changed
// the stack's on-disk state (rebuilt venv or image, stopped app, applied
// migrations). The hint tells the operator what that state is and what not to
// do before the update is finished, since the stack is then neither the old
// build nor the new one.
type stackStageError struct {
	hint string
	err  error
}

func (e *stackStageError) Error() string { return fmt.Sprintf("%v\n%s", e.err, e.hint) }

func (e *stackStageError) Unwrap() error { return e.err }

// Stage hints for stackStageError.
const (
	hintLocalRebuildFailed  = "the local environment may be partly rebuilt; finish the update before running `jenticctl start`"
	hintLocalMigrateFailed  = "the app is stopped and its environment is already rebuilt; finish the update before running `jenticctl start`"
	hintDockerImageRebuilt  = "the stack image is already rebuilt; don't start the stack until the update finishes"
	hintDockerMigrationsRan = "the stack image is already rebuilt and migrations have run; don't start the stack until the update finishes"
	// The pull route rebuilds nothing; what changed is the compose file, which
	// now names the new image, so an operator reverting by hand needs to know
	// that rather than be told about a build that never ran.
	hintDockerImagePulled    = "the compose stack already names the new image; don't start the stack until the update finishes"
	hintDockerPulledMigrated = "the compose stack already names the new image and migrations have run; don't start the stack until the update finishes"
	// The conversion rewrote the compose file off the published image onto the
	// locally built one, which finishing (or abandoning) the update does not
	// undo, so the hint says so rather than only mentioning the build.
	hintDockerConverted         = "the stack image is rebuilt and the compose stack now runs the locally built image instead of a published one; don't start the stack until the update finishes"
	hintDockerConvertedMigrated = "the compose stack now runs the locally built image instead of a published one and migrations have run; don't start the stack until the update finishes"
)

// dockerRefreshRoute is how updateStackDocker brought the stack's image up to
// date, which decides what a later failure tells the operator is on disk.
type dockerRefreshRoute int

const (
	// routeRebuilt rebuilt the fixed local tag; the compose file is unchanged.
	routeRebuilt dockerRefreshRoute = iota
	// routePulled pulled a published image and repointed the compose file at it.
	routePulled
	// routeConverted rebuilt AND repointed the compose file off the published
	// reference onto the local tag, so the stack is a from-source deployment
	// from now on. That durable change needs saying: it is not undone by
	// finishing the update, and `update` has no flag to reverse it.
	routeConverted
)

// dockerStageHints pairs the post-image and post-migration stage hints for the
// route the refresh took, so a failure after it names the change that is
// actually on disk.
func dockerStageHints(route dockerRefreshRoute) (afterImage, afterMigrations string) {
	switch route {
	case routePulled:
		return hintDockerImagePulled, hintDockerPulledMigrated
	case routeConverted:
		return hintDockerConverted, hintDockerConvertedMigrated
	default:
		return hintDockerImageRebuilt, hintDockerMigrationsRan
	}
}

// cliTargetLabel words where the CLI half landed for the divergence message:
// a brew-managed CLI reaches whatever the cask ships, not necessarily ref.
func cliTargetLabel(brewManaged bool, version string) string {
	switch {
	case brewManaged && version != "":
		return "via Homebrew to " + version
	case brewManaged:
		return "via Homebrew"
	default:
		return "to " + version
	}
}

// stackRetryCommand is the command that re-runs only the stack half at the
// same target. A pinned ref must be repeated; otherwise the default (latest
// release) target is the one this run used.
func stackRetryCommand(ref string, pinned bool) string {
	if pinned {
		return "jenticctl update --stack-only --ref " + ref
	}
	return "jenticctl update --stack-only"
}

// hostPortConflictError replaces compose's raw "port is already allocated"
// daemon error with the contended port and how to find its holder.
type hostPortConflictError struct {
	port string
	err  error
}

func (e *hostPortConflictError) Error() string {
	return fmt.Sprintf("%v: host port %s is already in use by another container or process — "+
		"find it with `docker ps --filter publish=%s` (or `lsof -nP -iTCP:%s -sTCP:LISTEN` for a host process) and stop it",
		e.err, e.port, e.port, e.port)
}

func (e *hostPortConflictError) Unwrap() error { return e.err }

// withPortConflict wraps err as a hostPortConflictError when the compose
// output that produced it shows a host-port bind failure.
func withPortConflict(err error, output string) error {
	if port, ok := install.DetectPortConflict(output); ok {
		return &hostPortConflictError{port: port, err: err}
	}
	return err
}

// tailBuffer keeps the last tailBufferMax bytes written to it — enough to hold
// the daemon's error from a compose run without growing with build output.
type tailBuffer struct{ buf []byte }

const tailBufferMax = 64 << 10

func (t *tailBuffer) Write(p []byte) (int, error) {
	t.buf = append(t.buf, p...)
	if over := len(t.buf) - tailBufferMax; over > 0 {
		t.buf = t.buf[over:]
	}
	return len(p), nil
}

func (t *tailBuffer) String() string { return string(t.buf) }

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
		fmt.Fprintln(a.Out, theme.Warnf("warning: could not record stack version: %v", err))
		return
	}
	if err := m.RecordStackBuild(a.Paths, ref); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("warning: could not record stack version: %v", err))
	}
}

// recordCLIUpdate persists what the CLI half just installed (see
// config.Manifest.RecordCLIUpdate) into the existing install manifest.
// Without one there is nothing to correct — the next run reads the new
// binary's build-time version — so no manifest is created (a CLI-only or
// brew install keeps reporting "no install manifest"). An empty update (brew
// could not report the cask version) records nothing. Best-effort: the
// binaries are already in place, so a manifest failure is warned, not fatal —
// it only costs a redundant CLI swap next time.
func (a *app) recordCLIUpdate(u config.CLIUpdate) {
	if u == (config.CLIUpdate{}) {
		return
	}
	m, found, err := config.LoadManifest(a.Paths)
	if err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("warning: could not record CLI version: %v", err))
		return
	}
	if !found {
		return
	}
	if err := m.RecordCLIUpdate(a.Paths, u); err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("warning: could not record CLI version: %v", err))
	}
}

// brewUpgradeCLI refreshes a Homebrew-managed CLI by delegating to
// `brew upgrade jentic`, streaming brew's output. brew swaps both binaries
// (they ship in one cask) and its bookkeeping stays consistent.
//
// brew can only install what the cask currently ships, and the cask bump lags
// the GitHub release tag by a bit; inside that window `brew upgrade` is a
// clean no-op, so success is only claimed after verifying the installed cask
// actually reached latest. It returns the installed cask version ("" when brew
// cannot report it) so the caller records what brew actually installed.
func (a *app) brewUpgradeCLI(ctx context.Context, latest string, latestKnown bool) (string, error) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Heading.Render("Updating CLI via Homebrew"))
	fmt.Fprintln(a.Out, theme.Dimf("  running: %s", update.BrewUpgradeCommand))
	if err := brewUpgrade(ctx, a.Out, a.Err); err != nil {
		return "", err
	}
	caskVersion := brewCaskVersion(ctx)
	if latestKnown && caskVersion != "" && update.NewerAvailable(caskVersion, latest) {
		fmt.Fprintln(a.Out, theme.Warnf("Homebrew's jentic cask is still at %s (release %s not yet published to the tap) — re-run `%s` in a while.", caskVersion, latest, update.BrewUpgradeCommand))
		return caskVersion, nil
	}
	fmt.Fprintln(a.Out, theme.Successf("CLI updated via Homebrew."))
	return caskVersion, nil
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
// must have already ruled out package-manager-owned locations. It returns what
// was installed so the caller can record it (see recordCLIUpdate) — on the
// release-download path tools/install.sh never runs, so nothing else updates
// the manifest's CLI fields.
func (a *app) updateCLI(ctx context.Context, repo, ref, ctlTarget string) (config.CLIUpdate, error) {
	// The sibling jentic binary lives next to jenticctl (install.sh co-locates
	// both in JENTIC_INSTALL_DIR).
	installDir := filepath.Dir(ctlTarget)

	// Prefer a DOWNLOAD-AND-SWAP when `ref` resolves to a real published release
	// (no compiler, no clone). Fall back to the from-source rebuild for forks /
	// dev refs / a tag without published assets.
	staged, cleanup, fromDownload, err := acquireCLI(a, ctx, repo, ref)
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		return config.CLIUpdate{}, err
	}

	newVersion, err := binaryVersion(staged[ctlBinaryName])
	if err != nil {
		return config.CLIUpdate{}, fmt.Errorf("verify built binary: %w", err)
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
			return config.CLIUpdate{}, fmt.Errorf("replace %s: %w", name, err)
		}
		done = append(done, swap{target: target, backup: backup})
		swapped = append(swapped, name)
		fmt.Fprintln(a.Out, theme.Field(name, target))
		if backup != "" {
			fmt.Fprintln(a.Out, theme.Dimf("  previous %s backed up at %s", name, backup))
		}
	}

	how := "built"
	if fromDownload {
		how = "downloaded"
	}
	fmt.Fprintln(a.Out, theme.Successf("Updated %s -> %s (%s)", strings.Join(swapped, " + "), strings.TrimSpace(newVersion), how))
	// The version recorded is the ref, matching what tools/install.sh writes
	// for cli_version; the commit comes from the swapped binary itself.
	return config.CLIUpdate{Ref: ref, Version: ref, Commit: commitFromVersionLine(newVersion), BinaryPath: ctlTarget}, nil
}

// versionLineCommit matches the "(commit <sha>" part of the root version
// template ("jenticctl v0.28.0 (commit abc1234, built …)").
var versionLineCommit = regexp.MustCompile(`\(commit ([0-9A-Za-z]+)`)

// commitFromVersionLine extracts the commit from a `--version` line, or ""
// when the binary was built without one ("none").
func commitFromVersionLine(line string) string {
	m := versionLineCommit.FindStringSubmatch(line)
	if m == nil || m[1] == "none" {
		return ""
	}
	return m[1]
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

// binaryVersion runs `<path> --version` and returns its first output line.
// path is a CLI-internal staged build artifact written to a temp dir, not user
// input.
func binaryVersion(path string) (string, error) {
	out, err := exec.Command(path, "--version").Output()
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
// stay in lockstep. preflighted is true when updateE already ran the Docker
// preflight up front (combined run), so the docker path skips a redundant one.
func (a *app) updateStack(ctx context.Context, mode, db, ref string, pinned, preflighted bool, imageTag string) error {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Warn.Render("Stack update runs forward-only migrations — back up your data first"))
	fmt.Fprintln(a.Out, theme.Dim.Render("  SQLite: copy ~/.jentic/data/*.db · Postgres: pg_dump your database"))

	if mode == config.ModeDocker {
		return a.updateStackDocker(ctx, ref, pinned, preflighted, imageTag)
	}
	return a.updateStackLocal(ctx, db, ref, pinned)
}

// updateStackLocal pulls the source, reinstalls into the existing venv, applies
// migrations, and restarts the app if it was running. For the SQLite backend it
// snapshots the *.db files first and rolls them back if the migration fails, so
// a broken forward-only migration can't leave the operator with a half-migrated
// database and no recovery. Postgres keeps the documented pg_dump warning
// above — an in-CLI dump of an operator-managed server is out of scope.
// A failure after the rebuild started returns a stackStageError: the source
// and venv are no longer the last successful build.
func (a *app) updateStackLocal(ctx context.Context, db, ref string, pinned bool) error {
	configPath := a.Paths.InstallConfigPath()
	if !proc.FileExists(configPath) {
		return fmt.Errorf("not configured: %s not found — run `jenticctl install` first", configPath)
	}

	ensureUv(a.Out)
	plan := install.PlanLocalBuild(a.Paths.VenvPath(), a.Paths.SrcPath()).AtRef(ref, pinned)
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, plan.RenderHeader())
	if err := buildLocalStack(plan, a.Out); err != nil {
		return &stackStageError{hint: hintLocalRebuildFailed, err: fmt.Errorf("rebuild failed: %w", err)}
	}

	// Stop the app BEFORE snapshotting + migrating. A forward-only Alembic
	// migration against a live, WAL-mode SQLite DB — with a backup that couldn't
	// cleanly capture the in-flight WAL — is the inconsistent half-state the
	// rollback net exists to prevent. Stopping first makes the snapshot
	// self-contained and the migration the sole writer. Restart happens after a
	// successful migration (or is left stopped on failure, having rolled back).
	// An app that wasn't running is not started.
	wasRunning := a.stopLocalIfRunning(ctx)

	// Snapshot the SQLite files before the forward-only migration so a failure
	// rolls back to the pre-migration state rather than stranding a half-applied
	// schema. Shared with first-`install` via MigrateWithRollback so the two
	// rollback paths cannot drift.
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderMigrateHeader(configPath))
	if err := update.MigrateWithRollback(
		a.Paths.DataDir(),
		db == install.BackendSQLite,
		func() error { return runLocalMigrations(ctx, a.Out, plan.VenvPython(), configPath) },
		func() { fmt.Fprintln(a.Out, theme.Dim.Render("  snapshotted SQLite data for rollback")) },
		func() {
			fmt.Fprintln(a.Out, theme.Warnf("migrations failed; rolled the SQLite database back to its pre-update state"))
		},
	); err != nil {
		return &stackStageError{hint: hintLocalMigrateFailed, err: err}
	}

	if wasRunning {
		a.startLocalAfterStop(ctx)
	} else {
		fmt.Fprintln(a.Out, theme.Dim.Render("  app not running — start it with `jenticctl start`"))
	}
	fmt.Fprintln(a.Out, theme.Successf("Stack updated (local)."))
	return nil
}

// updateStackDocker brings the app image up to ref, applies migrations in a
// one-shot container, and recreates the running stack with it. preflighted
// is true when updateE already ran preflightDockerStack up front (combined
// run), so we skip a redundant second check/announce; a standalone/stack-only
// call passes false and preflights here.
func (a *app) updateStackDocker(ctx context.Context, ref string, pinned, preflighted bool, imageTag string) error {
	composePath := a.Paths.ComposePath()
	if !proc.FileExists(composePath) {
		return fmt.Errorf("no compose stack at %s — run `jenticctl install` first", composePath)
	}

	// Fail fast with an actionable message when the daemon is down or a host
	// port is taken, before the long image build — otherwise the
	// build/migrations/up sequence surfaces a raw compose error deep into the
	// run. The probe may poll (~30s) for a cold-starting daemon, so it is
	// announced first (see start.go/stop.go).
	if !preflighted {
		if err := a.preflightDockerStack(ctx); err != nil {
			return err
		}
	}

	pulled, err := a.refreshDockerStackImage(composePath, ref, pinned, imageTag)
	if err != nil {
		return err
	}

	// The stack now resolves to the new image — the build overwrote the fixed
	// local tag, or the compose file was repointed at the pulled reference — so
	// any later failure leaves `jenticctl start` running it; the stage hints name
	// whichever of the two actually happened. The migration one-shot and `up` are
	// the steps that bind host ports; tee their output so a bind failure becomes
	// a named port conflict rather than the daemon's raw networking error (#976).
	hintAfterImage, hintAfterMigrations := dockerStageHints(pulled)
	configPath := a.Paths.InstallConfigPath()
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderMigrateHeader(configPath))
	tail := &tailBuffer{}
	if err := runComposeMigrations(io.MultiWriter(a.Out, tail), composePath); err != nil {
		return &stackStageError{hint: hintAfterImage, err: withPortConflict(fmt.Errorf("migrations failed: %w", err), tail.String())}
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, install.RenderStartHeader())
	tail = &tailBuffer{}
	if err := composeUp(io.MultiWriter(a.Out, tail), composePath); err != nil {
		return &stackStageError{hint: hintAfterMigrations, err: withPortConflict(fmt.Errorf("docker compose up: %w", err), tail.String())}
	}
	fmt.Fprintln(a.Out, theme.Successf("Stack updated (docker)."))
	return nil
}

// dockerStackRunsPublishedImage reports whether the compose stack at
// composePath runs a published image (rather than one built from source), which
// is what decides between pulling and building on an update.
//
// The compose file is the single authority for this: it is what the running
// stack resolves its image from, so it cannot drift the way a route recorded
// once at install time could. `install` decides the route from its flags and
// the CLI's own version; an update does not re-run that decision, it reads what
// that decision produced.
func dockerStackRunsPublishedImage(composePath string) (image string, published bool, err error) {
	current, err := install.ComposeAppImage(composePath)
	if err != nil {
		return "", false, err
	}
	return current, current != "" && current != install.AppImageTag, nil
}

// dockerStackPullsOnUpdate reports whether a stack-half update to ref would
// refresh this stack by pulling rather than building, which is the case where
// the operator's own checkout plays no part in the refresh.
//
// It takes ref because "runs a published image" is not enough: a target with no
// published image is served by building even on a pulled stack, and that build
// reads the checkout like any other. Answering true there would skip the guard
// that stops a pinned ref being silently ignored (#949).
//
// A compose file that cannot be read is reported as an error rather than as
// false, so the caller states the parse failure instead of diagnosing a
// checkout conflict that is not the real problem.
func (a *app) dockerStackPullsOnUpdate(mode, ref, imageTag string) (bool, error) {
	if mode != config.ModeDocker {
		return false, nil
	}
	composePath := a.Paths.ComposePath()
	if !proc.FileExists(composePath) {
		return false, nil
	}
	_, published, err := dockerStackRunsPublishedImage(composePath)
	if err != nil {
		return false, err
	}
	// An explicit --image-tag names the image directly, so the refresh pulls
	// whatever the target ref looks like.
	return published && (imageTag != "" || install.IsReleaseVersion(ref)), nil
}

// dockerStackImageLagsRecord reports whether a Docker stack running a published
// image is on an older tag than the version the manifest records for it.
//
// An update that filed its target ref without moving the compose file leaves
// exactly that mismatch, and the recorded version then makes every later run
// answer "already up to date" while the containers stay on the old image. The
// deployed tag is readable, so the staleness is detectable rather than
// permanent.
//
// Conservative: anything it cannot read or compare answers false, so it only
// ever adds a refresh that is genuinely owed.
func (a *app) dockerStackImageLagsRecord(manifest *config.Manifest) bool {
	if manifest.Mode != config.ModeDocker {
		return false
	}
	recorded := manifest.ResolvedStackRef()
	if recorded == "" {
		return false
	}
	composePath := a.Paths.ComposePath()
	if !proc.FileExists(composePath) {
		return false
	}
	current, published, err := dockerStackRunsPublishedImage(composePath)
	if err != nil || !published {
		return false
	}
	_, tag, digest := install.SplitImageRef(current)
	if digest != "" || tag == "" {
		// A digest pin names no version to compare, and the refresh would be
		// refused anyway.
		return false
	}
	return update.NewerAvailable(tag, recorded)
}

// checkDockerRefreshPossible reports whether a Docker stack's image can be
// brought to ref at all, without doing any of it.
//
// Every refusal the stack half can raise from state alone is raised here
// instead, before the CLI half swaps the binaries: a refusal after the swap
// leaves the CLI ahead of the stack, and because the cause is static, every
// retry fails the same way with no flag to work around it.
func (a *app) checkDockerRefreshPossible(mode, ref string, pinned bool, imageTag string) error {
	if mode != config.ModeDocker {
		return nil
	}
	composePath := a.Paths.ComposePath()
	if !proc.FileExists(composePath) {
		return nil
	}
	current, published, err := dockerStackRunsPublishedImage(composePath)
	if err != nil {
		return err
	}
	if !published {
		if imageTag != "" {
			return fmt.Errorf(
				"--image-tag applies to a stack running a published image; this one runs %s, built from source. "+
					"Reinstall with `jenticctl install --image-tag %s` to move it onto a published image",
				install.AppImageTag, imageTag)
		}
		return nil
	}
	// An explicit override is the operator naming the image themselves, which is
	// what gets a digest- or tag-pinned stack past the refusals below.
	if imageTag != "" {
		if _, err := install.ApplyImageOverride(current, imageTag); err != nil {
			return err
		}
		return nil
	}
	if !install.IsReleaseVersion(ref) {
		if !pinned {
			return errUnpinnedNonReleaseOnPulledStack(current, ref)
		}
		// A pinned non-release ref converts the stack to a from-source build,
		// which needs no published image for ref.
		return nil
	}
	// The pull route resolves the new reference off the deployed one; a digest
	// or an operator's own tag pin cannot be moved automatically.
	if _, err := install.SwapImageTag(current, ref); err != nil {
		return err
	}
	return nil
}

// refreshDockerStackImage brings the compose stack's app image up to ref by the
// route the install took, and leaves the compose file naming whatever the stack
// should now run.
//
// A stack running a published image pulls the image for ref — on the registry
// and repository it already names — and is repointed at it. One built from
// source is rebuilt, and its compose file already names the fixed local tag.
func (a *app) refreshDockerStackImage(composePath, ref string, pinned bool, imageTag string) (dockerRefreshRoute, error) {
	current, published, err := dockerStackRunsPublishedImage(composePath)
	if err != nil {
		// Every step after this one reads the same file and cannot parse it
		// either, so failing here costs the operator a pointless image build.
		return routeRebuilt, fmt.Errorf("%w — fix it, or reinstall with `jenticctl install`", err)
	}

	if published {
		if imageTag != "" || install.IsReleaseVersion(ref) {
			return routePulled, a.pullDockerStackImage(composePath, current, ref, imageTag)
		}
		// A ref with no published image can only be served by building, which
		// converts the stack to a from-source one for good. Do that only when
		// the operator pinned the ref themselves: an unpinned ref is a fallback
		// (`manifest.Ref` when the release lookup fails — a rate limit or an
		// offline host), and a transient network error must not silently change
		// how the stack is deployed.
		if !pinned {
			return routeRebuilt, errUnpinnedNonReleaseOnPulledStack(current, ref)
		}
		fmt.Fprintln(a.Out, theme.Warnf(
			"warning: %s has no published image, so the stack is rebuilt from source and will be "+
				"built from source from now on; reinstall to go back to published images", ref))
	}

	plan := install.PlanLocalBuild(a.Paths.VenvPath(), a.Paths.SrcPath()).AtRef(ref, pinned)
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, plan.RenderDockerBuildHeader())
	if err := buildStackImages(plan, a.Out); err != nil {
		return routeRebuilt, fmt.Errorf("image build failed: %w", err)
	}
	route := routeRebuilt
	if published {
		changed, err := install.SetComposeAppImage(composePath, install.AppImageTag)
		if err != nil {
			return routeRebuilt, fmt.Errorf("point the compose stack at %s: %w", install.AppImageTag, err)
		}
		if changed {
			fmt.Fprintln(a.Out, theme.Dimf("  compose now runs %s", install.AppImageTag))
			route = routeConverted
		}
	}
	return route, nil
}

// errUnpinnedNonReleaseOnPulledStack is the refusal to convert a stack that
// runs a published image into a from-source one on a ref the operator did not
// pin. Shared with updateE, which checks the same precondition BEFORE the CLI
// half runs: refusing only here would swap the binaries first and then fail on
// something no retry can satisfy.
func errUnpinnedNonReleaseOnPulledStack(current, ref string) error {
	return fmt.Errorf(
		"the stack runs the published image %s, but the update target %q is not a release "+
			"(the latest release could not be looked up, so the tracked ref was used). "+
			"Re-run when the release lookup works, or pass --ref <version> to pin a release",
		current, ref)
}

// pullDockerStackImage pulls the image for ref and repoints the compose file at
// it, keeping the registry and repository current already names.
func (a *app) pullDockerStackImage(composePath, current, ref, imageTag string) error {
	// The tag comes from ref and the repository from the deployed reference —
	// never from this binary's own version or the image environment overrides.
	// The CLI half swaps the binary on disk and this process carries on as the
	// OLD one, so its version still names the image the stack already runs; and
	// re-deriving the repository would move a mirrored install to another
	// registry. Deriving the tag from ref alone also keeps the ref that
	// recordStackBuild files true of the image actually installed.
	resolve := func() (string, error) {
		if imageTag != "" {
			// The operator named the image, so an explicit override moves a pin
			// SwapImageTag would refuse to move on its own.
			return install.ApplyImageOverride(current, imageTag)
		}
		return install.SwapImageTag(current, ref)
	}
	image, err := resolve()
	if err != nil {
		return err
	}
	fmt.Fprintln(a.Out)
	fmt.Fprint(a.Out, install.RenderPullHeader(image))
	if err := pullAppImage(a.Out, image); err != nil {
		return fmt.Errorf("%w\nthe stack still runs %s; no change has been made", err, current)
	}
	changed, err := install.SetComposeAppImage(composePath, image)
	if err != nil {
		return fmt.Errorf("point the compose stack at %s: %w", image, err)
	}
	if changed {
		fmt.Fprintln(a.Out, theme.Dimf("  compose now runs %s", image))
	}
	return nil
}

// stopLocalIfRunning stops the local app if it is running and reports whether it
// was. It is separate from the restart so the update flow can stop the app
// BEFORE snapshotting + migrating the SQLite database — migrating a live,
// WAL-mode DB and then trying to roll back a .db whose committed pages still
// live in an uncheckpointed WAL is exactly the inconsistent half-state the
// rollback net is meant to prevent.
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

// updateVerdict is the top-line summary of where each half stands against the
// latest release. The CLI and the stack advance independently (a stack half
// can fail after the CLI half swapped, #976), so "up to date" requires both.
type updateVerdict struct {
	cliBehind, stackBehind, unknown bool
}

// computeVerdict compares each half against latest. stackVersion is the ref
// the stack was last built from (the caller falls back to the CLI version for
// manifests that predate stack tracking).
func computeVerdict(cliVersion, stackVersion, latest string) updateVerdict {
	if cliVersion == "" || cliVersion == "none" {
		return updateVerdict{unknown: true}
	}
	return updateVerdict{
		cliBehind:   update.NewerAvailable(cliVersion, latest),
		stackBehind: stackVersion != "" && update.NewerAvailable(stackVersion, latest),
	}
}

// message words the verdict, naming whichever half is behind.
func (v updateVerdict) message(cliVersion, stackVersion, latest string) string {
	switch {
	case v.unknown:
		return "Installed version is unknown; cannot compare. Latest is " + latest + "."
	case v.cliBehind && v.stackBehind:
		if strings.TrimPrefix(cliVersion, "v") == strings.TrimPrefix(stackVersion, "v") {
			return fmt.Sprintf("Update available: %s → %s", cliVersion, latest)
		}
		return fmt.Sprintf("Update available: CLI %s and stack %s → %s", cliVersion, stackVersion, latest)
	case v.cliBehind && stackVersion == "":
		return fmt.Sprintf("CLI update available: %s → %s", cliVersion, latest)
	case v.cliBehind:
		return fmt.Sprintf("CLI update available: %s → %s (the stack is already at %s)", cliVersion, latest, latest)
	case v.stackBehind:
		return fmt.Sprintf("Stack update available: %s → %s (the CLI is already at %s)", stackVersion, latest, latest)
	default:
		return fmt.Sprintf("Up to date (%s).", latest)
	}
}

// printVerdict reports up-to-date / update-available for the CLI and the stack
// against the latest release tag, compared as semver.
func (a *app) printVerdict(cliVersion, stackVersion, latest string) {
	v := computeVerdict(cliVersion, stackVersion, latest)
	msg := v.message(cliVersion, stackVersion, latest)
	switch {
	case v.unknown:
		fmt.Fprintln(a.Out, theme.Warn.Render(msg))
	case v.cliBehind || v.stackBehind:
		fmt.Fprintln(a.Out, theme.Accent.Render(msg))
	default:
		fmt.Fprintln(a.Out, theme.Successf("%s", msg))
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

// isStampedVersion reports whether a build-time version was stamped by a real
// build rather than left at the plain `go build` placeholder.
func isStampedVersion(v string) bool {
	return v != "" && v != "dev" && v != "none"
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}
