package ctlcmd

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"strings"
	"testing"
)

// stubDaemonDown replaces the runtime daemon guard with one that reports the
// daemon down, and restores it after the test. It returns the sentinel error
// the stub yields so callers can assert it propagated unchanged.
func stubDaemonDown(t *testing.T) error {
	t.Helper()
	orig := requireDockerDaemon
	t.Cleanup(func() { requireDockerDaemon = orig })
	sentinel := errors.New("docker daemon is not responding: start Docker Desktop")
	requireDockerDaemon = func(context.Context, string) error { return sentinel }
	return sentinel
}

// stubDaemonUp replaces the runtime daemon guard with one that reports the
// daemon healthy (nil error), so tests can exercise the pass-through path.
func stubDaemonUp(t *testing.T) {
	t.Helper()
	orig := requireDockerDaemon
	t.Cleanup(func() { requireDockerDaemon = orig })
	requireDockerDaemon = func(context.Context, string) error { return nil }
}

// A Docker install (compose file present) with a stopped daemon must fail fast
// with the guard's actionable error, before any `docker compose up` is run.
func TestStartDockerFailsFastWhenDaemonDown(t *testing.T) {
	app := testApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	sentinel := stubDaemonDown(t)

	// Fail the test if the guard is bypassed and compose is actually invoked.
	origUp := composeUp
	t.Cleanup(func() { composeUp = origUp })
	composeUp = func(io.Writer, string) error {
		t.Fatal("composeUp must not run when the daemon is down")
		return nil
	}

	err := app.startE(context.Background(), &startOptions{})
	if !errors.Is(err, sentinel) {
		t.Fatalf("start should surface the daemon guard error, got %v", err)
	}
	// The guard short-circuits before announcing the stack start.
	if got := app.Out.(*bytes.Buffer).String(); strings.Contains(got, "Starting Docker stack") {
		t.Errorf("startDocker ran past the guard when the daemon was down:\n%s", got)
	}
}

// With the daemon healthy, the guard passes through and startDocker proceeds to
// bring the stack up. A compose seam stub keeps this off a real Docker.
func TestStartDockerProceedsWhenDaemonUp(t *testing.T) {
	app := testApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	stubDaemonUp(t)

	var upCalls int
	var gotPath string
	origUp := composeUp
	t.Cleanup(func() { composeUp = origUp })
	composeUp = func(_ io.Writer, path string) error {
		upCalls++
		gotPath = path
		return nil
	}

	if err := app.startE(context.Background(), &startOptions{}); err != nil {
		t.Fatalf("start with a healthy daemon should succeed, got %v", err)
	}
	if upCalls != 1 {
		t.Errorf("composeUp called %d times, want 1", upCalls)
	}
	if gotPath != app.Paths.ComposePath() {
		t.Errorf("composeUp path = %q, want %q", gotPath, app.Paths.ComposePath())
	}
	if got := app.Out.(*bytes.Buffer).String(); !strings.Contains(got, "Starting Docker stack") {
		t.Errorf("expected startDocker to announce the stack start, got:\n%s", got)
	}
}
