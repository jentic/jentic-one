package ctlcmd

import (
	"bytes"
	"os"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
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
		Paths:     config.Paths{Root: t.TempDir()},
		Out:       new(bytes.Buffer),
		Err:       new(bytes.Buffer),
		DetectEnv: sandboxSkillEnv(t.TempDir(), t.TempDir()),
	}}
}

// sandboxSkillEnv returns a skill detection probe rooted at home/cwd, so any
// command that inspects or removes installed agent skills (uninstall, update)
// never touches the developer's real ~/.claude, ~/.cursor, or AGENTS.md.
func sandboxSkillEnv(home, cwd string) func() (skillgen.DetectEnv, error) {
	return func() (skillgen.DetectEnv, error) {
		return skillgen.DetectEnv{
			Home:   home,
			Cwd:    cwd,
			Lookup: func(string) bool { return false },
			Stat:   func(p string) bool { _, err := os.Stat(p); return err == nil },
		}, nil
	}
}
