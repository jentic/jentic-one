package cmd

import (
	"errors"
	"io"
	"os"
	"testing"
)

// A Docker install with a stopped daemon must fail fast on `stop` with the
// guard's actionable error rather than a raw `docker compose down` failure.
func TestStopDockerFailsFastWhenDaemonDown(t *testing.T) {
	app := testApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	sentinel := stubDaemonDown(t)

	origDown := composeDown
	t.Cleanup(func() { composeDown = origDown })
	composeDown = func(io.Writer, string) error {
		t.Fatal("composeDown must not run when the daemon is down")
		return nil
	}

	err := app.stopE(&stopOptions{})
	if !errors.Is(err, sentinel) {
		t.Fatalf("stop should surface the daemon guard error, got %v", err)
	}
}

// The guard must run BEFORE the destructive --volumes confirmation prompt.
// Deliberately omit `yes: true`: if the guard is correctly first, a down daemon
// returns the sentinel and the interactive `huh` confirm is never reached (so
// the test doesn't block on stdin). A regression that moved the guard below the
// prompt would hang here instead of returning — proving the ordering.
func TestStopDockerVolumesFailsFastBeforeConfirm(t *testing.T) {
	app := testApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	sentinel := stubDaemonDown(t)

	origDownV := composeDownVolumes
	t.Cleanup(func() { composeDownVolumes = origDownV })
	composeDownVolumes = func(io.Writer, string) error {
		t.Fatal("composeDownVolumes must not run when the daemon is down")
		return nil
	}

	err := app.stopE(&stopOptions{volumes: true})
	if !errors.Is(err, sentinel) {
		t.Fatalf("stop --volumes should surface the daemon guard error before confirming, got %v", err)
	}
}
