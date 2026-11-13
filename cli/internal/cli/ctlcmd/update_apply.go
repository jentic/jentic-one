package ctlcmd

import (
	"context"
	"fmt"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/jentic/jentic-one/cli/internal/update"
)

// updatePlan holds the resolved targets and execution flags for an update run.
type updatePlan struct {
	manifest     *config.Manifest
	repo         string
	ref          string
	latest       string
	ctlTarget    string
	cliVersion   string
	stackVersion string
	stackImg     dockerStackImageState
	pinned       bool
	latestKnown  bool
	brewManaged  bool
	doCLI        bool
	doStack      bool
	skipConfirm  bool
}

// applyUpdate executes a decided update plan across confirmation, preflight,
// CLI replacement, and stack refresh.
func (a *app) applyUpdate(ctx context.Context, plan updatePlan) error {
	if !plan.skipConfirm {
		ok, err := confirmApply(plan.doCLI, plan.doStack, plan.brewManaged, plan.repo, plan.ref)
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
	if plan.doCLI && plan.doStack && plan.manifest.Mode == config.ModeDocker && proc.FileExists(a.Paths.ComposePath()) {
		if err := a.preflightDockerStack(ctx); err != nil {
			return err
		}
		preflighted = true
	}

	cliUpdated := false
	cliLanded := ""
	if plan.doCLI {
		var landed config.CLIUpdate
		if plan.brewManaged {
			caskVersion, err := upgradeBrewCLI(a, ctx, plan.latest, plan.latestKnown)
			if err != nil {
				return err
			}
			// brew is a clean no-op while the cask lags the release; only a cask
			// that actually moved past the recorded version counts as an update.
			// An unreadable cask version is assumed to have moved (brew succeeded).
			cliUpdated = caskVersion == "" || update.NewerAvailable(plan.cliVersion, caskVersion)
			if cliUpdated && caskVersion != "" {
				landed = config.CLIUpdate{Version: "v" + strings.TrimPrefix(caskVersion, "v")}
			}
		} else {
			var err error
			if landed, err = swapCLI(a, ctx, plan.repo, plan.ref, plan.ctlTarget); err != nil {
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
	if plan.doStack {
		if err := rebuildStack(a, ctx, plan.manifest.Mode, plan.manifest.DB, plan.ref, plan.pinned, preflighted); err != nil {
			if cliUpdated {
				priorStack := plan.stackVersion
				if plan.stackImg.lagsRecord {
					priorStack = plan.stackImg.releaseTag
				}
				return newStackDivergedError(err, cliTargetLabel(plan.brewManaged, cliLanded), plan.ref, priorStack, stackRetryCommand(plan.ref, plan.pinned))
			}
			return err
		}
		// Only now is the stack genuinely at `ref`. Recording it earlier (or
		// alongside the CLI half) is what let a failed/skipped stack rebuild
		// advertise itself as current and wedge every later update (#943).
		a.recordStackBuild(plan.ref)
	}
	return nil
}
