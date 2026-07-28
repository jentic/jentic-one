package cmd

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

func TestSkillRegisteredOnAPIRoot(t *testing.T) {
	root := newAPIRootCmd(testApp(t))
	if !hasCommand(root, "skill") {
		t.Fatal("jentic root missing skill command")
	}
	if hasCommand(newCtlRootCmd(testApp(t)), "skill") {
		t.Error("jenticctl should not register skill")
	}
}

// runSkill executes the skill command tree with args in an isolated cwd+home so
// detection and writes never touch the real environment.
func runSkill(t *testing.T, args ...string) (*App, string) {
	t.Helper()
	tmp := t.TempDir()
	t.Setenv("HOME", tmp)
	t.Chdir(tmp)

	out := new(bytes.Buffer)
	app := testApp(t)
	app.Out = out
	app.Err = out

	cmd := newSkillCmd(app)
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs(args)
	if err := cmd.Execute(); err != nil {
		t.Fatalf("skill %v: %v", args, err)
	}
	return app, out.String()
}

func TestSkillInitGenericWritesManagedBlock(t *testing.T) {
	_, out := runSkill(t, "init", "--operator", "generic", "--yes")
	if !strings.Contains(out, "created") {
		t.Errorf("output missing created line: %q", out)
	}
	cwd, _ := os.Getwd()
	data, err := os.ReadFile(filepath.Join(cwd, "AGENTS.md"))
	if err != nil {
		t.Fatalf("AGENTS.md not written: %v", err)
	}
	if !strings.Contains(string(data), "BEGIN JENTIC MANAGED SKILL") {
		t.Error("managed block sentinel missing")
	}
	if !strings.Contains(string(data), "jentic register") {
		t.Error("skill body missing expected command")
	}
}

func TestSkillInitDryRunWritesNothing(t *testing.T) {
	_, out := runSkill(t, "init", "--operator", "generic", "--dry-run")
	if !strings.Contains(out, "Dry run") {
		t.Errorf("missing dry-run notice: %q", out)
	}
	cwd, _ := os.Getwd()
	if _, err := os.Stat(filepath.Join(cwd, "AGENTS.md")); !os.IsNotExist(err) {
		t.Error("dry run should not write AGENTS.md")
	}
}

func TestSkillInitUnknownOperatorErrors(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("HOME", tmp)
	t.Chdir(tmp)
	app := testApp(t)
	app.Out = new(bytes.Buffer)
	cmd := newSkillCmd(app)
	cmd.SetOut(app.Out)
	cmd.SetErr(app.Out)
	cmd.SetArgs([]string{"init", "--operator", "bogus", "--yes"})
	if err := cmd.Execute(); err == nil {
		t.Fatal("expected error for unknown operator")
	}
}

// stubDetect replaces detectEnv for one test with a deterministic environment:
// detection fires only for the named operators (via PATH lookup), never via
// the real machine's state.
func stubDetect(t *testing.T, home, cwd string, detected ...string) {
	t.Helper()
	old := detectEnv
	t.Cleanup(func() { detectEnv = old })
	byName := map[string]bool{}
	for _, d := range detected {
		byName[d] = true
	}
	detectEnv = func() (skillgen.DetectEnv, error) {
		return skillgen.DetectEnv{
			Home:   home,
			Cwd:    cwd,
			Lookup: func(name string) bool { return byName[name] },
			Stat:   func(p string) bool { _, err := os.Stat(p); return err == nil },
		}, nil
	}
}

func TestSkillInitNoOperatorNonInteractiveErrorsWhenNothingDetected(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("HOME", tmp)
	t.Chdir(tmp)
	stubDetect(t, tmp, tmp) // nothing detected
	app := testApp(t)
	app.Out = new(bytes.Buffer)
	cmd := newSkillCmd(app)
	cmd.SetOut(app.Out)
	cmd.SetErr(app.Out)
	// --yes with no --operator, nothing detected: must error rather than hang
	// or write somewhere arbitrary, and must name the flags to pass.
	cmd.SetArgs([]string{"init", "--yes"})
	err := cmd.Execute()
	if err == nil {
		t.Fatal("expected error when no operators given and none detected")
	}
	if !strings.Contains(err.Error(), "--operator") || !strings.Contains(err.Error(), "--all") {
		t.Errorf("error should point at --operator/--all: %v", err)
	}
}

