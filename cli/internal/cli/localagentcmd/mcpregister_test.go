package localagentcmd

// Wiring tests for the MCP registration step (2-E3): the operator→runtime
// fan-out, presence filtering, and the setup/skill-init entry points. The
// writers themselves are golden-tested in internal/mcpcfg; here we test the
// glue that decides WHICH runtimes get an entry.

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/mcpcfg"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// fakeMcpEnv is an Env where only the named artifacts exist.
func fakeMcpEnv(home, goos string, paths []string, bins []string) mcpcfg.Env {
	pathSet := map[string]bool{}
	for _, p := range paths {
		pathSet[p] = true
	}
	binSet := map[string]bool{}
	for _, b := range bins {
		binSet[b] = true
	}
	return mcpcfg.Env{
		Home: home,
		GOOS: goos,
		Stat: func(p string) bool { return pathSet[p] },
		LookPath: func(name string) (string, error) {
			if binSet[name] {
				return "/usr/local/bin/" + name, nil
			}
			return "", exec.ErrNotFound
		},
	}
}

func targetsFor(t *testing.T, names ...string) []skillTarget {
	t.Helper()
	reg := skillgen.DefaultRegistry()
	out := make([]skillTarget, 0, len(names))
	for _, n := range names {
		ad, ok := reg.Resolve(n)
		if !ok {
			t.Fatalf("adapter %q not in registry", n)
		}
		out = append(out, skillTarget{adapter: ad, scope: ad.DefaultScope()})
	}
	return out
}

func TestMcpRuntimesForFansClaudeOutToCodeAndDesktop(t *testing.T) {
	home := t.TempDir()
	env := fakeMcpEnv(home, "darwin", []string{
		filepath.Join(home, "Library", "Application Support", "Claude"),
	}, []string{"claude"})
	got := mcpRuntimesFor(targetsFor(t, "claude"), env)
	want := []mcpcfg.Runtime{mcpcfg.RuntimeClaudeCode, mcpcfg.RuntimeClaudeDesktop}
	if len(got) != len(want) {
		t.Fatalf("runtimes = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("runtimes = %v, want %v", got, want)
		}
	}
}

func TestMcpRuntimesForFiltersUndetectedRuntimes(t *testing.T) {
	home := t.TempDir()
	// Cursor's dotdir exists; claude binary and desktop dir do not; no codex.
	env := fakeMcpEnv(home, "linux", []string{filepath.Join(home, ".cursor")}, nil)
	got := mcpRuntimesFor(targetsFor(t, "cursor", "claude", "codex"), env)
	if len(got) != 1 || got[0] != mcpcfg.RuntimeCursor {
		t.Fatalf("runtimes = %v, want [cursor]", got)
	}
}

func TestMcpRuntimesForSkipsOperatorsWithoutMcpConfig(t *testing.T) {
	home := t.TempDir()
	env := fakeMcpEnv(home, "linux", []string{filepath.Join(home, ".cursor")}, []string{"claude", "codex"})
	// generic + hermes have no MCP config surface: nothing to register even
	// though the skill targets them.
	got := mcpRuntimesFor(targetsFor(t, "generic"), env)
	if len(got) != 0 {
		t.Fatalf("runtimes = %v, want none for generic", got)
	}
}

func TestRegisterMCPEntriesSkipsQuietlyWithoutActiveContext(t *testing.T) {
	tmp := t.TempDir()
	if err := os.MkdirAll(filepath.Join(tmp, ".cursor"), 0o755); err != nil {
		t.Fatal(err)
	}
	app := testApp(t) // fresh XDG_CONFIG_HOME → no config.yaml → no active context
	stubDetect(t, app, tmp, tmp)

	got := app.registerMCPEntries(t.Context(), targetsFor(t, "cursor"), false)
	if got != nil {
		t.Fatalf("outcomes = %v, want nil without an active context", got)
	}
	if _, err := os.Stat(filepath.Join(tmp, ".cursor", "mcp.json")); !os.IsNotExist(err) {
		t.Fatalf("mcp.json stat err = %v, want not-exist (nothing must be written without a context to pin)", err)
	}
}

func TestMcpEnvHonorsInjectedLookup(t *testing.T) {
	tmp := t.TempDir()
	app := testApp(t)
	stubDetect(t, app, tmp, tmp) // stub Lookup reports nothing on PATH

	env, err := app.mcpEnv()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := env.LookPath("claude"); !errors.Is(err, exec.ErrNotFound) {
		t.Fatalf("LookPath through a stubbed detectEnv = %v, want ErrNotFound", err)
	}
}
