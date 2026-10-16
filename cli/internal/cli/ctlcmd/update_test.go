package ctlcmd

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/pkg/core"
	"github.com/spf13/cobra"
)

// TestUpdateNeeded covers the per-half gating: the stack half must be gated on
// its own recorded ref, not the CLI binary's version (a brew-managed CLI is
// refreshed out-of-band while the stack lags).
func TestUpdateNeeded(t *testing.T) {
	tests := []struct {
		name                             string
		doCLI, doStack                   bool
		cliVersion, stackVersion, latest string
		want                             bool
	}{
		{"lockstep source install up to date", true, true, "0.16.0", "v0.16.0", "v0.16.0", false},
		{"lockstep source install behind", true, true, "0.15.0", "v0.15.0", "v0.16.0", true},
		{"brew degrade: fresh CLI, stale stack", false, true, "0.16.0", "v0.15.0", "v0.16.0", true},
		{"brew degrade: fresh CLI, fresh stack", false, true, "0.16.0", "v0.16.0", "v0.16.0", false},
		{"brew degrade: no stack manifest falls back to cli version", false, true, "0.16.0", "0.16.0", "v0.16.0", false},
		{"cli-only behind", true, false, "0.15.0", "v0.16.0", "v0.16.0", true},
		{"cli-only current, stack stale but not requested", true, false, "0.16.0", "v0.15.0", "v0.16.0", false},
		{"unparseable stack ref (branch install) always offers rebuild", false, true, "0.16.0", "main", "v0.16.0", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := updateNeeded(tt.doCLI, tt.doStack, tt.cliVersion, tt.stackVersion, tt.latest)
			if got != tt.want {
				t.Errorf("updateNeeded(%v, %v, %q, %q, %q) = %v, want %v",
					tt.doCLI, tt.doStack, tt.cliVersion, tt.stackVersion, tt.latest, got, tt.want)
			}
		})
	}
}

// TestUpdateNeededStackRefGatesIndependently is the regression test for #943:
// a CLI-only version bump must not make a stale stack look current. The stack
// half is gated on the manifest's stack_ref, so when the CLI reached the latest
// release but the stack never rebuilt, `update` must still offer the rebuild.
func TestUpdateNeededStackRefGatesIndependently(t *testing.T) {
	const latest = "v0.25.0"
	// The wedge: CLI swapped to v0.25.0, stack still on the v0.24.0 build.
	wedged := &config.Manifest{Ref: latest, StackRef: "v0.24.0", CLIVersion: latest}
	stackVersion := firstNonEmpty(wedged.ResolvedStackRef(), wedged.CLIVersion)
	if !updateNeeded(false, true, wedged.CLIVersion, stackVersion, latest) {
		t.Error("stale stack with a current CLI reported nothing to rebuild (#943 regression)")
	}

	// Once the stack rebuild is recorded, the same invocation is a no-op.
	rebuilt := &config.Manifest{Ref: latest, StackRef: latest, CLIVersion: latest}
	stackVersion = firstNonEmpty(rebuilt.ResolvedStackRef(), rebuilt.CLIVersion)
	if updateNeeded(false, true, rebuilt.CLIVersion, stackVersion, latest) {
		t.Error("fully-updated install still offered a rebuild")
	}

	// A legacy manifest (no stack_ref) keeps its previous meaning via Ref.
	legacy := &config.Manifest{Ref: "v0.24.0", CLIVersion: latest}
	stackVersion = firstNonEmpty(legacy.ResolvedStackRef(), legacy.CLIVersion)
	if !updateNeeded(false, true, legacy.CLIVersion, stackVersion, latest) {
		t.Error("legacy manifest with a stale ref reported nothing to rebuild")
	}
}

