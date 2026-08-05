package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/jentic/jentic-one/cli/internal/update"
	"github.com/spf13/cobra"
)

// updateNudgeInterval throttles the background update check: an ordinary command
// probes GitHub for a newer release at most once per this window, caching the
// result so the common case is a cheap file read with no network at all. Matches
// the ~daily cadence of gh/npm/brew's update notifiers.
const updateNudgeInterval = 24 * time.Hour

// updateNudgeTimeout bounds the on-stale GitHub probe so a slow/offline network
// can never noticeably delay the command the user actually ran. On timeout we
// simply skip the nudge (and try again after the interval), mirroring the
// bounded server probe in the branded header.
const updateNudgeTimeout = 2 * time.Second

// nudgeLatestTag is the seam for resolving the latest release tag during the
// update nudge. Overridable in tests so the nudge can be exercised without a
// network/git dependency; production uses the same git-ls-remote path as
// `jenticctl update`.
var nudgeLatestTag = update.LatestReleaseTag

// updateCheckCache is the on-disk throttle record (~/.jentic/update-check.json).
// CheckedAt gates the once-per-interval probe; LatestTag is the last resolved
// release so a fast command still nudges from cache without re-probing.
type updateCheckCache struct {
	CheckedAt time.Time `json:"checked_at"`
	LatestTag string    `json:"latest_tag,omitempty"`
}

// maybeNudgeUpdate prints a one-line "update available" nag to stderr when a
// newer release than the running CLI exists. It is deliberately conservative and
// best-effort so it never gets in the way of the actual command:
//
//   - silent for non-interactive stderr (pipes/scripts/CI stay clean), when
//     JENTIC_NO_UPDATE_NOTIFIER / CI / JENTIC_NO_BANNER is set, and for the
//     commands that own their update messaging or output (update/help/etc).
//   - the freshness probe runs at most once per updateNudgeInterval (cached to
//     ~/.jentic/update-check.json) and is bounded by updateNudgeTimeout, so the
//     common path is a cheap file read and a slow network never blocks the run.
//   - every error (no manifest, offline, unparseable) is swallowed: the nudge
//     is a courtesy, not a gate.
//
// It is printed to a.Err (stderr) so stdout — which may be piped/captured —
// stays free of the notice, matching gh/npm/glab.
func (a *App) maybeNudgeUpdate(cmd *cobra.Command) {
	if !updateNudgeEnabled(cmd) {
		return
	}

	cliVersion := installedCLIVersion(a.Paths)
	repo := resolveNudgeRepo(a.Paths)

	tag, ok := a.latestReleaseForNudge(cmd.Context(), repo)
	if !ok {
		return
	}
	if !update.NewerAvailable(cliVersion, tag) {
		return
	}
	fmt.Fprintln(a.Err, theme.Accent.Render(
		fmt.Sprintf("Update available: %s → %s — run `jenticctl update`", cliVersion, tag),
	))
}

// updateNudgeEnabled reports whether the update nudge should run for cmd. It
// mirrors the banner's suppression (non-TTY, JENTIC_NO_BANNER, and the
// help/completion/install/update/execute trees) and additionally honours the
// conventional NO_UPDATE_NOTIFIER-style opt-outs (JENTIC_NO_UPDATE_NOTIFIER, CI).
func updateNudgeEnabled(cmd *cobra.Command) bool {
	// stderr must be a terminal: the nudge goes to stderr, and we don't want it
	// polluting captured logs even when stdout happens to be a TTY.
	if !term.IsTerminal(os.Stderr.Fd()) {
		return false
	}
	return updateNudgeAllowed(cmd)
}

// updateNudgeAllowed is the TTY-independent half of the gate: env opt-outs plus
// the command-skip set. Split out from the terminal check so the policy is
// unit-testable without a real TTY.
func updateNudgeAllowed(cmd *cobra.Command) bool {
	if os.Getenv("JENTIC_NO_UPDATE_NOTIFIER") != "" || os.Getenv("CI") != "" {
		return false
	}
	if os.Getenv("JENTIC_NO_BANNER") != "" {
		return false
	}
	return !bannerSkip(cmd)
}

// latestReleaseForNudge returns the latest release tag to compare against,
// preferring a fresh (within-interval) cached value and otherwise probing GitHub
// once (bounded by updateNudgeTimeout) and caching the result. ok is false when
// no tag could be determined (offline, no repo, etc.) — the caller then stays
// silent. The cache timestamp is advanced on every probe attempt, success or
// not, so a persistent failure re-probes at most once per interval rather than
// on every command.
func (a *App) latestReleaseForNudge(ctx context.Context, repo string) (string, bool) {
	cache, _ := loadUpdateCheck(a.Paths)
	if cache.LatestTag != "" && time.Since(cache.CheckedAt) < updateNudgeInterval {
		return cache.LatestTag, true
	}

	probeCtx, cancel := context.WithTimeout(ctx, updateNudgeTimeout)
	defer cancel()

	tag, err := nudgeLatestTag(probeCtx, repo, os.Getenv("GITHUB_TOKEN"))
	next := updateCheckCache{CheckedAt: time.Now()}
	if err != nil {
		// Probe failed: persist the attempt time (throttle the retry) but keep
		// any previously known tag so we can still nudge from it.
		next.LatestTag = cache.LatestTag
		_ = saveUpdateCheck(a.Paths, next)
		if cache.LatestTag != "" {
			return cache.LatestTag, true
		}
		return "", false
	}
	next.LatestTag = tag
	_ = saveUpdateCheck(a.Paths, next)
	return tag, true
}

// resolveNudgeRepo picks the repo to check: the installed manifest's repo when
// present, else the built-in default. A missing/unreadable manifest is not an
// error here — an un-installed CLI still tracks jentic/jentic-one.
func resolveNudgeRepo(paths config.Paths) string {
	if m, _, err := config.LoadManifest(paths); err == nil {
		return m.ResolvedRepo()
	}
	return config.DefaultRepo
}

// installedCLIVersion returns the version to compare against latest: the
// manifest's recorded CLI version when present, else the build-time version.
func installedCLIVersion(paths config.Paths) string {
	if m, _, err := config.LoadManifest(paths); err == nil {
		return firstNonEmpty(m.CLIVersion, version)
	}
	return version
}

// loadUpdateCheck reads the update-notify cache. A missing/corrupt file yields a
// zero-value cache (treated as "never checked"), never an error the caller must
// handle — the nudge degrades to a fresh probe.
func loadUpdateCheck(paths config.Paths) (updateCheckCache, error) {
	var c updateCheckCache
	data, err := os.ReadFile(paths.UpdateCheckPath()) //nolint:gosec // path derived from the CLI's own JENTIC_HOME, not user input.
	if err != nil {
		return c, err
	}
	if err := json.Unmarshal(data, &c); err != nil {
		return updateCheckCache{}, err
	}
	return c, nil
}

// saveUpdateCheck writes the update-notify cache (0600). Best-effort: a write
// failure only costs a redundant probe next time, so callers ignore the error.
func saveUpdateCheck(paths config.Paths, c updateCheckCache) error {
	if _, err := paths.Ensure(paths.Dir()); err != nil {
		return err
	}
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(paths.UpdateCheckPath(), append(data, '\n'), 0o600)
}
