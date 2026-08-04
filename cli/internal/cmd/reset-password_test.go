package cmd

import (
	"bytes"
	"context"
	"errors"
	"os"
	"strings"
	"testing"
)

// With --yes the command is non-interactive, so missing or too-short inputs
// must be rejected up front — before any docker/venv dispatch.
func TestResetPasswordNonInteractiveValidation(t *testing.T) {
	cases := []struct {
		name    string
		opts    *resetPasswordOptions
		wantSub string
	}{
		{
			name:    "missing email",
			opts:    &resetPasswordOptions{yes: true, password: "a-strong-password"},
			wantSub: "email is required",
		},
		{
			name:    "short password",
			opts:    &resetPasswordOptions{yes: true, email: "user@example.com", password: "short"},
			wantSub: "at least",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			app := testApp(t)
			err := app.resetPasswordE(context.Background(), tc.opts)
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
func TestResetPasswordRequiresInstall(t *testing.T) {
	app := testApp(t)
	err := app.resetPasswordE(context.Background(), &resetPasswordOptions{
		yes:      true,
		email:    "user@example.com",
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
// with the guard's actionable error, before the one-shot reset container is
// started (which would otherwise surface a raw compose transport error).
func TestResetPasswordDockerFailsFastWhenDaemonDown(t *testing.T) {
	app := testApp(t)
	if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("write compose: %v", err)
	}
	sentinel := stubDaemonDown(t)

	err := app.resetPasswordE(context.Background(), &resetPasswordOptions{
		yes:      true,
		email:    "user@example.com",
		password: "a-strong-password",
	})
	if !errors.Is(err, sentinel) {
		t.Fatalf("reset-password should surface the daemon guard error, got %v", err)
	}
	if got := app.Out.(*bytes.Buffer).String(); strings.Contains(got, "Temporary password set") {
		t.Errorf("reset-password ran past the guard when the daemon was down:\n%s", got)
	}
}