// TestResolveCtlTargetResolvesManifestSymlink covers the manifest branch: a
// recorded BinaryPath that is a PATH symlink (what `jenticctl install` records
// under a linked install) must resolve to the real file so the update swaps
// the binary, not the link.
func TestResolveCtlTargetResolvesManifestSymlink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink test targets unix")
	}
	root := t.TempDir()
	realDir := filepath.Join(root, "real")
	linkDir := filepath.Join(root, "linkbin")
	for _, d := range []string{realDir, linkDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	realBin := filepath.Join(realDir, "jenticctl")
	if err := os.WriteFile(realBin, []byte("bin"), 0o755); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(linkDir, "jenticctl")
	if err := os.Symlink(realBin, link); err != nil {
		t.Fatal(err)
	}

	got, err := resolveCtlTarget(&config.Manifest{BinaryPath: link})
	if err != nil {
		t.Fatalf("resolveCtlTarget: %v", err)
	}
	want, _ := filepath.EvalSymlinks(realBin) // temp dirs may themselves be symlinked (macOS /tmp)
	if got != want {
		t.Errorf("resolveCtlTarget = %q, want %q", got, want)
	}
}

// TestResolveCtlTargetKeepsMissingManifestPath: a stale manifest path that no
// longer exists is returned verbatim (the swap recreates it).
func TestResolveCtlTargetKeepsMissingManifestPath(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "gone", "jenticctl")
	got, err := resolveCtlTarget(&config.Manifest{BinaryPath: missing})
	if err != nil {
		t.Fatalf("resolveCtlTarget: %v", err)
	}
	if got != missing {
		t.Errorf("resolveCtlTarget = %q, want %q", got, missing)
	}
}

// TestResolveCtlTargetFallsBackToExecutable: with no manifest path the running
// executable is used.
func TestResolveCtlTargetFallsBackToExecutable(t *testing.T) {
	got, err := resolveCtlTarget(&config.Manifest{})
	if err != nil {
		t.Fatalf("resolveCtlTarget: %v", err)
	}
	if got == "" {
		t.Error("resolveCtlTarget returned empty path for executable fallback")
	}
}

// TestApplyPromptTitle: the confirm prompt must not promise repo@ref for a
// brew-managed CLI half (brew ships only its latest cask), while non-brew
// wording is unchanged.
func TestApplyPromptTitle(t *testing.T) {
	const repo, ref = "jentic/jentic-one", "v0.21.0"
	tests := []struct {
		name                 string
		doCLI, doStack, brew bool
		want                 string
	}{
		{"source combined", true, true, false, "Update the CLI and the stack to jentic/jentic-one@v0.21.0?"},
		{"source cli-only", true, false, false, "Update the CLI to jentic/jentic-one@v0.21.0?"},
		{"stack-only", false, true, false, "Update the stack to jentic/jentic-one@v0.21.0?"},
		{"brew combined", true, true, true, "Update the CLI (via `brew upgrade jentic`) and the stack (from jentic/jentic-one@v0.21.0)?"},
		{"brew cli-only", true, false, true, "Update the CLI via `brew upgrade jentic`?"},
		{"brew stack-only unaffected", false, true, true, "Update the stack to jentic/jentic-one@v0.21.0?"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := applyPromptTitle(tt.doCLI, tt.doStack, tt.brew, repo, ref)
			if got != tt.want {
				t.Errorf("applyPromptTitle(%v, %v, %v) = %q, want %q", tt.doCLI, tt.doStack, tt.brew, got, tt.want)
			}
		})
	}
}

// A Docker install (compose file present) with a stopped daemon must fail fast
// with the guard's actionable error, before the long image build / migrations
// / compose up sequence surfaces a raw compose transport error.
func TestUpdateStackDockerFailsFastWhenDaemonDown(t *testing.T) {
	app := testApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	sentinel := stubDaemonDown(t)

	// The stack bring-up routes through the composeUp seam; a bypassed guard
	// would reach it and fail loudly (mirrors start/stop).
	origUp := composeUp
	t.Cleanup(func() { composeUp = origUp })
	composeUp = func(io.Writer, string) error {
		t.Fatal("composeUp must not run when the daemon is down")
		return nil
	}

	err := app.updateStackDocker(context.Background(), "", false, false)
	if !errors.Is(err, sentinel) {
		t.Fatalf("update should surface the daemon guard error, got %v", err)
	}
	// The guard short-circuits before the image build header is rendered, so the
	// build/migrations/compose-up sequence never starts (no real Docker call).
	if got := app.Out.(*bytes.Buffer).String(); strings.Contains(got, "Build (Docker images)") {
		t.Errorf("updateStackDocker ran past the guard when the daemon was down:\n%s", got)
	}
}

