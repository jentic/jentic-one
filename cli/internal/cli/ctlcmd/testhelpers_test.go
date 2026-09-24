package ctlcmd

import (
	"bytes"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
)

// testApp builds a ctl-tree receiver backed by a throwaway cmdcore.App with a
// temp-dir Paths and buffered streams, matching the pre-split white-box helper.
// XDG_CONFIG_HOME is pointed at a fresh temp dir so the agent state read by
// doctor's local-agent checks is isolated from the developer's real
// ~/.config/jentic.
func testApp(t *testing.T) *app {
	t.Helper()
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	return &app{App: &cmdcore.App{
		Paths: config.Paths{Root: t.TempDir()},
		Out:   new(bytes.Buffer),
		Err:   new(bytes.Buffer),
	}}
}
