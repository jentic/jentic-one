package ctlcmd

import (
	"bytes"
	"context"
	"encoding/json"
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

	err := app.updateStackDocker(context.Background(), "", false, false, "")
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
	cliSwaps     []string // refs the CLI half staged binaries for
	stackBuilds  []string // refs the stack rebuild was asked for
	preflighted  []bool   // the preflighted flag each stack rebuild received
	skillRefresh int
	stackErr     error
	portHolders  []install.PortHolder
	portLookups  int
	portLookupEr error
}

// fakeCLIBinary writes an executable that answers `--version` like a real
// release build of name at version.
func fakeCLIBinary(t *testing.T, dir, name, version string) string {
	t.Helper()
	path := filepath.Join(dir, name)
	script := fmt.Sprintf("#!/bin/sh\necho '%s %s (commit c0ffee1, built 2026-10-01)'\n", name, version)
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// stubUpdateFlow fakes update's external effects. The CLI half fakes only the
// binary acquisition (what a release download or source build would stage), so
// the real swap and manifest recording in updateCLI run against the temp root.
func stubUpdateFlow(t *testing.T, latest string) *updateFakes {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("fake CLI binaries are shell scripts")
	}
	f := &updateFakes{}
	origLatest, origAcquire, origSkills, origStack, origPorts := latestReleaseTag, acquireCLI, refreshSkills, rebuildStack, foreignPortHolders
	t.Cleanup(func() {
		latestReleaseTag, acquireCLI, refreshSkills, rebuildStack, foreignPortHolders = origLatest, origAcquire, origSkills, origStack, origPorts
	})
	latestReleaseTag = func(context.Context, string, string) (string, error) { return latest, nil }
	acquireCLI = func(_ *app, _ context.Context, _, ref string) (map[string]string, func(), bool, error) {
		f.cliSwaps = append(f.cliSwaps, ref)
		stage := t.TempDir()
		return map[string]string{
			ctlBinaryName: fakeCLIBinary(t, stage, ctlBinaryName, ref),
			apiBinaryName: fakeCLIBinary(t, stage, apiBinaryName, ref),
		}, nil, true, nil
	}
	refreshSkills = func(*app) { f.skillRefresh++ }
	rebuildStack = func(_ *app, _ context.Context, _, _, ref string, _, preflighted bool, _ string) error {
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

func loadManifest(t *testing.T, a *app) *config.Manifest {
	t.Helper()
	m, _, err := config.LoadManifest(a.Paths)
	if err != nil {
		t.Fatalf("load manifest: %v", err)
	}
	return m
}

// stubBrew makes the install Homebrew-managed with brew "installing" the given
// cask version, and counts the upgrades.
func stubBrew(t *testing.T, caskVersion string) *int {
	t.Helper()
	origManaged, origUpgrade, origCask := isBrewManaged, brewUpgrade, brewCaskVersion
	t.Cleanup(func() { isBrewManaged, brewUpgrade, brewCaskVersion = origManaged, origUpgrade, origCask })
	upgrades := 0
	isBrewManaged = func(string) bool { return true }
	brewUpgrade = func(context.Context, io.Writer, io.Writer) error { upgrades++; return nil }
	brewCaskVersion = func(context.Context) string { return caskVersion }
	return &upgrades
}

// TestUpdateTwoStepDivergenceThenRerun drives the #976 sequence through the
// real flow on the release-download path: run 1 swaps the CLI and the stack
// fails; run 2 must see the CLI as current (it was recorded on swap), skip it,
// and finish only the stack. Pins that the CLI half records its version — a
// manifest still naming the old CLI would re-swap it and misreport the verdict.
func TestUpdateTwoStepDivergenceThenRerun(t *testing.T) {
	a := testApp(t)
	saveManifest(t, a, config.ModeLocal, "v0.26.0", "v0.26.0")
	f := stubUpdateFlow(t, "v0.28.0")
	f.stackErr = errors.New("migrations failed: exit status 1")

	if err := a.updateE(context.Background(), &updateOptions{yes: true}); err == nil {
		t.Fatal("run 1: want the stack failure")
	}
	m := loadManifest(t, a)
	if m.CLIVersion != "v0.28.0" || m.Ref != "v0.28.0" || m.Commit != "c0ffee1" || m.StackRef != "v0.26.0" {
		t.Fatalf("after run 1: cli_version=%q ref=%q commit=%q stack_ref=%q, want v0.28.0/v0.28.0/c0ffee1/v0.26.0",
			m.CLIVersion, m.Ref, m.Commit, m.StackRef)
	}
	if want := filepath.Join(a.Paths.Root, "bin", "jenticctl"); m.BinaryPath != want {
		t.Errorf("binary_path = %q, want %q", m.BinaryPath, want)
	}

	a.Out.(*bytes.Buffer).Reset()
	f.stackErr = nil
	if err := a.updateE(context.Background(), &updateOptions{yes: true}); err != nil {
		t.Fatalf("run 2: %v", err)
	}
	out := a.Out.(*bytes.Buffer).String()
	if !strings.Contains(out, "Stack update available: v0.26.0 → v0.28.0 (the CLI is already at v0.28.0)") {
		t.Errorf("run 2 verdict does not name the stale stack alone:\n%s", out)
	}
	if len(f.cliSwaps) != 1 {
		t.Errorf("CLI swaps across both runs = %v, want only run 1's", f.cliSwaps)
	}
	if len(f.stackBuilds) != 2 || recordedStackRef(t, a) != "v0.28.0" {
		t.Errorf("stack builds = %v, stack_ref = %q; want two builds and v0.28.0", f.stackBuilds, recordedStackRef(t, a))
	}

	// Run 3: both halves current — nothing to do.
	a.Out.(*bytes.Buffer).Reset()
	if err := a.updateE(context.Background(), &updateOptions{yes: true}); err != nil {
		t.Fatalf("run 3: %v", err)
	}
	if out := a.Out.(*bytes.Buffer).String(); !strings.Contains(out, "Up to date (v0.28.0).") || len(f.cliSwaps) != 1 || len(f.stackBuilds) != 2 {
		t.Errorf("run 3 rebuilt something (cli=%v stack=%v):\n%s", f.cliSwaps, f.stackBuilds, out)
	}
}

// A legacy manifest (no stack_ref, stack installed) records its stack build
// only in ref. The CLI half must backfill stack_ref before overwriting ref, or
// a failed stack half would leave the stack looking current (#943).
func TestUpdateCLISwapBackfillsLegacyStackRef(t *testing.T) {
	a := testApp(t)
	saveManifest(t, a, config.ModeLocal, "v0.26.0", "")
	f := stubUpdateFlow(t, "v0.28.0")
	f.stackErr = errors.New("migrations failed: exit status 1")

	_ = a.updateE(context.Background(), &updateOptions{yes: true})
	m := loadManifest(t, a)
	if m.StackRef != "v0.26.0" || m.ResolvedStackRef() != "v0.26.0" || m.Ref != "v0.28.0" {
		t.Errorf("stack_ref=%q resolved=%q ref=%q, want v0.26.0/v0.26.0/v0.28.0", m.StackRef, m.ResolvedStackRef(), m.Ref)
	}
}

// TestUpdateBrewRecordsCaskVersion covers the Homebrew CLI half: the manifest
// records what brew actually installed, and a cask still lagging the release
// is neither recorded as latest nor reported as a CLI update in the
// divergence error.
func TestUpdateBrewRecordsCaskVersion(t *testing.T) {
	tests := []struct {
		name, cask       string
		wantCLI          string
		wantDiverged     bool
		wantSkillRefresh int
	}{
		{"cask reached latest", "0.28.0", "v0.28.0", true, 1},
		{"cask lags the release", "0.26.0", "v0.26.0", false, 0},
		{"cask version unreadable", "", "v0.26.0", true, 1},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			a := testApp(t)
			saveManifest(t, a, config.ModeLocal, "v0.26.0", "v0.26.0")
			before := backdateManifest(t, a)
			f := stubUpdateFlow(t, "v0.28.0")
			f.stackErr = errors.New("migrations failed: exit status 1")
			upgrades := stubBrew(t, tt.cask)

			err := a.updateE(context.Background(), &updateOptions{yes: true})
			if *upgrades != 1 || len(f.cliSwaps) != 0 {
				t.Fatalf("brew upgrades = %d, in-place swaps = %v; want 1 and none", *upgrades, f.cliSwaps)
			}
			var diverged *stackDivergedError
			if got := errors.As(err, &diverged); got != tt.wantDiverged {
				t.Errorf("divergence error = %v (%v), want %v", got, err, tt.wantDiverged)
			}
			if m := loadManifest(t, a); m.CLIVersion != tt.wantCLI || m.StackRef != "v0.26.0" {
				t.Errorf("cli_version=%q stack_ref=%q, want %q/v0.26.0", m.CLIVersion, m.StackRef, tt.wantCLI)
			}
			if f.skillRefresh != tt.wantSkillRefresh {
				t.Errorf("skill refreshes = %d, want %d", f.skillRefresh, tt.wantSkillRefresh)
			}
			// Nothing landed (lagging or unreadable cask): the manifest is not
			// rewritten at all, installed_at included.
			if tt.cask != "0.28.0" && !bytes.Equal(manifestBytes(t, a), before) {
				t.Errorf("manifest rewritten although no CLI version landed:\n%s", manifestBytes(t, a))
			}
		})
	}
}