// updateFakes replaces update's external effects (release lookup, CLI swap,
// skill refresh, stack rebuild, port lookup) with recorders, so updateE runs
// end to end without network, binaries, or Docker.
type updateFakes struct {
	cliSwaps     []string // refs the CLI swapper was asked for
	stackBuilds  []string // refs the stack rebuild was asked for
	preflighted  []bool   // the preflighted flag each stack rebuild received
	stackErr     error
	portHolders  []install.PortHolder
	portLookups  int
	portLookupEr error
}

func stubUpdateFlow(t *testing.T, latest string) *updateFakes {
	t.Helper()
	f := &updateFakes{}
	origLatest, origSwap, origSkills, origStack, origPorts := latestReleaseTag, swapCLI, refreshSkills, rebuildStack, foreignPortHolders
	t.Cleanup(func() {
		latestReleaseTag, swapCLI, refreshSkills, rebuildStack, foreignPortHolders = origLatest, origSwap, origSkills, origStack, origPorts
	})
	latestReleaseTag = func(context.Context, string, string) (string, error) { return latest, nil }
	swapCLI = func(_ *app, _ context.Context, _, ref, _ string) error {
		f.cliSwaps = append(f.cliSwaps, ref)
		return nil
	}
	refreshSkills = func(*app) {}
	rebuildStack = func(_ *app, _ context.Context, _, _, ref string, _, preflighted bool) error {
		f.stackBuilds = append(f.stackBuilds, ref)
		f.preflighted = append(f.preflighted, preflighted)
		return f.stackErr
	}
	foreignPortHolders = func(context.Context, string) ([]install.PortHolder, error) {
		f.portLookups++
		return f.portHolders, f.portLookupEr
	}
	return f
}

// saveManifest records an install whose CLI and stack are at the given
// versions. BinaryPath points into the temp root so the update never resolves
// (or brew-classifies) the test binary itself.
func saveManifest(t *testing.T, a *app, mode, cliVersion, stackRef string) {
	t.Helper()
	m := &config.Manifest{
		Repo:       "jentic/jentic-one",
		Ref:        cliVersion,
		StackRef:   stackRef,
		CLIVersion: cliVersion,
		Mode:       mode,
		BinaryPath: filepath.Join(a.Paths.Root, "bin", "jenticctl"),
	}
	if err := m.Save(a.Paths); err != nil {
		t.Fatalf("save manifest: %v", err)
	}
}

func recordedStackRef(t *testing.T, a *app) string {
	t.Helper()
	m, _, err := config.LoadManifest(a.Paths)
	if err != nil {
		t.Fatalf("load manifest: %v", err)
	}
	return m.StackRef
}

// runUpdateCmd drives `jenticctl update <args>` through core.Run, so the test
// observes the real exit code and the stderr rendering of a returned error.
func runUpdateCmd(t *testing.T, a *app, args ...string) (int, string) {
	t.Helper()
	var stderr bytes.Buffer
	root := &cobra.Command{Use: "jenticctl", SilenceUsage: true, SilenceErrors: true}
	root.AddCommand(newUpdateCmd(a))
	root.SetArgs(append([]string{"update"}, args...))
	root.SetOut(io.Discard)
	root.SetErr(&stderr)
	return core.Run(root), stderr.String()
}

// outsideCheckout runs the test from a temp dir with no $JENTIC_SRC, so a
// pinned --ref plans a managed clone instead of hitting the local-checkout
// refusal (#949) for the repo the tests run in.
func outsideCheckout(t *testing.T) {
	t.Helper()
	t.Setenv(install.SrcEnv, "")
	t.Chdir(t.TempDir())
}

