package localagentcmd

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/charmbracelet/huh"

	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// skill_init_json_test.go pins two #830 contract points of `skill init`:
// --json selects only the output shape (a promptable human still gets the
// pickers; only machine mode skips them), and the envelope's `mcp` member
// reports the MCP registration half of the command.

// stubPicker makes stdin look promptable and replaces the interactive
// operator picker for one test, recording whether it ran.
func stubPicker(t *testing.T, pick func(*Cmd, *skillgen.Registry, skillgen.DetectEnv) ([]skillgen.Adapter, error)) *bool {
	t.Helper()
	called := new(bool)
	prevPromptable, prevPicker := promptable, operatorPicker
	promptable = func() bool { return true }
	operatorPicker = func(a *Cmd, reg *skillgen.Registry, env skillgen.DetectEnv) ([]skillgen.Adapter, error) {
		*called = true
		return pick(a, reg, env)
	}
	t.Cleanup(func() { promptable, operatorPicker = prevPromptable, prevPicker })
	return called
}

// TestSkillInitJSONKeepsPickerForHumans: under default-human mode on a
// promptable terminal, --json must not turn the run into the detected-operator
// default; the picker runs (it draws on stderr) and its selection is written.
func TestSkillInitJSONKeepsPickerForHumans(t *testing.T) {
	h := newSkillJSONHarness(t, "claude", "cursor")
	called := stubPicker(t, func(_ *Cmd, reg *skillgen.Registry, _ skillgen.DetectEnv) ([]skillgen.Adapter, error) {
		ad, _ := reg.Resolve("cursor")
		return []skillgen.Adapter{ad}, nil
	})

	stdout, stderr, err := h.exec(newHumanSkillCmd, "init", "--json", "--scope", "user", "--skill", "jentic")
	if err != nil {
		t.Fatalf("init --json: %v\nstderr: %s", err, stderr)
	}
	if !*called {
		t.Fatal("--json under human mode on a promptable terminal must still open the operator picker")
	}
	if strings.Contains(stderr, "defaulting to detected operators") {
		t.Errorf("--json must not take the detected-operator default for a promptable human:\n%s", stderr)
	}
	env := decodeSkillEnvelope(t, stdout)
	if len(env.Results) != 1 || env.Results[0].Operator != "cursor" || env.Results[0].Action != "created" {
		t.Errorf("results = %+v, want only the picked cursor install", env.Results)
	}
	if _, err := os.Stat(filepath.Join(h.home, ".claude")); !os.IsNotExist(err) {
		t.Error("a detected but unpicked operator must not be written")
	}
}

// TestSkillInitJSONPickerAbort: Esc in the picker under --json is a clean
// exit with an empty envelope on stdout and the notice on stderr.
func TestSkillInitJSONPickerAbort(t *testing.T) {
	h := newSkillJSONHarness(t, "claude")
	stubPicker(t, func(*Cmd, *skillgen.Registry, skillgen.DetectEnv) ([]skillgen.Adapter, error) {
		return nil, huh.ErrUserAborted
	})
	stdout, stderr, err := h.exec(newHumanSkillCmd, "init", "--json")
	if err != nil {
		t.Fatalf("aborted picker must not fail: %v", err)
	}
	env := decodeSkillEnvelope(t, stdout)
	if env.Results == nil || len(env.Results) != 0 || env.MCP == nil || len(env.MCP.Results) != 0 {
		t.Errorf("aborted init = %+v, want empty results and an empty mcp report", env)
	}
	if !strings.Contains(stderr, "Cancelled.") {
		t.Errorf("cancel notice should go to stderr:\n%s", stderr)
	}
}

// TestSkillInitMachineModeNeverPrompts: agent mode skips the picker even on a
// promptable terminal and takes the detected-operator default.
func TestSkillInitMachineModeNeverPrompts(t *testing.T) {
	h := newSkillJSONHarness(t, "claude")
	called := stubPicker(t, func(*Cmd, *skillgen.Registry, skillgen.DetectEnv) ([]skillgen.Adapter, error) {
		return nil, nil
	})
	stdout, stderr, err := h.exec(newMachineSkillCmd, "init", "--skill", "jentic")
	if err != nil {
		t.Fatalf("init in agent mode: %v\nstderr: %s", err, stderr)
	}
	if *called {
		t.Fatal("agent mode must never open a picker")
	}
	env := decodeSkillEnvelope(t, stdout)
	if len(env.Results) != 1 || env.Results[0].Operator != "claude" {
		t.Errorf("results = %+v, want the detected claude install", env.Results)
	}
}