func TestSkillInitNoOperatorNonInteractiveDefaultsToDetected(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	t.Setenv("HOME", home)
	t.Chdir(cwd)
	stubDetect(t, home, cwd, "claude")

	out := new(bytes.Buffer)
	app := testApp(t)
	app.Out = out
	app.Err = out
	cmd := newSkillCmd(app)
	cmd.SetOut(out)
	cmd.SetErr(out)
	// #755: --yes (or no TTY) with no --operator degrades to the detected
	// operators instead of erroring, echoing the resolved targets first.
	cmd.SetArgs([]string{"init", "--yes"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("init --yes with a detected operator should not error: %v", err)
	}
	if !strings.Contains(out.String(), "defaulting to detected operators") {
		t.Errorf("missing pre-write default echo: %q", out.String())
	}
	skill := filepath.Join(home, ".claude", "skills", "jentic", "SKILL.md")
	if _, err := os.Stat(skill); err != nil {
		t.Fatalf("skill not written to detected operator's user target: %v", err)
	}
	if _, err := os.Stat(filepath.Join(cwd, "AGENTS.md")); !os.IsNotExist(err) {
		t.Error("no AGENTS.md operator was detected; cwd must stay untouched")
	}
}

func TestSkillListJSON(t *testing.T) {
	_, out := runSkill(t, "list", "--json")
	var payload struct {
		Operators []struct {
			Operator  string `json:"operator"`
			Target    string `json:"target"`
			Installed bool   `json:"installed"`
			Installs  []struct {
				Scope     string `json:"scope"`
				Installed bool   `json:"installed"`
			} `json:"installs"`
		} `json:"operators"`
	}
	if err := json.Unmarshal([]byte(out), &payload); err != nil {
		t.Fatalf("list --json not valid JSON: %v\n%s", err, out)
	}
	if len(payload.Operators) < 5 {
		t.Errorf("expected >=5 operators, got %d", len(payload.Operators))
	}
	for _, op := range payload.Operators {
		if op.Installed {
			t.Errorf("%s reports installed in a fresh environment (#752: detected must not imply installed)", op.Operator)
		}
		if len(op.Installs) == 0 {
			t.Errorf("%s missing per-scope install states", op.Operator)
		}
	}
}

func TestSkillListJSONReportsInstalledAfterInit(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	t.Setenv("HOME", home)
	t.Chdir(cwd)
	stubDetect(t, home, cwd)

	run := func(args ...string) string {
		out := new(bytes.Buffer)
		app := testApp(t)
		app.Out = out
		app.Err = out
		cmd := newSkillCmd(app)
		cmd.SetOut(out)
		cmd.SetErr(out)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err != nil {
			t.Fatalf("skill %v: %v", args, err)
		}
		return out.String()
	}

	run("init", "--operator", "claude", "--yes")
	out := run("list", "--json")

	var payload struct {
		Operators []struct {
			Operator      string `json:"operator"`
			Installed     bool   `json:"installed"`
			InstalledPath string `json:"installed_path"`
		} `json:"operators"`
	}
	if err := json.Unmarshal([]byte(out), &payload); err != nil {
		t.Fatalf("list --json not valid JSON: %v\n%s", err, out)
	}
	for _, op := range payload.Operators {
		want := op.Operator == "claude"
		if op.Installed != want {
			t.Errorf("%s installed = %v, want %v", op.Operator, op.Installed, want)
		}
		if want && op.InstalledPath != filepath.Join(home, ".claude", "skills", "jentic", "SKILL.md") {
			t.Errorf("claude installed_path = %q", op.InstalledPath)
		}
	}
}

func TestSkillRemoveNoOperatorErrors(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("HOME", tmp)
	t.Chdir(tmp)
	app := testApp(t)
	app.Out = new(bytes.Buffer)
	cmd := newSkillCmd(app)
	cmd.SetOut(app.Out)
	cmd.SetErr(app.Out)
	cmd.SetArgs([]string{"remove"})
	if err := cmd.Execute(); err == nil {
		t.Fatal("expected error when remove has no operators")
	}
}
