package ctlcmd

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/localagentcmd"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// skillUninstallApp builds a ctl receiver with a populated local install under
// a temp ~/.jentic and a sandboxed skill environment rooted at home/cwd.
func skillUninstallApp(t *testing.T) (a *app, out *bytes.Buffer, home, cwd string) {
	t.Helper()
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	home, cwd = t.TempDir(), t.TempDir()
	out = new(bytes.Buffer)
	a = &app{App: &cmdcore.App{
		Paths:     config.Paths{Root: t.TempDir()},
		Out:       out,
		Err:       out,
		DetectEnv: sandboxSkillEnv(home, cwd),
	}}
	writeFile(t, a.Paths.InstallConfigPath(), "mode: local\n")
	writeFile(t, filepath.Join(a.Paths.Dir(), "venv", "marker"), "x")
	return a, out, home, cwd
}

// installSkill writes the bundled jentic skill for one operator at its default
// scope, through the same engine `jentic skill init` uses, and returns its path.
func installSkill(t *testing.T, home, cwd, operator string) string {
	t.Helper()
	env, err := sandboxSkillEnv(home, cwd)()
	if err != nil {
		t.Fatal(err)
	}
	ad, ok := skillgen.DefaultRegistry().Resolve(operator)
	if !ok {
		t.Fatalf("unknown operator %q", operator)
	}
	c, err := skillgen.Bundled("jentic", "http://example.test")
	if err != nil {
		t.Fatal(err)
	}
	o, err := skillgen.Apply(ad, c, env, skillgen.ApplyOptions{Scope: ad.DefaultScope()})
	if err != nil {
		t.Fatalf("install %s skill: %v", operator, err)
	}
	return o.Path
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// By default uninstall must not touch installed skills (files it doesn't own),
// but must list what remains and name the exact removal command.
func TestUninstall_DefaultListsRemainingSkills(t *testing.T) {
	a, out, home, cwd := skillUninstallApp(t)
	claude := installSkill(t, home, cwd, "claude")
	agents := installSkill(t, home, cwd, "codex")

	if err := a.uninstallE(&uninstallOptions{yes: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}
	if !exists(claude) || !exists(agents) {
		t.Fatal("default uninstall must leave installed skills in place")
	}
	s := out.String()
	for _, want := range []string{"still installed", "claude/jentic", "codex/jentic", "jentic skill remove --all"} {
		if !strings.Contains(s, want) {
			t.Errorf("output missing %q:\n%s", want, s)
		}
	}
	// codex and generic share the project AGENTS.md: list that block once.
	if strings.Contains(s, "generic/jentic") {
		t.Errorf("shared AGENTS.md block listed twice:\n%s", s)
	}
}

// With nothing installed, the skills hint stays quiet.
func TestUninstall_NoSkillsNoHint(t *testing.T) {
	a, out, _, _ := skillUninstallApp(t)
	if err := a.uninstallE(&uninstallOptions{yes: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}
	if s := out.String(); strings.Contains(s, "still installed") || strings.Contains(s, "skill remove") {
		t.Errorf("no skills installed, but uninstall printed a skills hint:\n%s", s)
	}
}

// --purge removes unmodified skill installs through the `skill remove --all`
// path, keeps (and reports) manually edited ones, and still completes the
// uninstall.
func TestUninstall_PurgeRemovesSkillsButKeepsEdited(t *testing.T) {
	a, out, home, cwd := skillUninstallApp(t)
	claude := installSkill(t, home, cwd, "claude")
	cursor := installSkill(t, home, cwd, "cursor")
	agents := installSkill(t, home, cwd, "codex")
	if err := os.WriteFile(cursor, []byte("my own notes\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := a.uninstallE(&uninstallOptions{yes: true, purge: true}); err != nil {
		t.Fatalf("uninstall --purge: %v", err)
	}
	if exists(claude) {
		t.Error("--purge must remove the unmodified claude skill")
	}
	if data, err := os.ReadFile(agents); err == nil && strings.Contains(string(data), "jentic") {
		t.Errorf("--purge must strip the managed AGENTS.md block, got:\n%s", data)
	}
	if !exists(cursor) {
		t.Fatal("--purge must never delete a manually edited skill")
	}
	s := out.String()
	for _, want := range []string{"manual edits detected", "cursor/jentic", "(manually edited)", "jentic skill remove --all --force"} {
		if !strings.Contains(s, want) {
			t.Errorf("output missing %q:\n%s", want, s)
		}
	}
	if exists(filepath.Join(a.Paths.Dir(), "venv")) {
		t.Error("the filesystem teardown must still run")
	}
}

// --remove-skills removes skills without --purge's data semantics.
func TestUninstall_RemoveSkillsFlag(t *testing.T) {
	a, out, home, cwd := skillUninstallApp(t)
	claude := installSkill(t, home, cwd, "claude")

	if err := a.uninstallE(&uninstallOptions{yes: true, keepData: true, removeSkills: true}); err != nil {
		t.Fatalf("uninstall --remove-skills: %v", err)
	}
	if exists(claude) {
		t.Error("--remove-skills must remove the claude skill")
	}
	if s := out.String(); strings.Contains(s, "still installed") {
		t.Errorf("nothing should remain after --remove-skills:\n%s", s)
	}
}

// Skills can outlive ~/.jentic; an explicit removal flag still applies when
// there is no install to tear down.
func TestUninstall_RemoveSkillsWithNothingInstalled(t *testing.T) {
	a, _, home, cwd := skillUninstallApp(t)
	if err := os.RemoveAll(a.Paths.Dir()); err != nil {
		t.Fatal(err)
	}
	claude := installSkill(t, home, cwd, "claude")

	if err := a.uninstallE(&uninstallOptions{yes: true, removeSkills: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}
	if exists(claude) {
		t.Error("--remove-skills must remove skills even with no ~/.jentic")
	}
}

// failingSkillCleanup simulates a skill engine that cannot probe or remove.
type failingSkillCleanup struct{ detectErr, removeErr error }

func (f failingSkillCleanup) InstalledSkills() ([]localagentcmd.InstalledSkill, error) {
	if f.detectErr != nil {
		return nil, f.detectErr
	}
	return []localagentcmd.InstalledSkill{{Operator: "claude", Skill: "jentic", Scope: "user", Display: "~/.claude/skills/jentic/SKILL.md"}}, nil
}

func (f failingSkillCleanup) SkillRemoveAll() error { return f.removeErr }

// A skill-cleanup failure (detection or removal) is warned, never fatal: the
// uninstall itself must still succeed and finish its teardown.
func TestUninstall_SkillCleanupFailureDoesNotFailUninstall(t *testing.T) {
	for name, fake := range map[string]failingSkillCleanup{
		"detect": {detectErr: errors.New("probe exploded")},
		"remove": {removeErr: errors.New("remove exploded")},
	} {
		t.Run(name, func(t *testing.T) {
			orig := newSkillCleanup
			newSkillCleanup = func(*app) skillCleanup { return fake }
			t.Cleanup(func() { newSkillCleanup = orig })

			a, out, _, _ := skillUninstallApp(t)
			if err := a.uninstallE(&uninstallOptions{yes: true, purge: true}); err != nil {
				t.Fatalf("skill cleanup failure must not fail uninstall: %v", err)
			}
			s := out.String()
			if !strings.Contains(s, "exploded") || !strings.Contains(s, "continuing") {
				t.Errorf("expected a continuing warning, got:\n%s", s)
			}
			if !strings.Contains(s, "jentic skill remove --all") {
				t.Errorf("expected the manual removal hint, got:\n%s", s)
			}
			if exists(filepath.Join(a.Paths.Dir(), "venv")) {
				t.Error("the filesystem teardown must still run after a skill-cleanup failure")
			}
		})
	}
}

// A detection environment that cannot resolve (no home dir) degrades to the
// generic hint rather than failing the uninstall.
func TestUninstall_DetectEnvErrorDegradesToHint(t *testing.T) {
	a, out, _, _ := skillUninstallApp(t)
	a.DetectEnv = func() (skillgen.DetectEnv, error) { return skillgen.DetectEnv{}, errors.New("no home") }

	if err := a.uninstallE(&uninstallOptions{yes: true, removeSkills: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}
	if s := out.String(); !strings.Contains(s, "no home") || !strings.Contains(s, "jentic skill remove --all") {
		t.Errorf("expected warning + generic hint, got:\n%s", s)
	}
}

func TestUninstallHelpDescribesSkillCleanup(t *testing.T) {
	cmd := newUninstallCmd(testApp(t))
	if cmd.Flags().Lookup("remove-skills") == nil {
		t.Fatal("uninstall command missing --remove-skills flag")
	}
	for _, want := range []string{"jentic skill remove --all", "--remove-skills", "implied by --purge", "manually edited"} {
		if !strings.Contains(cmd.Long, want) {
			t.Errorf("uninstall Long help missing %q", want)
		}
	}
}