// TestUpdateVerdict pins the top-line verdict to BOTH halves (#976): a stack
// that lags a current CLI must never read "Up to date".
func TestUpdateVerdict(t *testing.T) {
	const latest = "v0.28.0"
	tests := []struct {
		name, cli, stack string
		want             string
	}{
		{"both current", "v0.28.0", "v0.28.0", "Up to date (v0.28.0)."},
		{"diverged: stack behind a current CLI", "v0.28.0", "v0.26.0", "Stack update available: v0.26.0 → v0.28.0 (the CLI is already at v0.28.0)"},
		{"cli behind a current stack", "v0.26.0", "v0.28.0", "CLI update available: v0.26.0 → v0.28.0 (the stack is already at v0.28.0)"},
		{"lockstep behind", "v0.26.0", "v0.26.0", "Update available: v0.26.0 → v0.28.0"},
		{"lockstep behind, unprefixed cli version", "0.26.0", "v0.26.0", "Update available: 0.26.0 → v0.28.0"},
		{"both behind at different versions", "v0.27.0", "v0.26.0", "Update available: CLI v0.27.0 and stack v0.26.0 → v0.28.0"},
		{"unknown cli version", "none", "v0.26.0", "Installed version is unknown; cannot compare. Latest is v0.28.0."},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := computeVerdict(tt.cli, tt.stack, latest).message(tt.cli, tt.stack, latest)
			if got != tt.want {
				t.Errorf("verdict(cli=%q, stack=%q) = %q, want %q", tt.cli, tt.stack, got, tt.want)
			}
		})
	}
}

// TestUpdateRerunAfterDivergenceSkipsCLIAndUpdatesStack is the #976 re-run: the
// CLI already reached latest but the stack is a release behind. The verdict
// names the stale stack, the CLI is not swapped again, and only the stack is
// rebuilt and recorded.
func TestUpdateRerunAfterDivergenceSkipsCLIAndUpdatesStack(t *testing.T) {
	a := testApp(t)
	saveManifest(t, a, config.ModeLocal, "v0.28.0", "v0.26.0")
	f := stubUpdateFlow(t, "v0.28.0")

	if err := a.updateE(context.Background(), &updateOptions{yes: true}); err != nil {
		t.Fatalf("updateE: %v", err)
	}

	out := a.Out.(*bytes.Buffer).String()
	if strings.Contains(out, "Up to date (") {
		t.Errorf("verdict claims up to date while the stack lags:\n%s", out)
	}
	if !strings.Contains(out, "Stack update available: v0.26.0 → v0.28.0") {
		t.Errorf("verdict does not name the stale stack:\n%s", out)
	}
	if !strings.Contains(out, "updating the stack only") {
		t.Errorf("missing the stack-only notice:\n%s", out)
	}
	if len(f.cliSwaps) != 0 {
		t.Errorf("CLI already at latest was swapped again: %v", f.cliSwaps)
	}
	if len(f.stackBuilds) != 1 || f.stackBuilds[0] != "v0.28.0" {
		t.Errorf("stack rebuilds = %v, want [v0.28.0]", f.stackBuilds)
	}
	if got := recordedStackRef(t, a); got != "v0.28.0" {
		t.Errorf("recorded stack ref = %q, want v0.28.0", got)
	}
}

// A pinned --ref is an explicit request for that build, so the CLI half still
// runs even when the CLI is already at the latest release.
func TestUpdatePinnedRefKeepsCLIHalf(t *testing.T) {
	a := testApp(t)
	outsideCheckout(t)
	saveManifest(t, a, config.ModeLocal, "v0.28.0", "v0.26.0")
	f := stubUpdateFlow(t, "v0.28.0")

	if err := a.updateE(context.Background(), &updateOptions{yes: true, ref: "v0.27.0"}); err != nil {
		t.Fatalf("updateE: %v", err)
	}
	if len(f.cliSwaps) != 1 || f.cliSwaps[0] != "v0.27.0" {
		t.Errorf("CLI swaps = %v, want [v0.27.0]", f.cliSwaps)
	}
}