// backdateManifest rewrites install.json with an old installed_at and returns
// its bytes. Save stamps installed_at at second precision, so without this a
// rewrite in the same second would be byte-identical and go unnoticed.
func backdateManifest(t *testing.T, a *app) []byte {
	t.Helper()
	m := loadManifest(t, a)
	m.InstalledAt = "2000-01-01T00:00:00Z"
	data, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	data = append(data, '\n')
	if err := os.WriteFile(a.Paths.ManifestPath(), data, 0o600); err != nil {
		t.Fatal(err)
	}
	return data
}

// manifestBytes returns install.json verbatim.
func manifestBytes(t *testing.T, a *app) []byte {
	t.Helper()
	b, err := os.ReadFile(a.Paths.ManifestPath())
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	return b
}

// A brew install with no install manifest (brew alone, no `jenticctl
// install`) must not get one created by the CLI half: `jenticctl status`
// keeps reporting "no install manifest", and the next run reads the brew
// binary's own version.
func TestUpdateBrewWithoutManifestCreatesNone(t *testing.T) {
	a := testApp(t)
	stubUpdateFlow(t, "v0.28.0")
	upgrades := stubBrew(t, "0.28.0")

	if err := a.updateE(context.Background(), &updateOptions{yes: true, cliOnly: true}); err != nil {
		t.Fatalf("updateE: %v", err)
	}
	if *upgrades != 1 {
		t.Fatalf("brew upgrades = %d, want 1", *upgrades)
	}
	if _, err := os.Stat(a.Paths.ManifestPath()); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("install.json created by a brew CLI update (stat err = %v)", err)
	}
}

