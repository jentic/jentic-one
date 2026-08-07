package cmdcore

import (
	"bytes"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/profile"
)

// testApp builds a throwaway App with a temp-dir Paths and buffered streams,
// matching the pre-split white-box helper.
func testApp(t *testing.T) *App {
	t.Helper()
	return &App{
		Paths: config.Paths{Root: t.TempDir()},
		Out:   new(bytes.Buffer),
		Err:   new(bytes.Buffer),
	}
}

// seedProfile writes a profile (optionally with agent metadata) under the App's
// Paths — a shared white-box helper for the identity/profile tests.
func seedProfile(t *testing.T, app *App, name, agentID string) {
	t.Helper()
	p, err := profile.Open(app.Paths, name)
	if err != nil {
		t.Fatalf("open profile %q: %v", name, err)
	}
	if agentID != "" {
		if err := p.SaveMeta(&profile.Meta{AgentID: agentID, BaseURL: "http://example:9000"}); err != nil {
			t.Fatalf("save meta %q: %v", name, err)
		}
	}
}