// writeActiveContext writes a config.yaml with one active context under the
// XDG_CONFIG_HOME testApp isolated, so MCP registration has a name to pin.
func writeActiveContext(t *testing.T) {
	t.Helper()
	dir := filepath.Join(os.Getenv("XDG_CONFIG_HOME"), "jentic")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	cfg := "active_context: dev\ncontexts:\n  dev:\n    environment: dev\n    identity: dev\n    mode: human\n"
	if err := os.WriteFile(filepath.Join(dir, "config.yaml"), []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
}

// TestSkillInitJSONReportsMCPRegistration: the MCP config files init writes
// appear in the envelope with their path and action, and a dry run lists them
// as planned without writing.
func TestSkillInitJSONReportsMCPRegistration(t *testing.T) {
	home, cwd := t.TempDir(), t.TempDir()
	t.Setenv("HOME", home)
	t.Chdir(cwd)
	if err := os.MkdirAll(filepath.Join(home, ".cursor"), 0o755); err != nil {
		t.Fatal(err)
	}
	mcpJSON := filepath.Join(home, ".cursor", "mcp.json")

	run := func(args ...string) skillJSONEnvelope {
		t.Helper()
		app := testApp(t)
		writeActiveContext(t)
		stubDetect(t, app, home, cwd, "cursor")
		out, errOut := new(bytes.Buffer), new(bytes.Buffer)
		app.Out, app.Err = out, errOut
		cmd := newMachineSkillCmd(t, app)
		cmd.SilenceUsage, cmd.SilenceErrors = true, true
		cmd.SetOut(out)
		cmd.SetErr(errOut)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err != nil {
			t.Fatalf("skill %v: %v\nstderr: %s", args, err, errOut)
		}
		if strings.Contains(out.String(), "MCP registration") {
			t.Errorf("MCP prose leaked into JSON stdout:\n%s", out)
		}
		return decodeSkillEnvelope(t, out.String())
	}

	env := run("init", "--operator", "cursor", "--skill", "jentic", "--dry-run")
	want := skillJSONMCPResult{Runtime: "cursor", Path: mcpJSON, Action: "planned", DryRun: true}
	if env.MCP == nil || len(env.MCP.Results) != 1 || env.MCP.Results[0] != want || env.MCP.SkippedReason != "" {
		t.Fatalf("dry-run mcp = %+v, want [%+v]", env.MCP, want)
	}
	if _, err := os.Stat(mcpJSON); !os.IsNotExist(err) {
		t.Fatal("a dry run must not write the MCP config")
	}

	env = run("init", "--operator", "cursor", "--skill", "jentic")
	want = skillJSONMCPResult{Runtime: "cursor", Path: mcpJSON, Action: "created"}
	if env.MCP == nil || len(env.MCP.Results) != 1 || env.MCP.Results[0] != want {
		t.Fatalf("mcp = %+v, want [%+v]", env.MCP, want)
	}
	if _, err := os.Stat(mcpJSON); err != nil {
		t.Fatalf("init must write the cursor MCP config: %v", err)
	}

	env = run("init", "--operator", "cursor", "--skill", "jentic")
	if env.MCP == nil || len(env.MCP.Results) != 1 || env.MCP.Results[0].Action != "unchanged" {
		t.Errorf("re-run mcp = %+v, want unchanged", env.MCP)
	}
}

// TestSkillInitJSONMCPSkippedWithoutContext: with no active context to pin,
// the envelope says registration was skipped and why.
func TestSkillInitJSONMCPSkippedWithoutContext(t *testing.T) {
	h := newSkillJSONHarness(t)
	if err := os.MkdirAll(filepath.Join(h.home, ".cursor"), 0o755); err != nil {
		t.Fatal(err)
	}
	env, _ := h.mustJSON(newHumanSkillCmd, "init", "--operator", "cursor", "--skill", "jentic", "--json")
	if env.MCP == nil || len(env.MCP.Results) != 0 || env.MCP.SkippedReason != "no_active_context" {
		t.Errorf("mcp = %+v, want empty results with skipped_reason no_active_context", env.MCP)
	}
}
