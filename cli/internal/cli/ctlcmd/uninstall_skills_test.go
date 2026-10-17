package ctlcmd

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
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
	for _, want := range []string{"still installed", "claude/jentic", "codex+generic/jentic", "jentic skill remove --all"} {
		if !strings.Contains(s, want) {
			t.Errorf("output missing %q:\n%s", want, s)
		}
	}
	// codex and generic share the project AGENTS.md: list that block once.
	if strings.Contains(s, "  generic/jentic") || strings.Count(s, "AGENTS.md") != 1 {
		t.Errorf("shared AGENTS.md block must be listed once:\n%s", s)
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

// --purge removes unmodified user-scope skill installs, keeps (and reports)
// manually edited ones with a targeted --force command, leaves project-scope
// installs under the cwd alone, and still completes the uninstall.
func TestUninstall_PurgeRemovesUserSkillsOnly(t *testing.T) {
	a, out, home, cwd := skillUninstallApp(t)
	claude := installSkill(t, home, cwd, "claude")
	cursor := installSkill(t, home, cwd, "cursor")
	agents := installSkill(t, home, cwd, "codex") // project scope: ./AGENTS.md
	if err := os.WriteFile(cursor, []byte("my own notes\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := a.uninstallE(&uninstallOptions{yes: true, purge: true}); err != nil {
		t.Fatalf("uninstall --purge: %v", err)
	}
	if exists(claude) {
		t.Error("--purge must remove the unmodified user-scope claude skill")
	}
	if data, err := os.ReadFile(agents); err != nil || !strings.Contains(string(data), "JENTIC MANAGED SKILL") {
		t.Errorf("--purge must not touch project-scope installs under the cwd (err=%v):\n%s", err, data)
	}
	if !exists(cursor) {
		t.Fatal("--purge must never delete a manually edited skill")
	}
	s := out.String()
	for _, want := range []string{
		"Uninstall will remove", "Removed 1 Jentic skill install(s)", "cursor/jentic", "(manually edited)",
		"jentic skill remove --operator cursor --skill jentic --scope user --force", "--remove-project-skills",
	} {
		if !strings.Contains(s, want) {
			t.Errorf("output missing %q:\n%s", want, s)
		}
	}
	if strings.Contains(s, "re-run with --force") || strings.Contains(s, "Re-run with --force") {
		t.Errorf("the engine's generic --force hint must not leak into uninstall output:\n%s", s)
	}
	if exists(filepath.Join(a.Paths.Dir(), "venv")) {
		t.Error("the filesystem teardown must still run")
	}
}

// --remove-project-skills also strips the project-scope installs.
func TestUninstall_RemoveProjectSkills(t *testing.T) {
	a, out, home, cwd := skillUninstallApp(t)
	claude := installSkill(t, home, cwd, "claude")
	agents := installSkill(t, home, cwd, "codex")

	if err := a.uninstallE(&uninstallOptions{yes: true, removeProjectSkills: true}); err != nil {
		t.Fatalf("uninstall --remove-project-skills: %v", err)
	}
	if exists(claude) || exists(agents) {
		t.Errorf("both scopes must be removed:\n%s", out.String())
	}
	if s := out.String(); strings.Contains(s, "still installed") {
		t.Errorf("nothing should remain:\n%s", s)
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

// Without --yes, the skills-only path (no ~/.jentic) previews and asks; a
// declined prompt removes nothing.
func TestUninstall_SkillsOnlyAsksWithoutYes(t *testing.T) {
	a, out, home, cwd := skillUninstallApp(t)
	if err := os.RemoveAll(a.Paths.Dir()); err != nil {
		t.Fatal(err)
	}
	claude := installSkill(t, home, cwd, "claude")
	asked := 0
	orig := askSkillRemoval
	askSkillRemoval = func(n int) (bool, error) { asked = n; return false, nil }
	t.Cleanup(func() { askSkillRemoval = orig })

	if err := a.uninstallE(&uninstallOptions{removeSkills: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}
	if asked != 1 {
		t.Fatalf("expected one prompt for 1 skill, got %d", asked)
	}
	if !exists(claude) {
		t.Fatal("a declined prompt must remove nothing")
	}
	s := out.String()
	if !strings.Contains(s, "Uninstall will remove") || !strings.Contains(s, "skill removal cancelled") {
		t.Errorf("expected the preview and a cancel line:\n%s", s)
	}
}

// --dry-run lists what would go, removes nothing, and skips the teardown.
func TestUninstall_DryRun(t *testing.T) {
	a, out, home, cwd := skillUninstallApp(t)
	claude := installSkill(t, home, cwd, "claude")
	agents := installSkill(t, home, cwd, "codex")

	if err := a.uninstallE(&uninstallOptions{dryRun: true, purge: true}); err != nil {
		t.Fatalf("uninstall --dry-run: %v", err)
	}
	if !exists(claude) || !exists(agents) || !exists(filepath.Join(a.Paths.Dir(), "venv")) {
		t.Fatal("--dry-run must change nothing")
	}
	s := out.String()
	for _, want := range []string{"Would remove these Jentic skill installs", "claude/jentic", "would stay installed", "codex+generic/jentic", "Dry run"} {
		if !strings.Contains(s, want) {
			t.Errorf("output missing %q:\n%s", want, s)
		}
	}
}

// A detection failure never stops the teardown. Without a removal flag it is
// only a warning; with one, uninstall finishes and exits non-zero, since the
// skills it was asked to remove are still there.
func TestUninstall_DetectEnvError(t *testing.T) {
	for name, opts := range map[string]uninstallOptions{
		"report only": {yes: true},
		"remove":      {yes: true, removeSkills: true},
	} {
		t.Run(name, func(t *testing.T) {
			a, out, _, _ := skillUninstallApp(t)
			a.DetectEnv = func() (skillgen.DetectEnv, error) { return skillgen.DetectEnv{}, errors.New("no home") }

			err := a.uninstallE(&opts)
			if opts.removeSkills != (err != nil) {
				t.Fatalf("removeSkills=%v but err=%v", opts.removeSkills, err)
			}
			s := out.String()
			if strings.Count(s, "could not detect installed skills") != 1 {
				t.Errorf("detection warning must print exactly once:\n%s", s)
			}
			if !strings.Contains(s, "no home") || !strings.Contains(s, "jentic skill remove --all") {
				t.Errorf("expected warning + generic hint, got:\n%s", s)
			}
			if exists(filepath.Join(a.Paths.Dir(), "venv")) {
				t.Error("the filesystem teardown must still run")
			}
		})
	}
}

func TestUninstallHelpDescribesSkillCleanup(t *testing.T) {
	cmd := newUninstallCmd(testApp(t))
	for _, flag := range []string{"remove-skills", "remove-project-skills", "dry-run"} {
		if cmd.Flags().Lookup(flag) == nil {
			t.Fatalf("uninstall command missing --%s flag", flag)
		}
	}
	for _, want := range []string{
		"jentic skill remove --all", "--remove-skills", "implied by --purge",
		"--remove-project-skills", "--dry-run", "Manually edited", "exits\nnon-zero",
	} {
		if !strings.Contains(cmd.Long, want) {
			t.Errorf("uninstall Long help missing %q", want)
		}
	}
}
