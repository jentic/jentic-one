package cmd

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/spf13/cobra"
)

// stubNudgeTag swaps the release-tag probe seam for the duration of a test.
func stubNudgeTag(t *testing.T, fn func(ctx context.Context, repo, token string) (string, error)) {
	t.Helper()
	orig := nudgeLatestTag
	t.Cleanup(func() { nudgeLatestTag = orig })
	nudgeLatestTag = fn
}

// TestLatestReleaseForNudgeProbesThenCaches verifies the once-per-interval
// throttle: the first call probes GitHub (seam) and writes the cache; the second
// is served from disk without re-probing.
func TestLatestReleaseForNudgeProbesThenCaches(t *testing.T) {
	app := testApp(t)
	var probes int
	stubNudgeTag(t, func(context.Context, string, string) (string, error) {
		probes++
		return "v9.9.9", nil
	})

	tag, ok := app.latestReleaseForNudge(context.Background(), config.DefaultRepo)
	if !ok || tag != "v9.9.9" {
		t.Fatalf("first probe = (%q, %v), want (v9.9.9, true)", tag, ok)
	}
	if probes != 1 {
		t.Fatalf("probes after first call = %d, want 1", probes)
	}

	tag, ok = app.latestReleaseForNudge(context.Background(), config.DefaultRepo)
	if !ok || tag != "v9.9.9" {
		t.Fatalf("cached read = (%q, %v), want (v9.9.9, true)", tag, ok)
	}
	if probes != 1 {
		t.Errorf("probes after cached read = %d, want 1 (served from cache)", probes)
	}
}

// TestLatestReleaseForNudgeRefreshesWhenStale confirms a cache older than the
// interval triggers a fresh probe.
func TestLatestReleaseForNudgeRefreshesWhenStale(t *testing.T) {
	app := testApp(t)
	if err := saveUpdateCheck(app.Paths, updateCheckCache{
		CheckedAt: time.Now().Add(-2 * updateNudgeInterval),
		LatestTag: "v1.0.0",
	}); err != nil {
		t.Fatalf("seed cache: %v", err)
	}
	stubNudgeTag(t, func(context.Context, string, string) (string, error) {
		return "v2.0.0", nil
	})

	tag, ok := app.latestReleaseForNudge(context.Background(), config.DefaultRepo)
	if !ok || tag != "v2.0.0" {
		t.Fatalf("stale refresh = (%q, %v), want (v2.0.0, true)", tag, ok)
	}
}

// TestLatestReleaseForNudgeFailureKeepsPriorTag confirms a failed probe still
// nudges from the previously cached tag (offline shouldn't drop the notice) and
// advances the timestamp so we don't re-probe on the very next command.
func TestLatestReleaseForNudgeFailureKeepsPriorTag(t *testing.T) {
	app := testApp(t)
	if err := saveUpdateCheck(app.Paths, updateCheckCache{
		CheckedAt: time.Now().Add(-2 * updateNudgeInterval),
		LatestTag: "v1.2.3",
	}); err != nil {
		t.Fatalf("seed cache: %v", err)
	}
	stubNudgeTag(t, func(context.Context, string, string) (string, error) {
		return "", errors.New("offline")
	})

	tag, ok := app.latestReleaseForNudge(context.Background(), config.DefaultRepo)
	if !ok || tag != "v1.2.3" {
		t.Fatalf("failed probe = (%q, %v), want prior (v1.2.3, true)", tag, ok)
	}
	got, err := loadUpdateCheck(app.Paths)
	if err != nil {
		t.Fatalf("reload cache: %v", err)
	}
	if time.Since(got.CheckedAt) > updateNudgeInterval {
		t.Errorf("timestamp not advanced on failed probe; got %v", got.CheckedAt)
	}
	if got.LatestTag != "v1.2.3" {
		t.Errorf("prior tag not preserved on failure; got %q", got.LatestTag)
	}
}

// TestLatestReleaseForNudgeNoCacheNoNetwork confirms a first-ever probe that
// fails (offline, no prior cache) reports "no tag" so the caller stays silent.
func TestLatestReleaseForNudgeNoCacheNoNetwork(t *testing.T) {
	app := testApp(t)
	stubNudgeTag(t, func(context.Context, string, string) (string, error) {
		return "", errors.New("offline")
	})

	if tag, ok := app.latestReleaseForNudge(context.Background(), config.DefaultRepo); ok {
		t.Errorf("first-run offline = (%q, true), want silent (false)", tag)
	}
}

// TestLatestReleaseForNudgeThrottlesRecentFailure is the regression test for the
// air-gapped case: a recent (within-interval) cache with an empty tag — i.e. a
// prior probe that resolved nothing — must be trusted verbatim so we neither
// re-probe (paying the timeout on every command) nor nudge.
func TestLatestReleaseForNudgeThrottlesRecentFailure(t *testing.T) {
	app := testApp(t)
	if err := saveUpdateCheck(app.Paths, updateCheckCache{
		CheckedAt: time.Now(),
		LatestTag: "",
	}); err != nil {
		t.Fatalf("seed cache: %v", err)
	}
	stubNudgeTag(t, func(context.Context, string, string) (string, error) {
		t.Fatalf("probed within the throttle window; want cache trusted")
		return "", nil
	})

	if tag, ok := app.latestReleaseForNudge(context.Background(), config.DefaultRepo); ok || tag != "" {
		t.Errorf("recent empty cache = (%q, %v), want silent (\"\", false)", tag, ok)
	}
}

// TestUpdateNudgeAllowedRespectsOptOut confirms the conventional env opt-outs
// suppress the nudge regardless of the command.
func TestUpdateNudgeAllowedRespectsOptOut(t *testing.T) {
	for _, env := range []string{"JENTIC_NO_UPDATE_NOTIFIER", "CI", "JENTIC_NO_BANNER"} {
		t.Run(env, func(t *testing.T) {
			t.Setenv(env, "1")
			cmd := newStatusCmd(testApp(t))
			if updateNudgeAllowed(cmd) {
				t.Errorf("nudge allowed with %s set, want suppressed", env)
			}
		})
	}
}

// TestUpdateNudgeAllowedSkipsOwnMessagingCommands confirms the nudge is silent
// for the commands that own their update messaging/output (update, execute) —
// the same set the banner skips.
func TestUpdateNudgeAllowedSkipsOwnMessagingCommands(t *testing.T) {
	// CI is commonly set in the test environment; clear the opt-outs so this test
	// isolates the command-skip behaviour rather than the env opt-out.
	t.Setenv("CI", "")
	t.Setenv("JENTIC_NO_UPDATE_NOTIFIER", "")
	t.Setenv("JENTIC_NO_BANNER", "")
	app := testApp(t)
	for _, build := range []func(*App) *cobra.Command{newUpdateCmd, newExecuteCmd} {
		cmd := build(app)
		if updateNudgeAllowed(cmd) {
			t.Errorf("nudge allowed for own-messaging command %q, want skipped", cmd.Name())
		}
	}
}

// TestUpdateNudgeAllowedForOrdinaryCommand confirms an ordinary command (status)
// is allowed when no opt-out is set — the positive case that makes the skip
// tests meaningful.
func TestUpdateNudgeAllowedForOrdinaryCommand(t *testing.T) {
	t.Setenv("CI", "")
	t.Setenv("JENTIC_NO_UPDATE_NOTIFIER", "")
	t.Setenv("JENTIC_NO_BANNER", "")
	if !updateNudgeAllowed(newStatusCmd(testApp(t))) {
		t.Error("nudge not allowed for ordinary command `status`, want allowed")
	}
}