// brew upgrades the CLI out-of-band, so a stale recorded cli_version must not
// shadow the stamped version of the brew binary that is actually running.
func TestUpdateBrewPrefersRunningBinaryVersion(t *testing.T) {
	a := testApp(t)
	saveManifest(t, a, config.ModeLocal, "v0.26.0", "v0.28.0")
	f := stubUpdateFlow(t, "v0.28.0")
	upgrades := stubBrew(t, "0.28.0")
	origVersion := version
	t.Cleanup(func() { version = origVersion })
	version = "0.28.0"

	if err := a.updateE(context.Background(), &updateOptions{yes: true}); err != nil {
		t.Fatalf("updateE: %v", err)
	}
	if out := a.Out.(*bytes.Buffer).String(); !strings.Contains(out, "Up to date (v0.28.0).") {
		t.Errorf("verdict ignored the running brew binary's version:\n%s", out)
	}
	if *upgrades != 0 || len(f.stackBuilds) != 0 {
		t.Errorf("brew upgrades = %d, stack builds = %v; want nothing to do", *upgrades, f.stackBuilds)
	}
}

// useRealStackRebuild routes the stack half through the real updateStack
// (after stubUpdateFlow faked it), so the stack step's own checks run.
func useRealStackRebuild(t *testing.T) {
	t.Helper()
	rebuildStack = (*app).updateStack
}

// TestUpdateNoStackInstallUpdatesCLIOnly: a CLI-only install (no deploy mode,
// no install config) has no stack to diverge from. A combined run updates the
// CLI and succeeds instead of reporting a fake CLI/stack divergence whose
// suggested retry would then claim "Up to date"; an explicit --stack-only is
// refused.
func TestUpdateNoStackInstallUpdatesCLIOnly(t *testing.T) {
	a := testApp(t)
	saveManifest(t, a, "", "v0.26.0", "")
	f := stubUpdateFlow(t, "v0.28.0")
	useRealStackRebuild(t)

	if err := a.updateE(context.Background(), &updateOptions{yes: true}); err != nil {
		t.Fatalf("updateE on a CLI-only install: %v", err)
	}
	out := a.Out.(*bytes.Buffer).String()
	if len(f.cliSwaps) != 1 {
		t.Errorf("CLI swaps = %v, want one", f.cliSwaps)
	}
	for _, want := range []string{"not installed", "CLI update available: v0.26.0 → v0.28.0\n", "no stack installed; updating the CLI only"} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
	if strings.Contains(out, "Stack update runs forward-only migrations") {
		t.Errorf("stack half ran on an install with no stack:\n%s", out)
	}

	err := a.updateE(context.Background(), &updateOptions{yes: true, stackOnly: true})
	if err == nil || !strings.Contains(err.Error(), "no stack installed") {
		t.Errorf("--stack-only on a CLI-only install = %v, want a no-stack error", err)
	}
	var diverged *stackDivergedError
	if errors.As(err, &diverged) {
		t.Errorf("--stack-only reported a divergence: %v", err)
	}
}

// An already-current CLI-only install reports "up to date" without the
// contradictory "updating the CLI only" note.
func TestUpdateNoStackInstallAlreadyCurrent(t *testing.T) {
	a := testApp(t)
	saveManifest(t, a, "", "v0.28.0", "")
	f := stubUpdateFlow(t, "v0.28.0")
	useRealStackRebuild(t)

	if err := a.updateE(context.Background(), &updateOptions{yes: true}); err != nil {
		t.Fatalf("updateE on a current CLI-only install: %v", err)
	}
	out := a.Out.(*bytes.Buffer).String()
	if len(f.cliSwaps) != 0 {
		t.Errorf("CLI swaps = %v, want none", f.cliSwaps)
	}
	if !strings.Contains(out, "Already up to date (v0.28.0); nothing to rebuild.") {
		t.Errorf("output missing the up-to-date line:\n%s", out)
	}
	if strings.Contains(out, "updating the CLI only") {
		t.Errorf("up-to-date run announced a CLI update:\n%s", out)
	}
}

// A brew re-run whose cask already reached latest skips brew entirely.
func TestUpdateBrewRerunSkipsCLIHalf(t *testing.T) {
	a := testApp(t)
	saveManifest(t, a, config.ModeLocal, "v0.26.0", "v0.26.0")
	f := stubUpdateFlow(t, "v0.28.0")
	f.stackErr = errors.New("migrations failed: exit status 1")
	upgrades := stubBrew(t, "0.28.0")

	_ = a.updateE(context.Background(), &updateOptions{yes: true})
	f.stackErr = nil
	if err := a.updateE(context.Background(), &updateOptions{yes: true}); err != nil {
		t.Fatalf("run 2: %v", err)
	}
	if *upgrades != 1 || recordedStackRef(t, a) != "v0.28.0" {
		t.Errorf("brew upgrades = %d, stack_ref = %q; want 1 and v0.28.0", *upgrades, recordedStackRef(t, a))
	}
}

