package localagentcmd

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// runSkill executes the skill command tree with args in an isolated cwd+home so
// detection and writes never touch the real environment.
func runSkill(t *testing.T, args ...string) (*Cmd, string) {
	t.Helper()
	tmp := t.TempDir()
	t.Setenv("HOME", tmp)
	t.Chdir(tmp)

	out := new(bytes.Buffer)
	app := testApp(t)
	app.Out = out
	app.Err = out

	cmd := NewSkillCmd(app.App)
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs(args)
	if err := cmd.Execute(); err != nil {
		t.Fatalf("skill %v: %v", args, err)
	}
	return app, out.String()
}

// TestBareSkillShowsHelpAndWritesNothing pins UX-20: bare `jentic skill` must
// behave like every other group parent — print help, mutate NOTHING — instead
// of the old alias to `skill init`, which non-interactively wrote SKILL.md into
// every detected operator home with no confirmation. Writes now live only under
// the explicit `skill init` subcommand.
func TestBareSkillShowsHelpAndWritesNothing(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("HOME", tmp)
	t.Chdir(tmp)

	// Pre-create a detectable operator home so the OLD behavior would have
	// written into it — proving the new behavior does not.
	if err := os.MkdirAll(filepath.Join(tmp, ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}

	out := new(bytes.Buffer)
	app := testApp(t)
	app.Out = out
	app.Err = out
	cmd := NewSkillCmd(app.App)
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs(nil) // bare `jentic skill`
	if err := cmd.Execute(); err != nil {
		t.Fatalf("bare skill should succeed (show help): %v", err)
	}

	// Help, not a write: the usage/subcommand listing appears, and no operator
	// home gained a SKILL.md / AGENTS.md.
	if !strings.Contains(out.String(), "init") || !strings.Contains(strings.ToLower(out.String()), "usage") {
		t.Errorf("bare skill did not render help (expected USAGE + init subcommand):\n%s", out.String())
	}
	for _, p := range []string{
		filepath.Join(tmp, ".claude", "SKILL.md"),
		filepath.Join(tmp, "AGENTS.md"),
	} {
		if _, err := os.Stat(p); !os.IsNotExist(err) {
			t.Errorf("bare skill must not write %s (UX-20 footgun)", p)
		}
	}
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
	if !strings.Contains(string(data), "BEGIN JENTIC MANAGED SKILL: jentic") {
		t.Error("named managed block sentinel missing")
	}
	if !strings.Contains(string(data), "See the full skill: GET") {
		t.Error("AGENTS.md should carry a pointer link to the full skill")
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
	cmd := NewSkillCmd(app.App)
	cmd.SetOut(app.Out)
	cmd.SetErr(app.Out)
	cmd.SetArgs([]string{"init", "--operator", "bogus", "--yes"})
	if err := cmd.Execute(); err == nil {
		t.Fatal("expected error for unknown operator")
	}
}

// stubDetect injects a deterministic detection environment into one test's
// App: detection fires only for the named operators (via PATH lookup), never
// via the real machine's state.
func stubDetect(t *testing.T, app *Cmd, home, cwd string, detected ...string) {
	t.Helper()
	byName := map[string]bool{}
	for _, d := range detected {
		byName[d] = true
	}
	app.DetectEnv = func() (skillgen.DetectEnv, error) {
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
	app := testApp(t)
	stubDetect(t, app, tmp, tmp) // nothing detected
	app.Out = new(bytes.Buffer)
	cmd := NewSkillCmd(app.App)
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

	out := new(bytes.Buffer)
	app := testApp(t)
	stubDetect(t, app, home, cwd, "claude")
	app.Out = out
	app.Err = out
	cmd := NewSkillCmd(app.App)
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

	run := func(args ...string) string {
		out := new(bytes.Buffer)
		app := testApp(t)
		stubDetect(t, app, home, cwd)
		app.Out = out
		app.Err = out
		cmd := NewSkillCmd(app.App)
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
	// Guard against a vacuous pass: the assertions below run per operator, so
	// an empty (or claude-less) payload must fail loudly, not silently.
	if len(payload.Operators) < 5 {
		t.Fatalf("expected >=5 operators, got %d", len(payload.Operators))
	}
	var sawClaude bool
	for _, op := range payload.Operators {
		want := op.Operator == "claude"
		sawClaude = sawClaude || want
		if op.Installed != want {
			t.Errorf("%s installed = %v, want %v", op.Operator, op.Installed, want)
		}
		// init installs the full skill set, so installed_path points at one of
		// the claude skill dirs (whichever sorts first) — assert the shape, not
		// a specific skill.
		if want {
			prefix := filepath.Join(home, ".claude", "skills") + string(filepath.Separator)
			if !strings.HasPrefix(op.InstalledPath, prefix) || !strings.HasSuffix(op.InstalledPath, "SKILL.md") {
				t.Errorf("claude installed_path = %q, want a %s<skill>/SKILL.md path", op.InstalledPath, prefix)
			}
		}
	}
	if !sawClaude {
		t.Error("claude missing from list output")
	}
}

// TestSkillInitNonInteractiveDefaultsToDetectedProjectScope covers the #755
// fallback for a project-scoped operator: a detected codex must default into
// the *project* AGENTS.md (the #552-ratified default), not the user scope.
func TestSkillInitNonInteractiveDefaultsToDetectedProjectScope(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	t.Setenv("HOME", home)
	t.Chdir(cwd)

	out := new(bytes.Buffer)
	app := testApp(t)
	stubDetect(t, app, home, cwd, "codex")
	app.Out = out
	app.Err = out
	cmd := NewSkillCmd(app.App)
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs([]string{"init", "--yes"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("init --yes with detected codex: %v", err)
	}
	if _, err := os.Stat(filepath.Join(cwd, "AGENTS.md")); err != nil {
		t.Fatalf("codex default is project scope; AGENTS.md not in cwd: %v", err)
	}
	if _, err := os.Stat(filepath.Join(home, ".codex", "AGENTS.md")); !os.IsNotExist(err) {
		t.Error("user-scope codex AGENTS.md written despite project default")
	}
	if strings.Contains(out.String(), "git status") {
		t.Errorf("no git repo here; the repo-pollution warning must not fire:\n%s", out.String())
	}
}

// TestSkillInitDefaultedProjectScopeWarnsInsideGitRepo pins the repo-pollution
// guard: a *defaulted* (nobody explicitly asked) project-scope write into a
// git worktree must carry a real warning, since the new AGENTS.md will show up
// in git status and could get swept into someone's next commit.
func TestSkillInitDefaultedProjectScopeWarnsInsideGitRepo(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	if err := os.Mkdir(filepath.Join(cwd, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	t.Chdir(cwd)

	out := new(bytes.Buffer)
	app := testApp(t)
	stubDetect(t, app, home, cwd, "codex")
	app.Out = out
	app.Err = out
	cmd := NewSkillCmd(app.App)
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs([]string{"init", "--yes"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("init --yes with detected codex: %v", err)
	}
	if !strings.Contains(out.String(), "git status") || !strings.Contains(out.String(), "--scope user") {
		t.Errorf("defaulted project-scope write inside a git repo must warn and name --scope user:\n%s", out.String())
	}
	if _, err := os.Stat(filepath.Join(cwd, "AGENTS.md")); err != nil {
		t.Fatalf("the write itself must still happen (warning, not refusal): %v", err)
	}
}

// TestSkillListPrettyShowsEveryInstall pins the human listing: detection and
// install state are separate lines (#752), and *both* coexisting installs of
// one operator are shown — hiding the second would lie by omission.
func TestSkillListPrettyShowsEveryInstall(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	env := skillgen.DetectEnv{
		Home:   home,
		Cwd:    cwd,
		Lookup: func(name string) bool { return name == "claude" },
		Stat:   func(p string) bool { _, err := os.Stat(p); return err == nil },
	}
	reg := skillgen.DefaultRegistry()
	ad, _ := reg.Resolve("claude")
	content, err := skillgen.Bundled("jentic", "http://example.test")
	if err != nil {
		t.Fatal(err)
	}
	for _, scope := range []skillgen.Scope{skillgen.ScopeUser, skillgen.ScopeProject} {
		if _, err := skillgen.Apply(ad, content, env, skillgen.ApplyOptions{Scope: scope}); err != nil {
			t.Fatal(err)
		}
	}

	out := new(bytes.Buffer)
	app := testApp(t)
	app.Out = out
	detected := map[skillgen.Operator]bool{ad.Operator(): true}
	if err := app.skillListPretty(reg, env, detected); err != nil {
		t.Fatal(err)
	}
	got := out.String()
	if !strings.Contains(got, "(detected)") {
		t.Errorf("detected tag missing:\n%s", got)
	}
	if !strings.Contains(got, "(user scope)") || !strings.Contains(got, "(project scope)") {
		t.Errorf("both coexisting installs must be listed:\n%s", got)
	}
	if !strings.Contains(got, "installed: no") {
		t.Errorf("uninstalled operators must say so:\n%s", got)
	}
}

// TestSkillRemoveFindsNonDefaultScope proves `skill remove` without --scope
// removes the install where it actually is: a --scope project install of a
// user-default operator must still be found and stripped.
func TestSkillRemoveFindsNonDefaultScope(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	t.Setenv("HOME", home)
	t.Chdir(cwd)

	run := func(args ...string) string {
		out := new(bytes.Buffer)
		app := testApp(t)
		stubDetect(t, app, home, cwd)
		app.Out = out
		app.Err = out
		cmd := NewSkillCmd(app.App)
		cmd.SetOut(out)
		cmd.SetErr(out)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err != nil {
			t.Fatalf("skill %v: %v", args, err)
		}
		return out.String()
	}

	run("init", "--operator", "cursor", "--scope", "project", "--yes")
	projSkill := filepath.Join(cwd, ".cursor", "skills", "jentic", "SKILL.md")
	if _, err := os.Stat(projSkill); err != nil {
		t.Fatalf("project-scoped install missing: %v", err)
	}

	out := run("remove", "--operator", "cursor")
	if !strings.Contains(out, "removed from") {
		t.Errorf("expected a removal, got:\n%s", out)
	}
	if _, err := os.Stat(projSkill); !os.IsNotExist(err) {
		t.Error("project-scoped skill still present after remove without --scope")
	}
}

func TestSkillRemoveNoOperatorErrors(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("HOME", tmp)
	t.Chdir(tmp)
	app := testApp(t)
	app.Out = new(bytes.Buffer)
	cmd := NewSkillCmd(app.App)
	cmd.SetOut(app.Out)
	cmd.SetErr(app.Out)
	cmd.SetArgs([]string{"remove"})
	if err := cmd.Execute(); err == nil {
		t.Fatal("expected error when remove has no operators")
	}
}

// TestSkillInitInstallsFullSet proves init writes every shipped skill into an
// owned-file operator (one SKILL.md per skill).
func TestSkillInitInstallsFullSet(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	t.Setenv("HOME", home)
	t.Chdir(cwd)

	out := new(bytes.Buffer)
	app := testApp(t)
	stubDetect(t, app, home, cwd)
	app.Out, app.Err = out, out
	cmd := NewSkillCmd(app.App)
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs([]string{"init", "--operator", "claude", "--yes"})
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	for _, name := range skillgen.BundledNames() {
		p := filepath.Join(home, ".claude", "skills", name, "SKILL.md")
		if _, err := os.Stat(p); err != nil {
			t.Errorf("skill %s not installed at %s: %v", name, p, err)
		}
	}
}

// TestSkillInitSkillFilter proves --skill limits the set.
func TestSkillInitSkillFilter(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	t.Setenv("HOME", home)
	t.Chdir(cwd)

	out := new(bytes.Buffer)
	app := testApp(t)
	stubDetect(t, app, home, cwd)
	app.Out, app.Err = out, out
	cmd := NewSkillCmd(app.App)
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs([]string{"init", "--operator", "claude", "--skill", "jentic", "--yes"})
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(home, ".claude", "skills", "jentic", "SKILL.md")); err != nil {
		t.Errorf("jentic should be installed: %v", err)
	}
	if _, err := os.Stat(filepath.Join(home, ".claude", "skills", "import-new-api", "SKILL.md")); !os.IsNotExist(err) {
		t.Error("import-new-api must NOT be installed when --skill jentic is given")
	}
}

// TestSkillInitUnknownSkillErrors proves --skill is validated against the set.
func TestSkillInitUnknownSkillErrors(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("HOME", tmp)
	t.Chdir(tmp)
	app := testApp(t)
	app.Out = new(bytes.Buffer)
	cmd := NewSkillCmd(app.App)
	cmd.SetOut(app.Out)
	cmd.SetErr(app.Out)
	cmd.SetArgs([]string{"init", "--operator", "claude", "--skill", "bogus-skill", "--yes"})
	err := cmd.Execute()
	if err == nil {
		t.Fatal("expected error for unknown skill")
	}
	if !strings.Contains(err.Error(), "bogus-skill") {
		t.Errorf("error should name the unknown skill: %v", err)
	}
}

// TestSkillUpdateRewritesOnBaseURLChange proves `skill update` re-renders
// installed skills with a new base URL and rewrites when the hash differs.
func TestSkillUpdateRewritesOnBaseURLChange(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	t.Setenv("HOME", home)
	t.Chdir(cwd)

	run := func(args ...string) string {
		out := new(bytes.Buffer)
		app := testApp(t)
		stubDetect(t, app, home, cwd)
		app.Out, app.Err = out, out
		cmd := NewSkillCmd(app.App)
		cmd.SetOut(out)
		cmd.SetErr(out)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err != nil {
			t.Fatalf("skill %v: %v", args, err)
		}
		return out.String()
	}

	run("init", "--operator", "claude", "--skill", "jentic", "--base-url", "http://one.test", "--yes")
	skill := filepath.Join(home, ".claude", "skills", "jentic", "SKILL.md")
	before, _ := os.ReadFile(skill)
	if !strings.Contains(string(before), "http://one.test") {
		t.Fatalf("initial base URL not rendered:\n%s", before)
	}

	out := run("update", "--operator", "claude", "--skill", "jentic", "--base-url", "http://two.test")
	if !strings.Contains(out, "updated") {
		t.Errorf("update should report a rewrite:\n%s", out)
	}
	after, _ := os.ReadFile(skill)
	if !strings.Contains(string(after), "http://two.test") {
		t.Errorf("update did not re-render with the new base URL:\n%s", after)
	}

	// A second update with the same URL is a no-op.
	out = run("update", "--operator", "claude", "--skill", "jentic", "--base-url", "http://two.test")
	if strings.Contains(out, "updated") {
		t.Errorf("idempotent update should not report a rewrite:\n%s", out)
	}
}

// TestSkillListJSONPerSkillRows proves the list JSON now carries a per-skill
// dimension on each install row.
func TestSkillListJSONPerSkillRows(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	t.Setenv("HOME", home)
	t.Chdir(cwd)

	run := func(args ...string) string {
		out := new(bytes.Buffer)
		app := testApp(t)
		stubDetect(t, app, home, cwd)
		app.Out, app.Err = out, out
		cmd := NewSkillCmd(app.App)
		cmd.SetOut(out)
		cmd.SetErr(out)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err != nil {
			t.Fatalf("skill %v: %v", args, err)
		}
		return out.String()
	}

	run("init", "--operator", "claude", "--skill", "jentic", "--yes")
	out := run("list", "--json")

	var payload struct {
		Operators []struct {
			Operator string `json:"operator"`
			Installs []struct {
				Skill     string `json:"skill"`
				Installed bool   `json:"installed"`
			} `json:"installs"`
		} `json:"operators"`
	}
	if err := json.Unmarshal([]byte(out), &payload); err != nil {
		t.Fatalf("list --json invalid: %v\n%s", err, out)
	}
	var jenticInstalled, sawOtherSkill bool
	for _, op := range payload.Operators {
		if op.Operator != "claude" {
			continue
		}
		for _, in := range op.Installs {
			if in.Skill == "" {
				t.Error("install row missing per-skill name")
			}
			if in.Skill == "jentic" && in.Installed {
				jenticInstalled = true
			}
			if in.Skill != "jentic" {
				sawOtherSkill = true
				if in.Installed {
					t.Errorf("%s should not be installed", in.Skill)
				}
			}
		}
	}
	if !jenticInstalled {
		t.Error("claude/jentic should report installed")
	}
	if !sawOtherSkill {
		t.Error("list should enumerate every shipped skill per operator")
	}
}

// TestSkillRemoveOneSkillKeepsSiblingInAgents proves per-skill remove on a
// shared AGENTS.md leaves the sibling block intact.
func TestSkillRemoveOneSkillKeepsSiblingInAgents(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	t.Setenv("HOME", home)
	t.Chdir(cwd)

	run := func(args ...string) string {
		out := new(bytes.Buffer)
		app := testApp(t)
		stubDetect(t, app, home, cwd)
		app.Out, app.Err = out, out
		cmd := NewSkillCmd(app.App)
		cmd.SetOut(out)
		cmd.SetErr(out)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err != nil {
			t.Fatalf("skill %v: %v", args, err)
		}
		return out.String()
	}

	run("init", "--operator", "generic", "--yes")
	agents := filepath.Join(cwd, "AGENTS.md")
	data, _ := os.ReadFile(agents)
	if !strings.Contains(string(data), "BEGIN JENTIC MANAGED SKILL: jentic") ||
		!strings.Contains(string(data), "BEGIN JENTIC MANAGED SKILL: import-new-api") {
		t.Fatalf("expected all skill blocks in AGENTS.md:\n%s", data)
	}

	run("remove", "--operator", "generic", "--skill", "import-new-api")
	data, _ = os.ReadFile(agents)
	if strings.Contains(string(data), "BEGIN JENTIC MANAGED SKILL: import-new-api") {
		t.Error("removed skill block should be gone")
	}
	if !strings.Contains(string(data), "BEGIN JENTIC MANAGED SKILL: jentic") {
		t.Error("sibling jentic block must survive")
	}
}
