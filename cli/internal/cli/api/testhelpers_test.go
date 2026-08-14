package api

import (
	"bytes"
	"context"
	"os"
	"testing"
	"time"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// testApp builds an api-tree receiver backed by a throwaway cmdcore.App with a
// temp-dir Paths and buffered streams, matching the pre-split white-box helper.
// The approval-poll cadence is shrunk to milliseconds so pending-path register/
// access tests don't burn real wall-clock seconds.
func testApp(t *testing.T) *app {
	t.Helper()
	a := &app{App: &cmdcore.App{
		Paths: config.Paths{Root: t.TempDir()},
		Out:   new(bytes.Buffer),
		Err:   new(bytes.Buffer),
	}}
	a.SetPollCadence(2*time.Millisecond, 5*time.Millisecond, time.Millisecond)
	return a
}

// activeCtx returns a context carrying an active state pointed at baseURL with
// an injected bearer token, so direct command-method calls resolve a session
// without any disk config or network token exchange — the white-box equivalent
// of the file-less env override.
func activeCtx(baseURL string) context.Context {
	return clictx.WithActiveState(context.Background(), &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:        "test-agent",
			EnvironmentName:     "test",
			BaseURL:             baseURL,
			InjectedBearerToken: "tok_abc",
		},
		Mode: clictx.ModeHuman,
	})
}

// seedRegistered points full-tree executions (root.Execute) at baseURL via the
// file-less env override (JENTIC_BASE_URL + JENTIC_BEARER_TOKEN), so the root
// interceptor resolves an authenticated state without disk config. The app
// arg is kept for call-site compatibility with the old profile-store seeder.
func seedRegistered(t *testing.T, _ *app, _ string, baseURL string) {
	t.Helper()
	t.Setenv("JENTIC_BASE_URL", baseURL)
	t.Setenv("JENTIC_BEARER_TOKEN", "tok_abc")
}

// stubDetect wires the App's environment-detection seam so skill/bootstrap tests
// can control which agents "exist" without touching the real filesystem beyond
// the given home/cwd.
func stubDetect(t *testing.T, app *app, home, cwd string, detected ...string) {
	t.Helper()
	byName := map[string]bool{}
	for _, d := range detected {
		byName[d] = true
	}
	app.DetectEnv = func() (skillgen.DetectEnv, error) {
		return skillgen.DetectEnv{
			Home:   home,
			Cwd:    cwd,
			Lookup: func(name string) bool { return byName[name] },
			Stat:   func(p string) bool { _, err := os.Stat(p); return err == nil },
		}, nil
	}
}