func TestCommitFromVersionLine(t *testing.T) {
	tests := map[string]string{
		"jenticctl v0.28.0 (commit 22344b5, built 2026-10-01)": "22344b5",
		"jenticctl dev (commit none, built unknown)":           "",
		"jenticctl v0.28.0": "",
	}
	for line, want := range tests {
		if got := commitFromVersionLine(line); got != want {
			t.Errorf("commitFromVersionLine(%q) = %q, want %q", line, got, want)
		}
	}
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
		{"cli behind, no stack installed", "v0.26.0", "", "CLI update available: v0.26.0 → v0.28.0"},
		{"cli current, no stack installed", "v0.28.0", "", "Up to date (v0.28.0)."},
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
// CLI moved but the stack did not reach the target, names the last successful
// stack build without claiming it is intact, keeps the port-conflict cause and
// the stage hint, and gives the exact retry command. The stack ref stays at
// the old build (#943).
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
			f.stackErr = &stackStageError{hint: hintDockerImageRebuilt, err: withPortConflict(errors.New("migrations failed: exit status 1"),
				"Error response from daemon: driver failed programming external connectivity on endpoint jentic-db-1: "+
					"Bind for 0.0.0.0:5432 failed: port is already allocated")}

			code, stderr := runUpdateCmd(t, a, tt.args...)

			if code != 1 {
				t.Errorf("exit code = %d, want 1", code)
			}
			if len(f.cliSwaps) != 1 {
				t.Fatalf("CLI swaps = %v, want one", f.cliSwaps)
			}
			for _, want := range []string{
				tt.wantCLI,
				"the stack was not updated to v0.28.0 (last successful stack build: v0.26.0)",
				hintDockerImageRebuilt,
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

	before := backdateManifest(t, a)

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
	if !bytes.Equal(manifestBytes(t, a), before) {
		t.Errorf("\"nothing was updated\" but the manifest changed:\n%s", manifestBytes(t, a))
	}
}

// The stack-only re-run (CLI already at latest, stack behind) skips the
// combined preflight, so the stack step's own preflight must still catch a
// foreign port holder before the image build.
func TestUpdateStackOnlyRerunPreflightsPortHolder(t *testing.T) {
	tests := []struct {
		name string
		opts updateOptions
	}{
		{"CLI-skip re-run", updateOptions{yes: true}},
		{"explicit --stack-only", updateOptions{yes: true, stackOnly: true}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			a := testApp(t)
			saveManifest(t, a, config.ModeDocker, "v0.28.0", "v0.26.0")
			if err := os.WriteFile(a.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
				t.Fatalf("write compose: %v", err)
			}
			stubDaemonUp(t)
			f := stubUpdateFlow(t, "v0.28.0")
			useRealStackRebuild(t)
			f.portHolders = []install.PortHolder{{Port: "5432", Container: "dev-db-1", Project: "dev"}}
			origBuild := buildStackImages
			t.Cleanup(func() { buildStackImages = origBuild })
			buildStackImages = func(install.BuildPlan, io.Writer) error {
				t.Fatal("image build must not run past a failed preflight")
				return nil
			}
			before := backdateManifest(t, a)

			opts := tt.opts
			err := a.updateE(context.Background(), &opts)
			if err == nil || !strings.Contains(err.Error(), "nothing was updated") || !strings.Contains(err.Error(), "dev-db-1") {
				t.Fatalf("err = %v, want the port-holder preflight error", err)
			}
			if len(f.cliSwaps) != 0 || f.portLookups != 1 {
				t.Errorf("cli swaps = %v, port lookups = %d; want none and 1", f.cliSwaps, f.portLookups)
			}
			if !bytes.Equal(manifestBytes(t, a), before) {
				t.Errorf("manifest changed after a failed preflight:\n%s", manifestBytes(t, a))
			}
		})
	}
}

// Each stack step that already changed on-disk state reports it, so the
// operator does not start a half-updated stack; a failure before any change
// carries no hint.
func TestUpdateStackStageHints(t *testing.T) {
	fail := errors.New("boom")
	tests := []struct {
		name     string
		mode     string
		build    error // local venv rebuild or docker image build
		migrate  error
		up       error
		wantHint string
	}{
		{"docker image build fails", config.ModeDocker, fail, nil, nil, ""},
		{"docker migrations fail", config.ModeDocker, nil, fail, nil, hintDockerImageRebuilt},
		{"docker compose up fails", config.ModeDocker, nil, nil, fail, hintDockerMigrationsRan},
		{"local rebuild fails", config.ModeLocal, fail, nil, nil, hintLocalRebuildFailed},
		{"local migrations fail", config.ModeLocal, nil, fail, nil, hintLocalMigrateFailed},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			a := testApp(t)
			outsideCheckout(t)
			for _, p := range []string{a.Paths.ComposePath(), a.Paths.InstallConfigPath()} {
				if err := os.WriteFile(p, []byte("services: {}\n"), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			stubStackSteps(t, tt.build, tt.migrate, tt.up)

			err := a.updateStack(context.Background(), tt.mode, install.BackendPostgres, "v0.28.0", false, true, "")
			if !errors.Is(err, fail) {
				t.Fatalf("err = %v, want the step failure", err)
			}
			var stage *stackStageError
			gotHint := ""
			if errors.As(err, &stage) {
				gotHint = stage.hint
			}
			if gotHint != tt.wantHint {
				t.Errorf("stage hint = %q, want %q", gotHint, tt.wantHint)
			}
			if tt.wantHint != "" && !strings.Contains(err.Error(), tt.wantHint) {
				t.Errorf("error text omits the hint: %v", err)
			}
		})
	}
}

