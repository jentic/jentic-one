package ctlcmd

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/install"
)

// dockerStackFakes swaps every Docker effect installDocker reaches for a
// recorder: calls lists the steps in the order they ran. Each step's error
// is the matching field (nil succeeds); fresh is what the data-volume probe
// answers ("no such volume" when true).
type dockerStackFakes struct {
	calls                []string
	build, migrate, up   error
	pull                 error
	fresh                bool
	pulledRef, resetPath string
	migratePath, upPath  string
}

func installDockerFakes(t *testing.T, f *dockerStackFakes) {
	t.Helper()
	origPreflight, origPull, origExists, origReset := dockerPreflight, pullAppImage, dataVolumeExists, resetFreshDataVolumes
	origBuild, origMigrate, origUp := buildStackImages, runComposeMigrations, composeUp
	t.Cleanup(func() {
		dockerPreflight, pullAppImage, dataVolumeExists, resetFreshDataVolumes = origPreflight, origPull, origExists, origReset
		buildStackImages, runComposeMigrations, composeUp = origBuild, origMigrate, origUp
	})
	dockerPreflight = func(context.Context, *install.Draft) []install.CheckResult {
		f.calls = append(f.calls, "preflight")
		return nil
	}
	pullAppImage = func(_ io.Writer, ref string) error {
		f.calls = append(f.calls, "pull")
		f.pulledRef = ref
		return f.pull
	}
	dataVolumeExists = func(string) (bool, error) { return !f.fresh, nil }
	resetFreshDataVolumes = func(_ io.Writer, composePath string, _ []string) error {
		f.calls = append(f.calls, "reset")
		f.resetPath = composePath
		return nil
	}
	buildStackImages = func(install.BuildPlan, io.Writer) error {
		f.calls = append(f.calls, "build")
		return f.build
	}
	runComposeMigrations = func(_ io.Writer, composePath string) error {
		f.calls = append(f.calls, "migrate")
		f.migratePath = composePath
		return f.migrate
	}
	composeUp = func(_ io.Writer, composePath string) error {
		f.calls = append(f.calls, "up")
		f.upPath = composePath
		return f.up
	}
}

func runInstallDocker(t *testing.T, opts *installOptions) (*app, *install.Draft, error) {
	t.Helper()
	a := testApp(t)
	draft := install.NewDraft()
	if err := draft.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	configPath := filepath.Join(a.Paths.Root, "jentic-one.yaml")
	// The compose writer creates the logs dir world-writable, so it insists
	// on a private (0700) parent, as ~/.jentic is.
	if err := os.Chmod(a.Paths.Root, 0o700); err != nil {
		t.Fatalf("chmod root: %v", err)
	}
	logsDir := filepath.Join(a.Paths.Root, "logs")
	err := a.installDocker(context.Background(), draft, configPath, logsDir, opts)
	return a, draft, err
}

// The build path (a source checkout is in play: this test runs inside one)
// builds, writes the compose stack, migrates and brings it up, in that order.
func TestInstallDockerBuildsMigratesAndStartsThroughTheSeams(t *testing.T) {
	f := &dockerStackFakes{}
	installDockerFakes(t, f)
	a, draft, err := runInstallDocker(t, &installOptions{buildLocal: true})
	if err != nil {
		t.Fatalf("installDocker: %v", err)
	}
	if want := []string{"preflight", "build", "migrate", "up"}; !reflect.DeepEqual(f.calls, want) {
		t.Errorf("calls = %v, want %v", f.calls, want)
	}
	compose := a.Paths.ComposePath()
	if f.migratePath != compose || f.upPath != compose {
		t.Errorf("migrate=%q up=%q, want both on %q", f.migratePath, f.upPath, compose)
	}
	if _, err := os.Stat(compose); err != nil {
		t.Errorf("compose stack not written: %v", err)
	}
	if !draft.MigrationsDone || !draft.AppStarted || draft.ComposePath != compose {
		t.Errorf("draft = migrations %v, started %v, compose %q", draft.MigrationsDone, draft.AppStarted, draft.ComposePath)
	}
}

// Outside a source checkout, a pinned image is pulled instead of built.
func TestInstallDockerPullsAPinnedImage(t *testing.T) {
	t.Chdir(t.TempDir())
	t.Setenv(install.SrcEnv, "")
	t.Setenv(install.AppImageTagEnv, "")
	f := &dockerStackFakes{}
	installDockerFakes(t, f)
	if _, _, err := runInstallDocker(t, &installOptions{imageTag: "9.9.9"}); err != nil {
		t.Fatalf("installDocker: %v", err)
	}
	if want := []string{"preflight", "pull", "migrate", "up"}; !reflect.DeepEqual(f.calls, want) {
		t.Errorf("calls = %v, want %v", f.calls, want)
	}
	if !strings.Contains(f.pulledRef, "9.9.9") {
		t.Errorf("pulled %q, want the pinned 9.9.9 image", f.pulledRef)
	}
}

