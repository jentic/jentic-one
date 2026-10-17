package ctlcmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/localagentcmd"
)

// plantEscapingSidecar rewrites an installed claude skill's sidecar so its
// recorded references climb out of the skill dir to victim. The SKILL.md and
// its body hash stay intact, so the install reads as an unedited Jentic write —
// the shape a repo shipping .claude/skills/jentic/ can plant in a project.
func plantEscapingSidecar(t *testing.T, skillMD, victim string) {
	t.Helper()
	sidecar := filepath.Join(filepath.Dir(skillMD), ".jentic-skill.json")
	data, err := os.ReadFile(sidecar)
	if err != nil {
		t.Fatalf("read sidecar: %v", err)
	}
	var sc map[string]any
	if err := json.Unmarshal(data, &sc); err != nil {
		t.Fatal(err)
	}
	refDir := filepath.Join(filepath.Dir(skillMD), "references")
	rel, err := filepath.Rel(refDir, victim)
	if err != nil {
		t.Fatal(err)
	}
	sc["references"] = []string{rel, victim, "../../../../../" + filepath.Base(victim)}
	out, err := json.Marshal(sc)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(sidecar, out, 0o600); err != nil {
		t.Fatal(err)
	}
}

func writeVictimFile(t *testing.T) string {
	t.Helper()
	victim := filepath.Join(t.TempDir(), "victim.txt")
	if err := os.WriteFile(victim, []byte("keep me\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return victim
}

// installProjectClaude installs the claude skill at project scope (under cwd).
func installProjectClaude(t *testing.T, home, cwd string) string {
	t.Helper()
	path := installSkill(t, home, cwd, "claude")
	project := filepath.Join(cwd, ".claude", "skills", "jentic")
	if err := os.MkdirAll(filepath.Dir(project), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(filepath.Dir(path), project); err != nil {
		t.Fatal(err)
	}
	return filepath.Join(project, "SKILL.md")
}

// Pins the CWE-22 regression through `jenticctl uninstall`: a planted
// project-scope install whose sidecar references escape the skill dir is
// removed, but the outside file it names survives.
func TestUninstall_PlantedSidecarCannotDeleteOutsideFiles(t *testing.T) {
	for name, opts := range map[string]uninstallOptions{
		"project scope via --remove-project-skills": {yes: true, purge: true, removeProjectSkills: true},
		"user scope via --purge":                    {yes: true, purge: true},
	} {
		t.Run(name, func(t *testing.T) {
			a, _, home, cwd := skillUninstallApp(t)
			var skillMD string
			if opts.removeProjectSkills {
				skillMD = installProjectClaude(t, home, cwd)
			} else {
				skillMD = installSkill(t, home, cwd, "claude")
			}
			victim := writeVictimFile(t)
			plantEscapingSidecar(t, skillMD, victim)

			if err := a.uninstallE(&opts); err != nil {
				t.Fatalf("uninstall: %v", err)
			}
			if exists(skillMD) {
				t.Error("the planted SKILL.md itself is ours to remove")
			}
			if !exists(victim) {
				t.Fatal("a file outside the skill dir named by the sidecar was deleted")
			}
		})
	}
}

// --purge never touches a project-scope install, even a planted one.
func TestUninstall_PurgeLeavesPlantedProjectInstall(t *testing.T) {
	a, _, home, cwd := skillUninstallApp(t)
	skillMD := installProjectClaude(t, home, cwd)
	victim := writeVictimFile(t)
	plantEscapingSidecar(t, skillMD, victim)

	if err := a.uninstallE(&uninstallOptions{yes: true, purge: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}
	if !exists(skillMD) || !exists(victim) {
		t.Fatal("--purge must not touch project-scope installs under the cwd")
	}
}

// A references/ dir replaced by a symlink to an outside dir cannot redirect
// uninstall's deletes there; reference symlinks are unlinked, not followed.
func TestUninstall_SymlinkEscapeKeepsTargets(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlinks need elevated privileges on Windows")
	}
	a, _, home, cwd := skillUninstallApp(t)
	skillMD := installSkill(t, home, cwd, "claude")
	refDir := filepath.Join(filepath.Dir(skillMD), "references")
	entries, err := os.ReadDir(refDir)
	if err != nil || len(entries) == 0 {
		t.Fatalf("expected shipped references: %v", err)
	}
	outside := t.TempDir()
	victims := make([]string, 0, len(entries))
	for _, e := range entries {
		v := filepath.Join(outside, e.Name())
		if err := os.WriteFile(v, []byte("keep me\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		victims = append(victims, v)
	}
	if err := os.RemoveAll(refDir); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, refDir); err != nil {
		t.Fatal(err)
	}

	if err := a.uninstallE(&uninstallOptions{yes: true, removeSkills: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}
	for _, v := range victims {
		if !exists(v) {
			t.Errorf("uninstall followed the references/ symlink and deleted %s", v)
		}
	}
	if fi, err := os.Lstat(refDir); err == nil && fi.Mode()&os.ModeSymlink == 0 {
		t.Error("the references/ symlink must never be replaced by a real dir")
	}
}

// A real per-file failure (the skill dir is read-only, so the SKILL.md
// cannot be unlinked) is reported, the teardown still completes, the
// leftover is listed, and uninstall exits non-zero with the manual command.
func TestUninstall_PartialSkillRemovalFailureExitsNonZero(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("directory write permissions do not block unlink on Windows")
	}
	if os.Geteuid() == 0 {
		t.Skip("root ignores directory permissions")
	}
	a, out, home, cwd := skillUninstallApp(t)
	cursor := installSkill(t, home, cwd, "cursor")
	claude := installSkill(t, home, cwd, "claude")
	dir := filepath.Dir(claude)
	if err := os.Chmod(dir, 0o500); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(dir, 0o700) })

	err := a.uninstallE(&uninstallOptions{yes: true, removeSkills: true})
	if err == nil || !strings.Contains(err.Error(), "jentic skill remove --all") {
		t.Fatalf("a partial skill-removal failure must fail uninstall with the manual command, got %v", err)
	}
	if exists(cursor) {
		t.Error("the removable cursor skill must still be removed")
	}
	if !exists(claude) {
		t.Fatal("the read-only claude skill should have survived")
	}
	s := out.String()
	for _, want := range []string{"could not remove claude/jentic", "still installed", "claude/jentic"} {
		if !strings.Contains(s, want) {
			t.Errorf("output missing %q:\n%s", want, s)
		}
	}
	if exists(filepath.Join(a.Paths.Dir(), "venv")) {
		t.Error("the filesystem teardown must still run")
	}
}

// A SKILL.md at a Jentic skill path with no provenance is not ours: it is
// labelled as such, never removed, and never offered --force.
func TestUninstall_UnattributedSkillNotOfferedForce(t *testing.T) {
	a, out, home, _ := skillUninstallApp(t)
	foreign := filepath.Join(home, ".claude", "skills", "jentic", "SKILL.md")
	if err := os.MkdirAll(filepath.Dir(foreign), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(foreign, []byte("someone else's skill\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := a.uninstallE(&uninstallOptions{yes: true, removeSkills: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}
	if !exists(foreign) {
		t.Fatal("an unattributed SKILL.md must never be removed")
	}
	s := out.String()
	if !strings.Contains(s, "(not written by Jentic)") {
		t.Errorf("expected the unattributed label:\n%s", s)
	}
	if strings.Contains(s, "--force") {
		t.Errorf("--force must not be suggested for content Jentic did not write:\n%s", s)
	}
}

// The listing shortens paths against the probed home, not the process's.
func TestUninstall_DisplayUsesProbedHome(t *testing.T) {
	a, out, home, cwd := skillUninstallApp(t)
	installSkill(t, home, cwd, "claude")
	if err := a.uninstallE(&uninstallOptions{yes: true}); err != nil {
		t.Fatal(err)
	}
	want := filepath.Join("~", ".claude", "skills", "jentic", "SKILL.md")
	if s := out.String(); !strings.Contains(s, want) {
		t.Errorf("expected %q in listing:\n%s", want, s)
	}
}

// editBeforeRemove is a skillCleanup that edits one installed file between
// uninstall's probe and its removal, then removes through the real engine.
type editBeforeRemove struct {
	skillCleanup
	path string
}

func (e editBeforeRemove) RemoveSkillCleanupTargets(targets []localagentcmd.SkillCleanupTarget, dryRun bool) ([]localagentcmd.SkillCleanupOutcome, error) {
	if err := os.WriteFile(e.path, []byte("edited after the preview\n"), 0o600); err != nil {
		return nil, err
	}
	return e.skillCleanup.RemoveSkillCleanupTargets(targets, dryRun)
}

// A skill edited between the preview and the removal is kept by the engine's
// edit guard and reported as manually edited with the targeted --force
// command, not the `--all` hint that would refuse it again.
func TestUninstall_EditedSinceProbeGetsForceHint(t *testing.T) {
	a, out, home, cwd := skillUninstallApp(t)
	claude := installSkill(t, home, cwd, "claude")
	prev := newSkillCleanup
	t.Cleanup(func() { newSkillCleanup = prev })
	newSkillCleanup = func(a *app) skillCleanup { return editBeforeRemove{skillCleanup: prev(a), path: claude} }

	if err := a.uninstallE(&uninstallOptions{yes: true, removeSkills: true}); err != nil {
		t.Fatalf("uninstall: %v", err)
	}
	if !exists(claude) {
		t.Fatal("a skill edited after the preview must be kept")
	}
	s := out.String()
	for _, want := range []string{
		"claude/jentic", "(manually edited)",
		"jentic skill remove --operator claude --skill jentic --scope user --force",
	} {
		if !strings.Contains(s, want) {
			t.Errorf("output missing %q:\n%s", want, s)
		}
	}
	if strings.Contains(s, "jentic skill remove --all") {
		t.Errorf("the --all hint cannot remove an edited skill:\n%s", s)
	}
}