// stubStackSteps fakes the local and docker stack steps with the given results.
func stubStackSteps(t *testing.T, build, migrate, up error) {
	t.Helper()
	origUv, origLocal, origLocalMig := ensureUv, buildLocalStack, runLocalMigrations
	origImages, origMig, origUp := buildStackImages, runComposeMigrations, composeUp
	t.Cleanup(func() {
		ensureUv, buildLocalStack, runLocalMigrations = origUv, origLocal, origLocalMig
		buildStackImages, runComposeMigrations, composeUp = origImages, origMig, origUp
	})
	ensureUv = func(io.Writer) {}
	buildLocalStack = func(install.BuildPlan, io.Writer) error { return build }
	runLocalMigrations = func(context.Context, io.Writer, string, string) error { return migrate }
	buildStackImages = func(install.BuildPlan, io.Writer) error { return build }
	runComposeMigrations = func(io.Writer, string) error { return migrate }
	composeUp = func(io.Writer, string) error { return up }
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

	err := a.updateStackDocker(context.Background(), "v0.28.0", false, true, "")
	var conflict *hostPortConflictError
	if !errors.As(err, &conflict) || conflict.port != "5432" {
		t.Fatalf("err = %v, want a host-port conflict on 5432", err)
	}
	if !strings.Contains(err.Error(), "migrations failed") {
		t.Errorf("conflict error lost the failing step: %v", err)
	}
}