// A failed compose up is non-fatal: the stack is built, configured and
// migrated, and `jenticctl start` brings it up later.
func TestInstallDockerComposeUpFailureIsNonFatal(t *testing.T) {
	f := &dockerStackFakes{up: errors.New("port is already allocated")}
	installDockerFakes(t, f)
	a, draft, err := runInstallDocker(t, &installOptions{buildLocal: true})
	if err != nil {
		t.Fatalf("installDocker = %v, want nil (compose up failure is non-fatal)", err)
	}
	if !draft.MigrationsDone || draft.AppStarted {
		t.Errorf("draft = migrations %v, started %v, want migrated but not started", draft.MigrationsDone, draft.AppStarted)
	}
	if out := a.Out.(*bytes.Buffer).String(); !strings.Contains(out, "port is already allocated") {
		t.Errorf("output does not carry the start warning:\n%s", out)
	}
}

// --no-start stops after the migrations.
func TestInstallDockerNoStartSkipsComposeUp(t *testing.T) {
	f := &dockerStackFakes{}
	installDockerFakes(t, f)
	if _, _, err := runInstallDocker(t, &installOptions{buildLocal: true, noStart: true}); err != nil {
		t.Fatalf("installDocker: %v", err)
	}
	if want := []string{"preflight", "build", "migrate"}; !reflect.DeepEqual(f.calls, want) {
		t.Errorf("calls = %v, want %v", f.calls, want)
	}
}

// A failed first migration on volumes this run created discards them, so a
// re-run starts clean; compose up never runs.
func TestInstallDockerMigrationFailureResetsFreshVolumes(t *testing.T) {
	f := &dockerStackFakes{migrate: errors.New("exit status 1"), fresh: true}
	installDockerFakes(t, f)
	a, draft, err := runInstallDocker(t, &installOptions{buildLocal: true})
	if err == nil || !strings.Contains(err.Error(), "freshly created database volume was removed") {
		t.Fatalf("installDocker = %v, want the fresh-volume recovery error", err)
	}
	if want := []string{"preflight", "build", "migrate", "reset"}; !reflect.DeepEqual(f.calls, want) {
		t.Errorf("calls = %v, want %v", f.calls, want)
	}
	if f.resetPath != a.Paths.ComposePath() || draft.MigrationsDone {
		t.Errorf("reset on %q (migrations done %v), want a reset of %q", f.resetPath, draft.MigrationsDone, a.Paths.ComposePath())
	}
}

// Volumes that pre-existed the install may hold real data: a failed
// migration leaves them alone and names the manual reset instead.
func TestInstallDockerMigrationFailureKeepsPreexistingVolumes(t *testing.T) {
	f := &dockerStackFakes{migrate: errors.New("exit status 1")}
	installDockerFakes(t, f)
	a, _, err := runInstallDocker(t, &installOptions{buildLocal: true})
	if err == nil || !strings.Contains(err.Error(), "migrations failed") {
		t.Fatalf("installDocker = %v, want a migrations failure", err)
	}
	if want := []string{"preflight", "build", "migrate"}; !reflect.DeepEqual(f.calls, want) {
		t.Errorf("calls = %v, want %v (no reset)", f.calls, want)
	}
	if out := a.Out.(*bytes.Buffer).String(); !strings.Contains(out, "pre-existed this install") {
		t.Errorf("output does not name the pre-existing volume:\n%s", out)
	}
}

// A failed image build stops before anything is written or migrated.
func TestInstallDockerBuildFailureStops(t *testing.T) {
	f := &dockerStackFakes{build: errors.New("docker build failed")}
	installDockerFakes(t, f)
	a, _, err := runInstallDocker(t, &installOptions{buildLocal: true})
	if err == nil || !strings.Contains(err.Error(), "image build failed") {
		t.Fatalf("installDocker = %v, want the image build failure", err)
	}
	if want := []string{"preflight", "build"}; !reflect.DeepEqual(f.calls, want) {
		t.Errorf("calls = %v, want %v", f.calls, want)
	}
	if _, err := os.Stat(a.Paths.ComposePath()); !os.IsNotExist(err) {
		t.Errorf("compose stack written after a failed build (stat err %v)", err)
	}
}
