package cmdcore

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/update"
	"github.com/spf13/cobra"
)

// stubNudgeTag wires the App's release-tag probe seam for the duration of a
// test. The split moved this from a package var to a per-App func field
// (cmdcore.App.NudgeLatestTag), so each test drives its own instance.
func stubNudgeTag(t *testing.T, app *App, fn func(ctx context.Context, repo, token string) (string, error)) {
	t.Helper()
	orig := app.NudgeLatestTag
	t.Cleanup(func() { app.NudgeLatestTag = orig })
	app.NudgeLatestTag = fn
}

// TestLatestReleaseForNudgeProbesThenCaches verifies the once-per-interval
// throttle: the first call probes GitHub (seam) and writes the cache; the second
// is served from disk without re-probing.
func TestLatestReleaseForNudgeProbesThenCaches(t *testing.T) {
	app := testApp(t)
	var probes int
	stubNudgeTag(t, app, func(context.Context, string, string) (string, error) {
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
	stubNudgeTag(t, app, func(context.Context, string, string) (string, error) {
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
	stubNudgeTag(t, app, func(context.Context, string, string) (string, error) {
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
	stubNudgeTag(t, app, func(context.Context, string, string) (string, error) {
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
	stubNudgeTag(t, app, func(context.Context, string, string) (string, error) {
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
	runnable := func(name string) *cobra.Command {
		return &cobra.Command{Use: name, RunE: func(*cobra.Command, []string) error { return nil }}
	}
	for _, env := range []string{"JENTIC_NO_UPDATE_NOTIFIER", "CI", "JENTIC_NO_BANNER"} {
		t.Run(env, func(t *testing.T) {
			t.Setenv(env, "1")
			if updateNudgeAllowed(runnable("status")) {
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
	for _, name := range []string{"update", "execute"} {
		cmd := &cobra.Command{Use: name, RunE: func(*cobra.Command, []string) error { return nil }}
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
	status := &cobra.Command{Use: "status", RunE: func(*cobra.Command, []string) error { return nil }}
	if !updateNudgeAllowed(status) {
		t.Error("nudge not allowed for ordinary command `status`, want allowed")
	}
}

// stubNudgeEnv configures interactive update-nudge seams (TTY, version,
// executable path, comparator, and opt-out env vars) for maybeNudgeUpdate tests.
func stubNudgeEnv(t *testing.T, app *App, exe, stampedVersion, latestTag string) {
	t.Helper()
	t.Setenv("CI", "")
	t.Setenv("JENTIC_NO_UPDATE_NOTIFIER", "")
	t.Setenv("JENTIC_NO_BANNER", "")

	origTTY := stderrIsTTY
	origExe := executablePath
	origVer := version
	t.Cleanup(func() {
		stderrIsTTY = origTTY
		executablePath = origExe
		version = origVer
	})

	stderrIsTTY = func() bool { return true }
	executablePath = func() (string, error) { return exe, nil }
	version = stampedVersion
	app.NewerVersionAvailable = update.NewerAvailable
	app.NudgeCommand = "jenticctl update"
	stubNudgeTag(t, app, func(context.Context, string, string) (string, error) {
		return latestTag, nil
	})
}

func saveNudgeManifest(t *testing.T, paths config.Paths, cliVersion, binaryPath string) {
	t.Helper()
	m := &config.Manifest{
		Repo:       config.DefaultRepo,
		Ref:        cliVersion,
		StackRef:   cliVersion,
		CLIVersion: cliVersion,
		BinaryPath: binaryPath,
		Mode:       config.ModeLocal,
	}
	if err := m.Save(paths); err != nil {
		t.Fatalf("save manifest: %v", err)
	}
}

func runnableStatusCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "status", RunE: func(*cobra.Command, []string) error { return nil }}
	cmd.SetContext(context.Background())
	return cmd
}

func TestBrewTreePath(t *testing.T) {
	tests := []struct {
		name string
		path string
		want bool
	}{
		{"apple silicon caskroom", "/opt/homebrew/Caskroom/jentic/0.28.0/jenticctl", true},
		{"apple silicon cellar", "/opt/homebrew/Cellar/jentic/0.28.0/bin/jenticctl", true},
		{"apple silicon opt", "/opt/homebrew/opt/jentic/bin/jenticctl", true},
		{"apple silicon bin", "/opt/homebrew/bin/jenticctl", true},
		{"intel macos caskroom", "/usr/local/Caskroom/jentic/0.28.0/jenticctl", true},
		{"intel macos cellar", "/usr/local/Cellar/jentic/0.28.0/bin/jenticctl", true},
		{"intel macos opt", "/usr/local/opt/jentic/bin/jenticctl", true},
		{"linuxbrew caskroom", "/home/linuxbrew/.linuxbrew/Caskroom/jentic/0.28.0/jenticctl", true},
		{"linuxbrew cellar", "/home/linuxbrew/.linuxbrew/Cellar/jentic/0.28.0/bin/jenticctl", true},
		{"linuxbrew opt", "/home/linuxbrew/.linuxbrew/opt/jentic/bin/jenticctl", true},
		{"linuxbrew bin", "/home/linuxbrew/.linuxbrew/bin/jenticctl", true},
		{"user linuxbrew", "/home/alice/.linuxbrew/bin/jenticctl", true},
		{"custom prefix cellar", "/custom/brew/Cellar/jentic/0.28.0/bin/jenticctl", true},
		{"custom prefix caskroom", "/custom/brew/Caskroom/jentic/0.28.0/jenticctl", true},
		{"usr local bin regular file", "/usr/local/bin/jenticctl", false},
		{"default installer dir", "/Users/u/.jentic/bin/jenticctl", false},
		{"linux fhs opt vendor", "/opt/jentic/bin/jenticctl", false},
		{"usr bin", "/usr/bin/jenticctl", false},
		{"cellar substring not segment", "/data/myCellar/bin/jenticctl", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := brewTreePath(tt.path); got != tt.want {
				t.Errorf("brewTreePath(%q) = %v, want %v", tt.path, got, tt.want)
			}
		})
	}
}

func TestBrewTreeExecutableResolvesSymlinks(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink layout test targets unix")
	}
	root := t.TempDir()

	// Layout 1: <prefix>/bin/jenticctl -> ../Caskroom/jentic/0.28.0/jenticctl
	caskBin := filepath.Join(root, "brew-cask", "Caskroom", "jentic", "0.28.0", "jenticctl")
	caskLink := filepath.Join(root, "brew-cask", "bin", "jenticctl")
	// Layout 2: <prefix>/opt/jentic -> ../Cellar/jentic/0.28.0
	cellarDir := filepath.Join(root, "brew-cellar", "Cellar", "jentic", "0.28.0")
	cellarBin := filepath.Join(cellarDir, "bin", "jenticctl")
	optDirLink := filepath.Join(root, "brew-cellar", "opt", "jentic")
	optBin := filepath.Join(optDirLink, "bin", "jenticctl")
	// Layout 3: non-brew PATH symlink (/usr/local/bin/jenticctl -> ~/.jentic/bin/jenticctl)
	sourceBin := filepath.Join(root, "home", ".jentic", "bin", "jenticctl")
	usrLocalLink := filepath.Join(root, "usr", "local", "bin", "jenticctl")

	for _, f := range []string{caskBin, cellarBin, sourceBin} {
		if err := os.MkdirAll(filepath.Dir(f), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(f, []byte("bin"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	for _, dir := range []string{filepath.Dir(caskLink), filepath.Dir(optDirLink), filepath.Dir(usrLocalLink)} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink(caskBin, caskLink); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(cellarDir, optDirLink); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(sourceBin, usrLocalLink); err != nil {
		t.Fatal(err)
	}

	origExe := executablePath
	t.Cleanup(func() { executablePath = origExe })

	for _, tc := range []struct {
		name string
		exe  string
		want bool
	}{
		{"cask bin symlink", caskLink, true},
		{"cellar opt symlink", optBin, true},
		{"source install symlinked in usr/local/bin", usrLocalLink, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			executablePath = func() (string, error) { return tc.exe, nil }
			if got := brewTreeExecutable(); got != tc.want {
				t.Errorf("brewTreeExecutable() for %q = %v, want %v", tc.exe, got, tc.want)
			}
		})
	}
}

// TestMaybeNudgeUpdateBrewCurrentBinaryIgnoresStaleManifest pins #1792: when a
// Homebrew-managed CLI is upgraded out-of-band (`brew upgrade jentic`), the
// running binary's stamped version overrides a stale cli_version in
// install.json without spawning `brew --prefix`, suppressing any false nudge.
func TestMaybeNudgeUpdateBrewCurrentBinaryIgnoresStaleManifest(t *testing.T) {
	var symlinkExe string
	if runtime.GOOS != "windows" {
		root := t.TempDir()
		realBin := filepath.Join(root, "Caskroom", "jentic", "0.28.0", "jenticctl")
		linkBin := filepath.Join(root, "bin", "jenticctl")
		if err := os.MkdirAll(filepath.Dir(realBin), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.MkdirAll(filepath.Dir(linkBin), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(realBin, []byte("bin"), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(realBin, linkBin); err != nil {
			t.Fatal(err)
		}
		symlinkExe = linkBin
	}

	cases := []struct {
		name    string
		exe     string
		stamped string
	}{
		{"apple silicon caskroom goreleaser stamp", "/opt/homebrew/Caskroom/jentic/0.28.0/jenticctl", "0.28.0"},
		{"apple silicon cellar v-prefixed stamp", "/opt/homebrew/Cellar/jentic/0.28.0/bin/jenticctl", "v0.28.0"},
		{"apple silicon opt", "/opt/homebrew/opt/jentic/bin/jenticctl", "0.28.0"},
		{"intel cellar", "/usr/local/Cellar/jentic/0.28.0/bin/jenticctl", "0.28.0"},
		{"intel caskroom", "/usr/local/Caskroom/jentic/0.28.0/jenticctl", "0.28.0"},
		{"intel opt", "/usr/local/opt/jentic/bin/jenticctl", "0.28.0"},
		{"linuxbrew caskroom", "/home/linuxbrew/.linuxbrew/Caskroom/jentic/0.28.0/jenticctl", "0.28.0"},
		{"linuxbrew cellar", "/home/linuxbrew/.linuxbrew/Cellar/jentic/0.28.0/bin/jenticctl", "0.28.0"},
	}
	if symlinkExe != "" {
		cases = append(cases, struct {
			name    string
			exe     string
			stamped string
		}{"resolved bin symlink into Caskroom", symlinkExe, "0.28.0"})
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			app := testApp(t)
			saveNudgeManifest(t, app.Paths, "v0.26.0", "/opt/homebrew/Caskroom/jentic/0.26.0/jenticctl")
			stubNudgeEnv(t, app, tc.exe, tc.stamped, "v0.28.0")

			// Poison PATH with a fake brew binary on Unix to prove the nudge path
			// never spawns `brew --prefix`.
			if runtime.GOOS != "windows" {
				binDir := t.TempDir()
				marker := filepath.Join(binDir, "brew-invoked")
				script := "#!/bin/sh\necho invoked > " + marker + "\nexit 1\n"
				if err := os.WriteFile(filepath.Join(binDir, "brew"), []byte(script), 0o755); err != nil {
					t.Fatal(err)
				}
				t.Setenv("PATH", binDir)
				t.Cleanup(func() {
					if _, err := os.Stat(marker); err == nil {
						t.Errorf("brew subprocess was spawned during maybeNudgeUpdate")
					}
				})
			}

			app.maybeNudgeUpdate(runnableStatusCmd())
			if got := app.Err.(*bytes.Buffer).String(); got != "" {
				t.Errorf("maybeNudgeUpdate emitted false nudge %q, want silent", got)
			}
		})
	}
}

// TestMaybeNudgeUpdateBrewOlderBinaryStillNudges confirms a genuinely outdated
// Homebrew binary still nudges, reporting the running binary's stamped version
// (rather than an even older manifest cli_version) and keeping the update hint.
func TestMaybeNudgeUpdateBrewOlderBinaryStillNudges(t *testing.T) {
	app := testApp(t)
	saveNudgeManifest(t, app.Paths, "v0.24.0", "/opt/homebrew/Caskroom/jentic/0.24.0/jenticctl")
	stubNudgeEnv(t, app, "/opt/homebrew/Caskroom/jentic/0.26.0/jenticctl", "0.26.0", "v0.28.0")

	app.maybeNudgeUpdate(runnableStatusCmd())
	got := app.Err.(*bytes.Buffer).String()
	want := "Update available: 0.26.0 → v0.28.0 — run `jenticctl update`"
	if !strings.Contains(got, want) {
		t.Errorf("maybeNudgeUpdate stderr = %q, want substring %q", got, want)
	}
}

// TestMaybeNudgeUpdateNonBrewAndUnstampedPreserved verifies non-brew installs
// continue preferring manifest cli_version and un-stamped ("dev") builds never
// clobber a recorded manifest cli_version.
func TestMaybeNudgeUpdateNonBrewAndUnstampedPreserved(t *testing.T) {
	tests := []struct {
		name            string
		exe             string
		stamped         string
		manifestVersion string
		wantNudge       string
	}{
		{
			name:            "non-brew stale manifest nudges even when dev build",
			exe:             "/Users/u/.jentic/bin/jenticctl",
			stamped:         "dev",
			manifestVersion: "v0.26.0",
			wantNudge:       "Update available: v0.26.0 → v0.28.0 — run `jenticctl update`",
		},
		{
			name:            "non-brew current manifest stays silent on dev build",
			exe:             "/Users/u/.jentic/bin/jenticctl",
			stamped:         "dev",
			manifestVersion: "v0.28.0",
			wantNudge:       "",
		},
		{
			name:            "non-brew regular file in usr/local/bin keeps manifest precedence",
			exe:             "/usr/local/bin/jenticctl",
			stamped:         "0.26.0",
			manifestVersion: "v0.28.0",
			wantNudge:       "",
		},
		{
			name:            "brew path with unstamped dev build keeps current manifest",
			exe:             "/opt/homebrew/Caskroom/jentic/0.28.0/jenticctl",
			stamped:         "dev",
			manifestVersion: "v0.28.0",
			wantNudge:       "",
		},
		{
			name:            "brew path with unstamped dev build and no manifest nudges from dev",
			exe:             "/opt/homebrew/Caskroom/jentic/0.28.0/jenticctl",
			stamped:         "dev",
			manifestVersion: "",
			wantNudge:       "Update available: dev → v0.28.0 — run `jenticctl update`",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			app := testApp(t)
			if tt.manifestVersion != "" {
				saveNudgeManifest(t, app.Paths, tt.manifestVersion, tt.exe)
			}
			stubNudgeEnv(t, app, tt.exe, tt.stamped, "v0.28.0")

			app.maybeNudgeUpdate(runnableStatusCmd())
			got := app.Err.(*bytes.Buffer).String()
			if tt.wantNudge == "" {
				if got != "" {
					t.Errorf("maybeNudgeUpdate = %q, want silent", got)
				}
				return
			}
			if !strings.Contains(got, tt.wantNudge) {
				t.Errorf("maybeNudgeUpdate = %q, want substring %q", got, tt.wantNudge)
			}
		})
	}
}
