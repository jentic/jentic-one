package ctlcmd

import (
	"bytes"
	"context"
	"errors"
	"os"
	"strings"
	"testing"
)

func TestAdminFlow_SharedValidation(t *testing.T) {
	flows := []struct {
		name          string
		run           func(a *app, ctx context.Context, opts *adminFlowOptions) error
		wantEmailErr  string
		wantPassErr   string
		promptHeading string
	}{
		{
			name:          "setup",
			run:           (*app).setupE,
			wantEmailErr:  "admin email is required (use --email or run interactively)",
			wantPassErr:   "admin password must be at least 12 characters",
			promptHeading: "First-run setup",
		},
		{
			name:          "reset-password",
			run:           (*app).resetPasswordE,
			wantEmailErr:  "user email is required (use --email or run interactively)",
			wantPassErr:   "temporary password must be at least 12 characters",
			promptHeading: "Reset a user's password",
		},
	}

	for _, fl := range flows {
		t.Run(fl.name, func(t *testing.T) {
			t.Run("yes without email", func(t *testing.T) {
				app := testApp(t)
				err := fl.run(app, context.Background(), &adminFlowOptions{
					yes:      true,
					password: "a-strong-password",
				})
				if err == nil || err.Error() != fl.wantEmailErr {
					t.Fatalf("error = %v, want %q", err, fl.wantEmailErr)
				}
			})

			t.Run("11-character password rejected", func(t *testing.T) {
				app := testApp(t)
				err := fl.run(app, context.Background(), &adminFlowOptions{
					yes:      true,
					email:    "admin@example.com",
					password: "11character", // 11 bytes < minPasswordLen (12)
				})
				if err == nil || err.Error() != fl.wantPassErr {
					t.Fatalf("error = %v, want %q", err, fl.wantPassErr)
				}
			})

			t.Run("missing venv gives no local environment error", func(t *testing.T) {
				app := testApp(t)
				if err := os.WriteFile(app.Paths.InstallConfigPath(), []byte("{}\n"), 0o600); err != nil {
					t.Fatalf("write config: %v", err)
				}
				err := fl.run(app, context.Background(), &adminFlowOptions{
					yes:      true,
					email:    "admin@example.com",
					password: "a-strong-password",
				})
				if err == nil || !strings.Contains(err.Error(), "no local environment") {
					t.Fatalf("error = %v, want substring %q", err, "no local environment")
				}
			})

			t.Run("docker install with daemon down fails before prompt", func(t *testing.T) {
				app := testApp(t)
				if err := os.WriteFile(app.Paths.ComposePath(), []byte("services: {}\n"), 0o600); err != nil {
					t.Fatalf("write compose: %v", err)
				}
				sentinel := stubDaemonDown(t)

				// Leave yes=false and email/password empty: the daemon pre-probe
				// must fail before any interactive prompt or credential check runs.
				err := fl.run(app, context.Background(), &adminFlowOptions{})
				if !errors.Is(err, sentinel) {
					t.Fatalf("expected daemon guard error %v, got %v", sentinel, err)
				}
				if got := app.Out.(*bytes.Buffer).String(); strings.Contains(got, fl.promptHeading) {
					t.Errorf("prompt heading rendered despite daemon down:\n%s", got)
				}
			})
		})
	}
}
