package localagentcmd

import (
	"bytes"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
)

// testApp builds a throwaway *Cmd (embedding a fresh *cmdcore.App with a
// temp-dir Paths and buffered streams) — the localagentcmd copy of the cmdcore
// white-box helper. It returns *Cmd (not *cmdcore.App) so tests can both drive
// the command constructors (passing app.App) and call the cluster's own
// unexported methods directly. XDG_CONFIG_HOME is pointed at a fresh temp dir
// so the agent state (config.LoadAgentState/MutateAgentState) is isolated too —
// without this a test would read the developer's real ~/.config/jentic.
func testApp(t *testing.T) *Cmd {
	t.Helper()
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	return &Cmd{App: &cmdcore.App{
		Paths: config.Paths{Root: t.TempDir()},
		Out:   new(bytes.Buffer),
		Err:   new(bytes.Buffer),
	}}
}
