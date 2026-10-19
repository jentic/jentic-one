package ctlcmd

import (
	"fmt"
	"os"
	"testing"
)

// TestMain points HOME, USERPROFILE, and the XDG roots at a throwaway dir and
// runs from a throwaway cwd, so a test that builds an app without a sandboxed
// DetectEnv still cannot probe or remove the developer's real ~/.claude,
// ~/.cursor, ~/.codex, or a project AGENTS.md.
func TestMain(m *testing.M) {
	os.Exit(runSandboxed(m))
}

func runSandboxed(m *testing.M) int {
	root, err := os.MkdirTemp("", "ctlcmd-test-home-*")
	if err != nil {
		fmt.Fprintln(os.Stderr, "create sandbox home:", err)
		return 1
	}
	defer func() { _ = os.RemoveAll(root) }()
	for _, k := range []string{"HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"} {
		if err := os.Setenv(k, root); err != nil {
			fmt.Fprintln(os.Stderr, "sandbox", k, err)
			return 1
		}
	}
	if err := os.Chdir(root); err != nil {
		fmt.Fprintln(os.Stderr, "chdir sandbox:", err)
		return 1
	}
	return m.Run()
}
