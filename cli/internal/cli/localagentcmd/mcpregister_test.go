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
	"runtime"
	"strings"
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

// --- Claude Code exec route (fake claude binary) -----------------------------

// fakeClaude writes an executable shell script standing in for the claude CLI
// and returns an Env resolving "claude" to it. The script logs every
// invocation to logPath; `mcp get` prints $FAKE_CLAUDE_GET (exit 1 when
// unset: entry absent), `mcp add` fails with stderr when $FAKE_CLAUDE_ADD_FAIL
// is set.
func fakeClaude(t *testing.T, logPath string) mcpcfg.Env {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("fake claude is a shell script")
	}
	script := filepath.Join(t.TempDir(), "claude")
	body := `#!/bin/sh
echo "$@" >> "` + logPath + `"
case "$1 $2" in
"mcp get")
  [ -n "$FAKE_CLAUDE_GET" ] || exit 1
  printf '%s\n' "$FAKE_CLAUDE_GET"
  exit 0 ;;
"mcp add")
  if [ -n "$FAKE_CLAUDE_ADD_FAIL" ]; then echo "user scope rejected by policy" >&2; exit 1; fi
  exit 0 ;;
esac
exit 0
`
	if err := os.WriteFile(script, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
	return mcpcfg.Env{LookPath: func(string) (string, error) { return script, nil }}
}

func claudeLog(t *testing.T, logPath string) string {
	t.Helper()
	data, _ := os.ReadFile(logPath)
	return string(data)
}

// TestRunClaudeCodeStepsConvergentIsNoOp: when `claude mcp get` already shows
// our exact entry, nothing is rewritten and the outcome reports "already up
// to date" (Changed=false) — re-runs must not claim "updated".
func TestRunClaudeCodeStepsConvergentIsNoOp(t *testing.T) {
	logPath := filepath.Join(t.TempDir(), "log")
	env := fakeClaude(t, logPath)
	entry := mcpcfg.PlainEntry("/abs/jentic", "claude-code")
	t.Setenv("FAKE_CLAUDE_GET", "Command: /abs/jentic\nArgs: mcp --context claude-code")

	app := testApp(t)
	out, err := app.runClaudeCodeSteps(t.Context(), env, entry)
	if err != nil {
		t.Fatal(err)
	}
	if out.Changed {
		t.Error("convergent entry must be a no-op (Changed=false)")
	}
	log := claudeLog(t, logPath)
	if !strings.Contains(log, "mcp get jentic") {
		t.Errorf("probe not run:\n%s", log)
	}
	if strings.Contains(log, "mcp add") || strings.Contains(log, "mcp remove") {
		t.Errorf("no-op path must not rewrite:\n%s", log)
	}
}

// TestRunClaudeCodeStepsFirstRunAddsWithoutRemove: an absent entry (probe
// fails) goes straight to add — no destructive remove window on a first run.
func TestRunClaudeCodeStepsFirstRunAddsWithoutRemove(t *testing.T) {
	logPath := filepath.Join(t.TempDir(), "log")
	env := fakeClaude(t, logPath)

	app := testApp(t)
	out, err := app.runClaudeCodeSteps(t.Context(), env, mcpcfg.PlainEntry("/abs/jentic", "claude-code"))
	if err != nil {
		t.Fatal(err)
	}
	if !out.Changed {
		t.Error("first run must report Changed")
	}
	log := claudeLog(t, logPath)
	if !strings.Contains(log, "mcp add --scope user jentic") {
		t.Errorf("add not run:\n%s", log)
	}
	if strings.Contains(log, "mcp remove") {
		t.Errorf("first run must not remove:\n%s", log)
	}
}

// TestRunClaudeCodeStepsStaleEntryRemovesThenAdds: a probe that shows a
// DIFFERENT entry triggers the remove-then-add rewrite.
func TestRunClaudeCodeStepsStaleEntryRemovesThenAdds(t *testing.T) {
	logPath := filepath.Join(t.TempDir(), "log")
	env := fakeClaude(t, logPath)
	t.Setenv("FAKE_CLAUDE_GET", "Command: /old/jentic\nArgs: mcp --context stale")

	app := testApp(t)
	out, err := app.runClaudeCodeSteps(t.Context(), env, mcpcfg.PlainEntry("/abs/jentic", "claude-code"))
	if err != nil {
		t.Fatal(err)
	}
	if !out.Changed {
		t.Error("stale entry must report Changed")
	}
	log := claudeLog(t, logPath)
	removeIdx, addIdx := strings.Index(log, "mcp remove"), strings.Index(log, "mcp add")
	if removeIdx < 0 || addIdx < 0 || removeIdx > addIdx {
		t.Errorf("want remove then add:\n%s", log)
	}
}

// TestRunClaudeCodeStepsSurfacesStderr: a failing add returns claude's own
// stderr in the error, not a bare exit status.
func TestRunClaudeCodeStepsSurfacesStderr(t *testing.T) {
	logPath := filepath.Join(t.TempDir(), "log")
	env := fakeClaude(t, logPath)
	t.Setenv("FAKE_CLAUDE_ADD_FAIL", "1")

	app := testApp(t)
	_, err := app.runClaudeCodeSteps(t.Context(), env, mcpcfg.PlainEntry("/abs/jentic", "claude-code"))
	if err == nil || !strings.Contains(err.Error(), "user scope rejected by policy") {
		t.Fatalf("error must carry claude's stderr, got: %v", err)
	}
}
