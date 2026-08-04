package cmd

import (
	"bytes"
	"errors"
	"os"
	"strings"
	"testing"
)

// With --yes the command is non-interactive, so missing or too-short inputs
// must be rejected up front — before any docker/venv dispatch.
func TestSetupNonInteractiveValidation(t *testing.T) {
	cases := []struct {
		name    string
		opts    *setupOptions
		wantSub string
	}{
		{
			name:    "missing email",
			opts:    &setupOptions{yes: true, password: "a-strong-password"},
			wantSub: "email is required",
		},
		{
			name:    "short password",
			opts:    &setupOptions{yes: true, email: "admin@example.com", password: "short"},
			wantSub: "at least",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			app := testApp(t)
			err := app.setupE(tc.opts)
			if err == nil {
				t.Fatalf("expected validation error, got nil")
			}
			if !strings.Contains(err.Error(), tc.wantSub) {
				t.Errorf("error = %q, want substring %q", err.Error(), tc.wantSub)
			}
		})
	}
}

// A valid request against a tempdir with no install must fail at dispatch
// (no compose file, no venv) rather than at input validation — proving inputs
// passed validation and the command tried to act.
func TestSetupRequiresInstall(t *testing.T) {
	app := testApp(t)
	err := app.setupE(&setupOptions{
		yes:      true,
		email:    "admin@example.com",
		password: "a-strong-password",
	})
	if err == nil {
		t.Fatalf("expected error when not installed")
	}
	if !strings.Contains(err.Error(), "install") {
		t.Errorf("error = %q, want it to point at `jenticctl install`", err.Error())
	}
}

// A Docker install (compose file present) with a stopped daemon must fail fast
// with the guard's actionable error, before the one-shot admin container is
// started (which would otherwise surface a raw compose transport error).
func TestSetupDockerFailsFastWhenDaemonDown(t *testing.T) {
	app := testApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	sentinel := stubDaemonDown(t)

	err := app.setupE(&setupOptions{
		yes:      true,
		email:    "admin@example.com",
		password: "a-strong-password",
	})
	if !errors.Is(err, sentinel) {
		t.Fatalf("setup should surface the daemon guard error, got %v", err)
	}
	// The guard runs after the "Creating ..." banner but before the container
	// runs; the success line must never appear when the daemon is down.
	if got := app.Out.(*bytes.Buffer).String(); strings.Contains(got, "Admin account created") {
		t.Errorf("setup ran past the guard when the daemon was down:\n%s", got)
	}
}
