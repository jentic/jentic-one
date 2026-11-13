package ctlcmd

import (
	"context"
	"fmt"
	"os"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

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
		fmt.Fprint(a.Out, install.RenderPullHeader(appImage))
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
// error. A first failure can leave behind a half-initialized data
// volume that poisons every retry (#992 item 3 — see install/recover.go).
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