// TestUpdateStackFailureAfterCLISwapReportsDivergence: when the stack half fails
// after the CLI was swapped, the command exits 1 with an error that says the
// CLI moved but the stack did not, keeps the port-conflict cause, and gives the
// exact retry command. The stack ref stays at the old build (#943).
func TestUpdateStackFailureAfterCLISwapReportsDivergence(t *testing.T) {
	tests := []struct {
		name      string
		args      []string
		wantCLI   string
		wantRetry string
	}{
		{"latest release", []string{"--yes"}, "the CLI was updated to v0.28.0", "`jenticctl update --stack-only`"},
		{"pinned ref", []string{"--yes", "--ref", "v0.28.0"}, "the CLI was updated to v0.28.0", "`jenticctl update --stack-only --ref v0.28.0`"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			a := testApp(t)
			outsideCheckout(t)
			saveManifest(t, a, config.ModeLocal, "v0.26.0", "v0.26.0")
			f := stubUpdateFlow(t, "v0.28.0")
			f.stackErr = withPortConflict(errors.New("migrations failed: exit status 1"),
				"Error response from daemon: driver failed programming external connectivity on endpoint jentic-db-1: "+
					"Bind for 0.0.0.0:5432 failed: port is already allocated")

			code, stderr := runUpdateCmd(t, a, tt.args...)

			if code != 1 {
				t.Errorf("exit code = %d, want 1", code)
			}
			if len(f.cliSwaps) != 1 {
				t.Fatalf("CLI swaps = %v, want one", f.cliSwaps)
			}
			for _, want := range []string{
				tt.wantCLI,
				"the stack update failed, so the stack is still at v0.26.0",
				"host port 5432 is already in use",
				"docker ps --filter publish=5432",
				tt.wantRetry,
			} {
				if !strings.Contains(stderr, want) {
					t.Errorf("stderr missing %q:\n%s", want, stderr)
				}
			}
			if got := recordedStackRef(t, a); got != "v0.26.0" {
				t.Errorf("failed stack rebuild recorded stack ref %q, want v0.26.0 kept", got)
			}
		})
	}
}

// A stack-only failure has no CLI/stack skew to report: the raw cause is
// returned unwrapped.
func TestUpdateStackOnlyFailureIsNotReportedAsDivergence(t *testing.T) {
	a := testApp(t)
	saveManifest(t, a, config.ModeLocal, "v0.28.0", "v0.26.0")
	f := stubUpdateFlow(t, "v0.28.0")
	f.stackErr = errors.New("migrations failed: exit status 1")

	err := a.updateE(context.Background(), &updateOptions{yes: true})
	var diverged *stackDivergedError
	if errors.As(err, &diverged) || !errors.Is(err, f.stackErr) {
		t.Fatalf("stack-only failure = %v, want the raw stack error", err)
	}
}

// TestUpdatePreflightPortHolderBlocksCLISwap: in a combined docker run, a host
// port held by another project's container fails the update BEFORE the CLI is
// swapped, naming the container — so the halves never diverge (#976).
func TestUpdatePreflightPortHolderBlocksCLISwap(t *testing.T) {
	a := testApp(t)
	saveManifest(t, a, config.ModeDocker, "v0.26.0", "v0.26.0")
	if err := os.WriteFile(a.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	stubDaemonUp(t)
	f := stubUpdateFlow(t, "v0.28.0")
	f.portHolders = []install.PortHolder{{Port: "5432", Container: "dev-db-1", Project: "dev"}}

	err := a.updateE(context.Background(), &updateOptions{yes: true})
	if err == nil {
		t.Fatal("updateE succeeded despite a foreign port holder")
	}
	for _, want := range []string{"host port 5432 is already allocated by container dev-db-1", "docker stop dev-db-1", "nothing was updated"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error missing %q: %v", want, err)
		}
	}
	if len(f.cliSwaps) != 0 || len(f.stackBuilds) != 0 {
		t.Errorf("update ran past the preflight: cli=%v stack=%v", f.cliSwaps, f.stackBuilds)
	}
}

