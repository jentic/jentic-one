package localagentcmd

import (
	"bytes"
	"testing"

	"github.com/spf13/cobra"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
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

// newHumanSkillCmd builds the skill command tree pinned to an EXPLICIT human
// mode. `go test` stdout is never a TTY, so without the pin the mutating verbs
// would auto-select their JSON envelope (#830) and the human-report assertions
// would see JSON. JSON-shape tests use --json or newMachineSkillCmd instead.
func newHumanSkillCmd(t *testing.T, app *Cmd) *cobra.Command {
	t.Helper()
	cmd := NewSkillCmd(app.App)
	cmd.SetContext(clictx.WithActiveState(t.Context(), &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{},
		Mode:          clictx.ModeHuman,
		ModeExplicit:  true,
	}))
	return cmd
}

// newMachineSkillCmd builds the skill command tree under agent mode, the
// machine rung that forces JSON regardless of --json or the TTY.
func newMachineSkillCmd(t *testing.T, app *Cmd) *cobra.Command {
	t.Helper()
	cmd := NewSkillCmd(app.App)
	cmd.SetContext(clictx.WithActiveState(t.Context(), &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{},
		Mode:          clictx.ModeAgent,
		ModeExplicit:  true,
	}))
	return cmd
}
