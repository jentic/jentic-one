package api

import (
	"bytes"
	"os"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// testApp builds an api-tree receiver backed by a throwaway cmdcore.App with a
// temp-dir Paths and buffered streams, matching the pre-split white-box helper.
func testApp(t *testing.T) *app {
	t.Helper()
	return &app{App: &cmdcore.App{
		Paths: config.Paths{Root: t.TempDir()},
		Out:   new(bytes.Buffer),
		Err:   new(bytes.Buffer),
	}}
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
