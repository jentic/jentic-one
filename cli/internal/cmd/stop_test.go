package cmd

import (
	"errors"
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

	err := app.stopE(&stopOptions{})
	if !errors.Is(err, sentinel) {
		t.Fatalf("stop should surface the daemon guard error, got %v", err)
	}
}

// The guard applies to the destructive --volumes path too (the confirmation
// prompt is downstream of the guard, so a down daemon fails before prompting).
func TestStopDockerVolumesFailsFastWhenDaemonDown(t *testing.T) {
	app := testApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	sentinel := stubDaemonDown(t)

	err := app.stopE(&stopOptions{volumes: true, yes: true})
	if !errors.Is(err, sentinel) {
		t.Fatalf("stop --volumes should surface the daemon guard error, got %v", err)
	}
}