// writeStackCompose writes a compose file whose app and broker run image, the
// shape `jenticctl install` renders. The recorded image is the only record of
// how the stack was installed, so it is what an update reads to decide between
// pulling and building.
func writeStackCompose(t *testing.T, a *app, image string) {
	t.Helper()
	body := "name: jentic\nservices:\n" +
		"  db:\n    image: postgres:16\n" +
		"  app:\n    image: " + image + "\n" +
		"  broker:\n    image: " + image + "\n"
	if err := os.WriteFile(a.Paths.ComposePath(), []byte(body), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
}

// stubDockerStack neutralises the Docker-touching steps of updateStackDocker
// and records which refresh route ran.
type dockerStackCalls struct {
	pulled []string
	builds int
}

func stubDockerStack(t *testing.T) *dockerStackCalls {
	t.Helper()
	// ResolveAppImage consults these, so a developer's environment must not
	// decide which image the assertions see.
	t.Setenv(install.AppImageTagEnv, "")
	t.Setenv(install.AppImageRepoEnv, "")
	calls := &dockerStackCalls{}
	origBuild, origPull, origMigrate, origUp := buildStackImages, pullAppImage, runComposeMigrations, composeUp
	t.Cleanup(func() {
		buildStackImages, pullAppImage, runComposeMigrations, composeUp = origBuild, origPull, origMigrate, origUp
	})
	buildStackImages = func(install.BuildPlan, io.Writer) error { calls.builds++; return nil }
	pullAppImage = func(_ io.Writer, ref string) error { calls.pulled = append(calls.pulled, ref); return nil }
	runComposeMigrations = func(io.Writer, string) error { return nil }
	composeUp = func(io.Writer, string) error { return nil }
	return calls
}

// A stack installed from a published image pulls the image for the target ref
// and is repointed at it — no git clone, no local build. Pins #2011.
//
// The expected tag also pins that the image resolves from the ref rather than
// from the running binary's version: the CLI half swaps the binary on disk and
// the stack half carries on in the OLD process, whose cmdcore.Version() would
// resolve the image the stack already runs (or :latest for a dev build).
func TestUpdateStackDockerPullsForAPulledInstall(t *testing.T) {
	a := testApp(t)
	writeStackCompose(t, a, "ghcr.io/jentic/jentic-one-app:0.43.0")
	calls := stubDockerStack(t)

	if err := a.updateStackDocker(context.Background(), "v0.44.0", false, true, ""); err != nil {
		t.Fatalf("updateStackDocker: %v", err)
	}

	want := "ghcr.io/jentic/jentic-one-app:0.44.0"
	if len(calls.pulled) != 1 || calls.pulled[0] != want {
		t.Errorf("pulled = %v, want exactly [%s]", calls.pulled, want)
	}
	if calls.builds != 0 {
		t.Errorf("builds = %d, want 0 — a pulled install must not build from source", calls.builds)
	}
	got, err := install.ComposeAppImage(a.Paths.ComposePath())
	if err != nil || got != want {
		t.Errorf("compose app image = (%q, %v), want %q — the stack would keep running the old image", got, err, want)
	}
}

// A stack built from source keeps building, and its compose file (already on
// the fixed local tag) is left alone.
func TestUpdateStackDockerBuildsForALocalInstall(t *testing.T) {
	a := testApp(t)
	writeStackCompose(t, a, install.AppImageTag)
	calls := stubDockerStack(t)

	if err := a.updateStackDocker(context.Background(), "v0.44.0", false, true, ""); err != nil {
		t.Fatalf("updateStackDocker: %v", err)
	}

	if calls.builds != 1 || len(calls.pulled) != 0 {
		t.Errorf("builds=%d pulled=%v, want 1 build and no pull", calls.builds, calls.pulled)
	}
	if got, err := install.ComposeAppImage(a.Paths.ComposePath()); err != nil || got != install.AppImageTag {
		t.Errorf("compose app image = (%q, %v), want %q unchanged", got, err, install.AppImageTag)
	}
}

// Updating a pulled stack to a ref with no published image builds from source
// AND moves the compose file off the published reference — otherwise compose
// keeps running the old tag and ignores the image just built.
func TestUpdateStackDockerNonReleaseRefMovesAPulledStackToTheLocalTag(t *testing.T) {
	a := testApp(t)
	writeStackCompose(t, a, "ghcr.io/jentic/jentic-one-app:0.43.0")
	calls := stubDockerStack(t)

	if err := a.updateStackDocker(context.Background(), "main", true, true, ""); err != nil {
		t.Fatalf("updateStackDocker: %v", err)
	}

	if calls.builds != 1 || len(calls.pulled) != 0 {
		t.Errorf("builds=%d pulled=%v, want 1 build and no pull for a non-release ref", calls.builds, calls.pulled)
	}
	if got, err := install.ComposeAppImage(a.Paths.ComposePath()); err != nil || got != install.AppImageTag {
		t.Errorf("compose app image = (%q, %v), want %q", got, err, install.AppImageTag)
	}
}

// A compose file naming no app image cannot be told apart, so the update builds
// (which leaves the file alone) instead of guessing a registry tag.
func TestUpdateStackDockerBuildsWhenNoAppImageIsNamed(t *testing.T) {
	a := testApp(t)
	if err := os.WriteFile(a.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	calls := stubDockerStack(t)

	if err := a.updateStackDocker(context.Background(), "v0.44.0", false, true, ""); err != nil {
		t.Fatalf("updateStackDocker: %v", err)
	}
	if calls.builds != 1 || len(calls.pulled) != 0 {
		t.Errorf("builds=%d pulled=%v, want 1 build and no pull", calls.builds, calls.pulled)
	}
}

// An unparseable compose file fails before any work: every step after the
// refresh reads the same file and cannot parse it either, so proceeding would
// cost the operator a full image build and then die anyway.
func TestUpdateStackDockerFailsFastOnAnUnparseableComposeFile(t *testing.T) {
	a := testApp(t)
	if err := os.WriteFile(a.Paths.ComposePath(), []byte("services:\n  app: [\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	calls := stubDockerStack(t)

	err := a.updateStackDocker(context.Background(), "v0.44.0", false, true, "")
	if err == nil {
		t.Fatal("updateStackDocker = nil error, want a parse failure")
	}
	if !strings.Contains(err.Error(), "compose file") {
		t.Errorf("error does not name the compose file: %v", err)
	}
	if calls.builds != 0 || len(calls.pulled) != 0 {
		t.Errorf("builds=%d pulled=%v, want no work before the parse failure", calls.builds, calls.pulled)
	}
}

// The pull keeps the registry and repository the stack already runs: a mirrored
// install must not be marched onto the published default, which would violate
// the operator's registry policy and fail outright on a host that cannot reach
// ghcr.io.
func TestUpdateStackDockerPullKeepsAMirrorsRegistry(t *testing.T) {
	a := testApp(t)
	writeStackCompose(t, a, "registry.corp/jentic-one-app:0.43.0")
	calls := stubDockerStack(t)

	if err := a.updateStackDocker(context.Background(), "v0.44.0", false, true, ""); err != nil {
		t.Fatalf("updateStackDocker: %v", err)
	}

	want := "registry.corp/jentic-one-app:0.44.0"
	if len(calls.pulled) != 1 || calls.pulled[0] != want {
		t.Errorf("pulled = %v, want exactly [%s]", calls.pulled, want)
	}
	if got, err := install.ComposeAppImage(a.Paths.ComposePath()); err != nil || got != want {
		t.Errorf("compose app image = (%q, %v), want %q", got, err, want)
	}
}

// An unpinned non-release ref is a FALLBACK — the tracked ref used when the
// release lookup fails (rate limit, offline). Converting a published-image
// stack to a from-source one is permanent and has no undo flag, so a transient
// network error must not trigger it.
func TestUpdateStackDockerRefusesToConvertAPulledStackOnAnUnpinnedNonReleaseRef(t *testing.T) {
	a := testApp(t)
	writeStackCompose(t, a, "ghcr.io/jentic/jentic-one-app:0.43.0")
	calls := stubDockerStack(t)

	err := a.updateStackDocker(context.Background(), "main", false, true, "")
	if err == nil {
		t.Fatal("updateStackDocker = nil error, want a refusal")
	}
	if !strings.Contains(err.Error(), "--ref") {
		t.Errorf("error does not say how to proceed: %v", err)
	}
	if calls.builds != 0 || len(calls.pulled) != 0 {
		t.Errorf("builds=%d pulled=%v, want no work at all", calls.builds, calls.pulled)
	}
	if got, _ := install.ComposeAppImage(a.Paths.ComposePath()); got != "ghcr.io/jentic/jentic-one-app:0.43.0" {
		t.Errorf("compose app image = %q, want the install left untouched", got)
	}
}

// A digest-pinned stack names an exact image and there is no way to learn the
// new digest, so the update refuses rather than downgrading it to a tag.
func TestUpdateStackDockerRefusesToMoveADigestPinnedStack(t *testing.T) {
	a := testApp(t)
	writeStackCompose(t, a, "ghcr.io/jentic/jentic-one-app@sha256:"+strings.Repeat("ab", 32))
	calls := stubDockerStack(t)

	err := a.updateStackDocker(context.Background(), "v0.44.0", false, true, "")
	if err == nil || !strings.Contains(err.Error(), "digest") {
		t.Fatalf("err = %v, want a refusal naming the digest pin", err)
	}
	if calls.builds != 0 || len(calls.pulled) != 0 {
		t.Errorf("builds=%d pulled=%v, want no work at all", calls.builds, calls.pulled)
	}
}

// The operators #2011 actually hit have a manifest claiming the new version
// while the compose file still runs the old image. That mismatch must count as
// "needs a refresh", or the fix only ever reaches installs that were never
// broken.
func TestDockerStackImageLagsRecord(t *testing.T) {
	for _, tc := range []struct {
		name, image, recorded string
		want                  bool
	}{
		{"compose behind the record", "ghcr.io/jentic/jentic-one-app:0.43.0", "v0.44.0", true},
		{"compose matches the record", "ghcr.io/jentic/jentic-one-app:0.44.0", "v0.44.0", false},
		{"compose ahead of the record", "ghcr.io/jentic/jentic-one-app:0.45.0", "v0.44.0", false},
		{"from-source stack", install.AppImageTag, "v0.44.0", false},
		{"digest pin has no version to compare", "ghcr.io/jentic/jentic-one-app@sha256:" + strings.Repeat("ab", 32), "v0.44.0", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a := testApp(t)
			writeStackCompose(t, a, tc.image)
			m := &config.Manifest{Mode: config.ModeDocker, StackRef: tc.recorded}
			if got := a.dockerStackImageLagsRecord(m); got != tc.want {
				t.Errorf("dockerStackImageLagsRecord = %v, want %v", got, tc.want)
			}
		})
	}
}

// A pulled stack updated to a ref with NO published image is refreshed by
// building, and that build reads the operator's checkout like any other — so
// the #949 guard must still apply. Reporting "this will pull" for any published
// stack regardless of the ref skipped it, and the build then silently used the
// checkout's HEAD instead of the pinned ref.
func TestDockerStackPullsOnUpdateIsFalseForARefWithNoPublishedImage(t *testing.T) {
	a := testApp(t)
	writeStackCompose(t, a, "ghcr.io/jentic/jentic-one-app:0.43.0")

	pulls, err := a.dockerStackPullsOnUpdate(config.ModeDocker, "v0.44.0", "")
	if err != nil || !pulls {
		t.Errorf("release ref: pulls=%v err=%v, want true", pulls, err)
	}
	pulls, err = a.dockerStackPullsOnUpdate(config.ModeDocker, "main", "")
	if err != nil {
		t.Fatalf("non-release ref: %v", err)
	}
	if pulls {
		t.Error("non-release ref reports a pull, so the pinned-ref guard would be skipped")
	}
}

// An unreadable compose file must surface as the parse failure, not collapse
// into "false" and be diagnosed as a checkout conflict that is not the problem.
func TestDockerStackPullsOnUpdateReportsAnUnreadableComposeFile(t *testing.T) {
	a := testApp(t)
	if err := os.WriteFile(a.Paths.ComposePath(), []byte("services:\n  app: [\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	if _, err := a.dockerStackPullsOnUpdate(config.ModeDocker, "v0.44.0", ""); err == nil {
		t.Error("dockerStackPullsOnUpdate = nil error, want the parse failure to propagate")
	}
}

// The refusals the stack half cannot escape are raised BEFORE the CLI half
// swaps binaries. Raising them afterwards leaves the CLI ahead of the stack
// with every retry hitting the same wall and no flag to work around it.
func TestCheckDockerRefreshPossibleRefusesBeforeTheCLIHalf(t *testing.T) {
	for _, tc := range []struct {
		name, image, ref string
		pinned           bool
		wantErr          string
	}{
		{"digest pin", "ghcr.io/jentic/jentic-one-app@sha256:" + strings.Repeat("ab", 32), "v0.44.0", false, "digest"},
		{"operator tag pin", "ghcr.io/jentic/jentic-one-app:canary", "v0.44.0", false, "canary"},
		{"unpinned non-release", "ghcr.io/jentic/jentic-one-app:0.43.0", "main", false, "--ref"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a := testApp(t)
			writeStackCompose(t, a, tc.image)
			err := a.checkDockerRefreshPossible(config.ModeDocker, tc.ref, tc.pinned, "")
			if err == nil {
				t.Fatalf("checkDockerRefreshPossible = nil error, want a refusal")
			}
			if !strings.Contains(err.Error(), tc.wantErr) {
				t.Errorf("error %q does not mention %q", err, tc.wantErr)
			}
		})
	}
}

// An explicit --image-tag is the way past the pin refusals: without it a
// digest- or tag-pinned stack cannot be updated at all, and because the refusal
// precedes the CLI half it would block that too.
func TestImageTagUnblocksAPinnedStack(t *testing.T) {
	digest := "ghcr.io/jentic/jentic-one-app@sha256:" + strings.Repeat("ab", 32)
	for _, tc := range []struct{ name, image, override, want string }{
		{"digest pin moves to a tag", digest, "0.44.0", "ghcr.io/jentic/jentic-one-app:0.44.0"},
		{"tag pin moves to the release", "ghcr.io/jentic/jentic-one-app:canary", "0.44.0", "ghcr.io/jentic/jentic-one-app:0.44.0"},
		{"mirror keeps its registry", "registry.corp/jentic-one-app:canary", "0.44.0", "registry.corp/jentic-one-app:0.44.0"},
		{"a new pin is honoured", "ghcr.io/jentic/jentic-one-app:0.43.0", "canary", "ghcr.io/jentic/jentic-one-app:canary"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a := testApp(t)
			writeStackCompose(t, a, tc.image)
			calls := stubDockerStack(t)

			// Not refused up front any more ...
			if err := a.checkDockerRefreshPossible(config.ModeDocker, "v0.44.0", false, tc.override); err != nil {
				t.Fatalf("checkDockerRefreshPossible = %v, want nil with --image-tag", err)
			}
			// ... and the refresh pulls and repoints to the named image.
			if err := a.updateStackDocker(context.Background(), "v0.44.0", false, true, tc.override); err != nil {
				t.Fatalf("updateStackDocker: %v", err)
			}
			if len(calls.pulled) != 1 || calls.pulled[0] != tc.want {
				t.Errorf("pulled = %v, want exactly [%s]", calls.pulled, tc.want)
			}
			if got, err := install.ComposeAppImage(a.Paths.ComposePath()); err != nil || got != tc.want {
				t.Errorf("compose app image = (%q, %v), want %q", got, err, tc.want)
			}
			if calls.builds != 0 {
				t.Errorf("builds = %d, want 0", calls.builds)
			}
		})
	}
}

// --image-tag has no meaning for a stack built from source: say so and name the
// reinstall that would move it onto published images, rather than half-applying.
func TestImageTagOnAFromSourceStackIsRefused(t *testing.T) {
	a := testApp(t)
	writeStackCompose(t, a, install.AppImageTag)

	err := a.checkDockerRefreshPossible(config.ModeDocker, "v0.44.0", false, "0.44.0")
	if err == nil {
		t.Fatal("checkDockerRefreshPossible = nil error, want a refusal")
	}
	if !strings.Contains(err.Error(), "jenticctl install") {
		t.Errorf("error does not name the reinstall remedy: %v", err)
	}
}

// An unpinned non-release ref is still refused WITHOUT --image-tag, so the new
// flag does not quietly reopen the accidental-conversion path.
func TestImageTagDoesNotReopenTheUnpinnedConversion(t *testing.T) {
	a := testApp(t)
	writeStackCompose(t, a, "ghcr.io/jentic/jentic-one-app:0.43.0")

	if err := a.checkDockerRefreshPossible(config.ModeDocker, "main", false, ""); err == nil {
		t.Error("checkDockerRefreshPossible = nil error, want the unpinned refusal to stand")
	}
}

// The routes that CAN proceed are not refused up front.
func TestCheckDockerRefreshPossibleAllowsTheWorkableRoutes(t *testing.T) {
	for _, tc := range []struct {
		name, image, ref string
		pinned           bool
	}{
		{"release pull", "ghcr.io/jentic/jentic-one-app:0.43.0", "v0.44.0", false},
		{"latest fallback moves", "ghcr.io/jentic/jentic-one-app:latest", "v0.44.0", false},
		{"pinned conversion", "ghcr.io/jentic/jentic-one-app:0.43.0", "main", true},
		{"from-source stack", install.AppImageTag, "main", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a := testApp(t)
			writeStackCompose(t, a, tc.image)
			if err := a.checkDockerRefreshPossible(config.ModeDocker, tc.ref, tc.pinned, ""); err != nil {
				t.Errorf("checkDockerRefreshPossible = %v, want nil", err)
			}
		})
	}
}

// Converting a pulled stack to a from-source one rewrites the compose file for
// good, so a later failure must say that — not just that an image was rebuilt.
func TestUpdateStackDockerConversionHintNamesTheComposeRepoint(t *testing.T) {
	a := testApp(t)
	writeStackCompose(t, a, "ghcr.io/jentic/jentic-one-app:0.43.0")
	stubDockerStack(t)
	origMigrate := runComposeMigrations
	t.Cleanup(func() { runComposeMigrations = origMigrate })
	runComposeMigrations = func(io.Writer, string) error { return errors.New("exit status 1") }

	err := a.updateStackDocker(context.Background(), "main", true, true, "")
	var staged *stackStageError
	if !errors.As(err, &staged) {
		t.Fatalf("err = %v, want a stackStageError", err)
	}
	if staged.hint != hintDockerConverted {
		t.Errorf("hint = %q, want the conversion hint", staged.hint)
	}
}

// A failure after the image step must name what is actually on disk: the pull
// route rebuilt nothing, so telling the operator the image was rebuilt sends
// them looking for the wrong state.
func TestUpdateStackDockerPullRouteHintNamesTheComposeChangeNotARebuild(t *testing.T) {
	a := testApp(t)
	writeStackCompose(t, a, "ghcr.io/jentic/jentic-one-app:0.43.0")
	stubDockerStack(t)
	origMigrate := runComposeMigrations
	t.Cleanup(func() { runComposeMigrations = origMigrate })
	runComposeMigrations = func(io.Writer, string) error { return errors.New("exit status 1") }

	err := a.updateStackDocker(context.Background(), "v0.44.0", false, true, "")
	var staged *stackStageError
	if !errors.As(err, &staged) {
		t.Fatalf("err = %v, want a stackStageError", err)
	}
	if staged.hint != hintDockerImagePulled {
		t.Errorf("hint = %q, want the pulled-image hint", staged.hint)
	}
	if strings.Contains(staged.hint, "rebuilt") {
		t.Errorf("hint claims a rebuild that never happened: %q", staged.hint)
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
