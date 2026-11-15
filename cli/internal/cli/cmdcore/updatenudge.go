package cmdcore

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/theme"
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

// firstNonEmpty returns the first non-empty string of a, b (or "" if both are
// empty). A private copy lives here so the update nudge (cmdcore) doesn't depend
// on the ctl-side update command where the shared helper otherwise lives.
func firstNonEmpty(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

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
	// The update nudge depends on the installer/lifecycle probe (internal/update),
	// which cmdcore must not import. The ctl tree injects these seams; the api tree
	// leaves them nil, making the nudge a no-op for the `jentic` binary.
	if a.NudgeLatestTag == nil || a.NewerVersionAvailable == nil {
		return
	}
	if !updateNudgeEnabled(cmd) {
		return
	}

	cliVersion := installedCLIVersion(a.Paths)
	repo := resolveNudgeRepo(a.Paths)

	tag, ok := a.latestReleaseForNudge(cmd.Context(), repo)
	if !ok {
		return
	}
	if !a.NewerVersionAvailable(cliVersion, tag) {
		return
	}
	nudgeCmd := a.NudgeCommand
	if nudgeCmd == "" {
		nudgeCmd = "jenticctl update"
	}
	st := theme.StylesFromContext(cmd.Context())
	fmt.Fprintln(a.Err, st.Accent.Render(
		fmt.Sprintf("Update available: %s → %s — run `%s`", cliVersion, tag, nudgeCmd),
	))
}

// stderrIsTTY is the update nudge's terminal probe; a var matching stdoutIsTTY
// in header.go so tests can exercise maybeNudgeUpdate without a real PTY.
var stderrIsTTY = func() bool { return term.IsTerminal(os.Stderr.Fd()) }

// executablePath locates the running binary; a var so tests can drive
// Homebrew-tree and non-Homebrew path layouts without spawning a subprocess.
var executablePath = os.Executable

// updateNudgeEnabled reports whether the update nudge should run for cmd. It
// mirrors the banner's suppression (non-TTY, JENTIC_NO_BANNER, and the
// help/completion/install/update/execute trees) and additionally honours the
// conventional NO_UPDATE_NOTIFIER-style opt-outs (JENTIC_NO_UPDATE_NOTIFIER, CI).
func updateNudgeEnabled(cmd *cobra.Command) bool {
	// stderr must be a terminal: the nudge goes to stderr, and we don't want it
	// polluting captured logs even when stdout happens to be a TTY.
	if !stderrIsTTY() {
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
	if time.Since(cache.CheckedAt) < updateNudgeInterval {
		// Within the throttle window we trust the cache verbatim — even a
		// recorded failure (empty tag). This is what keeps an air-gapped box
		// from re-probing (and eating the timeout) on every command; ok is
		// false when the last attempt resolved no tag.
		return cache.LatestTag, cache.LatestTag != ""
	}

	probeCtx, cancel := context.WithTimeout(ctx, updateNudgeTimeout)
	defer cancel()

	tag, err := a.NudgeLatestTag(probeCtx, repo, os.Getenv("GITHUB_TOKEN"))
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

// isStampedVersion reports whether v was stamped by a release/installer build
// rather than left at a plain `go build` placeholder.
func isStampedVersion(v string) bool {
	return v != "" && v != "dev" && v != "none"
}

// brewTreeExecutable reports whether the running binary resolves into a
// Homebrew-managed tree. Because the update nudge runs on every interactive
// command, detection uses symlink resolution and path inspection only — never
// spawning `brew --prefix`.
func brewTreeExecutable() bool {
	exe, err := executablePath()
	if err != nil || exe == "" {
		return false
	}
	if resolved, err := filepath.EvalSymlinks(exe); err == nil {
		exe = resolved
	}
	return brewTreePath(exe)
}

// brewTreePath reports whether a symlink-resolved executable path sits inside a
// Homebrew install tree across macOS Apple Silicon (/opt/homebrew), Intel macOS
// (/usr/local), Linuxbrew (/home/linuxbrew/.linuxbrew or ~/.linuxbrew), and
// custom prefixes:
//   - any path containing a Cellar, Caskroom, or .linuxbrew segment, or
//   - a path under /opt/homebrew/ or Homebrew's /usr/local/opt/ link tree.
//
// Shared POSIX directories such as /usr/local/bin without a Cellar/Caskroom
// target are excluded so a source/release install in /usr/local/bin (or a
// symlink there resolving to ~/.jentic/bin) is not misclassified as Homebrew.
func brewTreePath(resolved string) bool {
	slash := filepath.ToSlash(filepath.Clean(resolved))
	if hasPathSegment(slash, "Cellar") || hasPathSegment(slash, "Caskroom") || hasPathSegment(slash, ".linuxbrew") {
		return true
	}
	return strings.HasPrefix(slash, "/opt/homebrew/") || strings.HasPrefix(slash, "/usr/local/opt/")
}

// hasPathSegment reports whether slashPath contains seg as a whole path segment.
func hasPathSegment(slashPath, seg string) bool {
	for _, part := range strings.Split(slashPath, "/") {
		if part == seg {
			return true
		}
	}
	return false
}

// installedCLIVersion returns the version to compare against latest. A
// Homebrew-managed binary is upgraded out-of-band (`brew upgrade jentic`), so
// any cli_version recorded in install.json can lag the running executable; when
// the running binary resolves into a brew tree and carries a build-stamped
// version, that stamped version is authoritative (#1792). Otherwise the
// manifest's recorded cli_version wins when present, falling back to the
// build-time version.
func installedCLIVersion(paths config.Paths) string {
	if isStampedVersion(version) && brewTreeExecutable() {
		return version
	}
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
	data, err := os.ReadFile(paths.UpdateCheckPath())
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