// A failing port lookup must not block the update: it is skipped (and said so),
// and the stack step is told the preflight already ran.
func TestUpdatePreflightPortLookupFailureIsSkipped(t *testing.T) {
	a := testApp(t)
	saveManifest(t, a, config.ModeDocker, "v0.26.0", "v0.26.0")
	if err := os.WriteFile(a.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	stubDaemonUp(t)
	f := stubUpdateFlow(t, "v0.28.0")
	f.portLookupEr = errors.New("docker ps: boom")

	if err := a.updateE(context.Background(), &updateOptions{yes: true}); err != nil {
		t.Fatalf("updateE: %v", err)
	}
	if !strings.Contains(a.Out.(*bytes.Buffer).String(), "skipped the host-port check") {
		t.Error("skipped port check was not reported")
	}
	if f.portLookups != 1 || len(f.cliSwaps) != 1 || len(f.preflighted) != 1 || !f.preflighted[0] {
		t.Errorf("lookups=%d cli=%v preflighted=%v, want 1 lookup, 1 swap, preflighted stack", f.portLookups, f.cliSwaps, f.preflighted)
	}
}

// TestUpdateStackDockerNamesPortConflict: a compose bind failure during the
// migration one-shot surfaces as a named host-port conflict, and `up` is never
// attempted.
func TestUpdateStackDockerNamesPortConflict(t *testing.T) {
	a := testApp(t)
	if err := os.WriteFile(a.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	origBuild, origMigrate, origUp := buildStackImages, runComposeMigrations, composeUp
	t.Cleanup(func() { buildStackImages, runComposeMigrations, composeUp = origBuild, origMigrate, origUp })
	buildStackImages = func(install.BuildPlan, io.Writer) error { return nil }
	runComposeMigrations = func(w io.Writer, _ string) error {
		fmt.Fprintln(w, "Error response from daemon: failed to set up container networking: driver failed programming "+
			"external connectivity on endpoint jentic-db-1 (abc): Bind for 0.0.0.0:5432 failed: port is already allocated")
		return errors.New("exit status 1")
	}
	composeUp = func(io.Writer, string) error {
		t.Fatal("composeUp must not run after a failed migration")
		return nil
	}

	err := a.updateStackDocker(context.Background(), "v0.28.0", false, true)
	var conflict *hostPortConflictError
	if !errors.As(err, &conflict) || conflict.port != "5432" {
		t.Fatalf("err = %v, want a host-port conflict on 5432", err)
	}
	if !strings.Contains(err.Error(), "migrations failed") {
		t.Errorf("conflict error lost the failing step: %v", err)
	}
}

// A compose failure that is not a port conflict keeps its original error.
func TestWithPortConflictPassesOtherFailuresThrough(t *testing.T) {
	cause := errors.New("migrations failed: exit status 1")
	got := withPortConflict(cause, "alembic.util.exc.CommandError: Can't locate revision")
	var conflict *hostPortConflictError
	if errors.As(got, &conflict) || !errors.Is(got, cause) || got.Error() != cause.Error() {
		t.Errorf("withPortConflict = %v, want the cause unchanged", got)
	}
}

func TestTailBufferKeepsTail(t *testing.T) {
	tb := &tailBuffer{}
	_, _ = tb.Write(bytes.Repeat([]byte("x"), tailBufferMax))
	_, _ = tb.Write([]byte("Bind for 0.0.0.0:5432 failed: port is already allocated"))
	if got := tb.String(); len(got) != tailBufferMax || !strings.HasSuffix(got, "port is already allocated") {
		t.Errorf("tail buffer len=%d, suffix kept=%v", len(got), strings.HasSuffix(got, "port is already allocated"))
	}
}
