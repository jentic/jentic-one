package cmd

import (
	"bytes"
	"errors"
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
	requireDockerDaemon = func(string) error { return sentinel }
	return sentinel
}

// A Docker install (compose file present) with a stopped daemon must fail fast
// with the guard's actionable error, before any `docker compose up` is run.
func TestStartDockerFailsFastWhenDaemonDown(t *testing.T) {
	app := testApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	sentinel := stubDaemonDown(t)

	err := app.startE(&startOptions{})
	if !errors.Is(err, sentinel) {
		t.Fatalf("start should surface the daemon guard error, got %v", err)
	}
	// The guard short-circuits before announcing the stack start.
	if got := app.Out.(*bytes.Buffer).String(); strings.Contains(got, "Starting Docker stack") {
		t.Errorf("startDocker ran past the guard when the daemon was down:\n%s", got)
	}
}
