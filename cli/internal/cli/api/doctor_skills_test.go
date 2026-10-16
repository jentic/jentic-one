package api

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// doctor_skills_test.go pins the #1342 doctor row: one aggregate Skills check
// that warns (never fails) when an installed skill is older than the bundled
// copy, passes when everything is current, and is absent with nothing
// installed.

func TestSummarizeSkillFreshness(t *testing.T) {
	st := func(skill string, f skillgen.Freshness, installed string, ops ...skillgen.Operator) skillgen.InstalledSkill {
		if len(ops) == 0 {
			ops = []skillgen.Operator{skillgen.OpClaude}
		}
		return skillgen.InstalledSkill{
			InstallState: skillgen.InstallState{Skill: skill, Installed: true, Freshness: f, InstalledVersion: installed, AvailableVersion: "6"},
			Operators:    ops,
		}
	}
	cases := []struct {
		name       string
		states     []skillgen.InstalledSkill
		wantStatus agentCheckStatus
		wantDetail []string
	}{
		{"all current", []skillgen.InstalledSkill{st("jentic", skillgen.FreshnessCurrent, "6")}, agentPass, []string{"1 installed skill(s) up to date"}},
		{"stale", []skillgen.InstalledSkill{
			st("jentic", skillgen.FreshnessStale, "1"),
			st("import-new-api", skillgen.FreshnessCurrent, "6"),
		}, agentWarn, []string{"1 of 2", "jentic v1→v6 (claude)"}},
		{"shared artifact counted once, all operators named", []skillgen.InstalledSkill{
			st("jentic", skillgen.FreshnessStale, "1", skillgen.OpCodex, skillgen.OpGeneric),
		}, agentWarn, []string{"1 of 1", "jentic v1→v6 (codex, generic)"}},
		{"newer is not stale", []skillgen.InstalledSkill{st("jentic", skillgen.FreshnessNewer, "7")}, agentPass, []string{"none older than the bundled copy", "(1 newer than this CLI — upgrade jentic)"}},
		{"newer alongside current", []skillgen.InstalledSkill{
			st("jentic", skillgen.FreshnessNewer, "7"),
			st("import-new-api", skillgen.FreshnessCurrent, "6"),
		}, agentPass, []string{"2 installed skill(s)", "(1 newer than this CLI — upgrade jentic)"}},
		{"newer and stale together", []skillgen.InstalledSkill{
			st("jentic", skillgen.FreshnessNewer, "7"),
			st("import-new-api", skillgen.FreshnessStale, "1"),
		}, agentWarn, []string{"1 of 2", "import-new-api v1→v6", "(1 newer than this CLI — upgrade jentic)"}},
		{"unknown counted, not failed", []skillgen.InstalledSkill{
			st("jentic", skillgen.FreshnessUnknown, ""),
			st("import-new-api", skillgen.FreshnessCurrent, "6"),
		}, agentPass, []string{"2 installed skill(s) up to date", "1 with unknown version"}},
	}
	for _, tc := range cases {
		status, detail, hint := summarizeSkillFreshness(tc.states)
		if status != tc.wantStatus {
			t.Errorf("%s: status = %v, want %v", tc.name, status, tc.wantStatus)
		}
		for _, want := range tc.wantDetail {
			if !strings.Contains(detail, want) {
				t.Errorf("%s: detail %q missing %q", tc.name, detail, want)
			}
		}
		if (status == agentWarn) != strings.Contains(hint, "jentic skill update") {
			t.Errorf("%s: hint %q should name `jentic skill update` exactly when warning", tc.name, hint)
		}
	}
}

// skillsCheck runs doctor --json against an isolated home with an optional
// claude jentic install recorded at version (none when version is "") and
// returns the Skills/freshness row, if any.
func skillsCheck(t *testing.T, version string) (status, detail string, found bool) {
	t.Helper()
	return skillsCheckFor(t, "claude", version)
}

// skillsCheckFor is skillsCheck installing through the given operator.
func skillsCheckFor(t *testing.T, operator, version string) (status, detail string, found bool) {
	t.Helper()
	env := doctorSkillsEnv(t)
	if version != "" {
		installJentic(t, env, operator, version)
	}
	return runDoctorSkills(t, env)
}

// doctorSkillsEnv is an isolated home+cwd detection env with HOME pointed at it.
func doctorSkillsEnv(t *testing.T) skillgen.DetectEnv {
	t.Helper()
	home, cwd := t.TempDir(), t.TempDir()
	t.Setenv("HOME", home)
	return skillgen.DetectEnv{Home: home, Cwd: cwd}
}

// installJentic writes the bundled jentic skill through operator, stamped as
// version.
func installJentic(t *testing.T, env skillgen.DetectEnv, operator, version string) {
	t.Helper()
	c, err := skillgen.Bundled("jentic", "")
	if err != nil {
		t.Fatal(err)
	}
	c.Version = version
	ad, _ := skillgen.DefaultRegistry().Resolve(operator)
	if _, err := skillgen.Apply(ad, c, env, skillgen.ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
}

// runDoctorSkills runs doctor --json against env and returns the
// Skills/freshness row, if any.
func runDoctorSkills(t *testing.T, env skillgen.DetectEnv) (status, detail string, found bool) {
	t.Helper()
	app := testApp(t)
	app.DetectEnv = func() (skillgen.DetectEnv, error) { return env, nil }
	cmd := newDoctorCmd(app)
	cmd.SetContext(activeCtx("http://127.0.0.1:1"))
	_ = cmd.Flags().Set("json", "true")
	_ = cmd.RunE(cmd, nil) // reachability fails against the dead port; only the Skills row matters

	var report struct {
		Checks []struct {
			Section string `json:"section"`
			Name    string `json:"name"`
			Status  string `json:"status"`
			Detail  string `json:"detail"`
		} `json:"checks"`
	}
	out := app.Out.(*bytes.Buffer).String()
	if err := json.Unmarshal([]byte(out), &report); err != nil {
		t.Fatalf("doctor JSON did not parse: %v\n%s", err, out)
	}
	for _, c := range report.Checks {
		if c.Section == "Skills" && c.Name == "freshness" {
			return c.Status, c.Detail, true
		}
	}
	return "", "", false
}

func TestDoctorSkillsCheck(t *testing.T) {
	t.Run("stale warns", func(t *testing.T) {
		status, detail, ok := skillsCheck(t, "1")
		if !ok || status != "warn" || !strings.Contains(detail, "jentic v1→v"+skillgen.BundledVersion("jentic")) {
			t.Errorf("stale install row = (%v, %q, %q), want a warn naming jentic v1", ok, status, detail)
		}
	})
	t.Run("current passes", func(t *testing.T) {
		status, _, ok := skillsCheck(t, skillgen.BundledVersion("jentic"))
		if !ok || status != "pass" {
			t.Errorf("current install row = (%v, %q), want pass", ok, status)
		}
	})
	t.Run("shared AGENTS.md counted once with both operators", func(t *testing.T) {
		status, detail, ok := skillsCheckFor(t, "generic", "1")
		if !ok || status != "warn" || !strings.Contains(detail, "1 of 1") || !strings.Contains(detail, "(codex, generic)") {
			t.Errorf("shared stale block row = (%v, %q, %q), want warn, 1 of 1, (codex, generic)", ok, status, detail)
		}
	})
	t.Run("nothing installed adds no row", func(t *testing.T) {
		if _, _, ok := skillsCheck(t, ""); ok {
			t.Error("doctor should not add a Skills row with nothing installed")
		}
	})
}

// TestDoctorSkillsCheckIsReadOnly: probing stale installs must not touch
// anything on disk — neither an owned-file install (SKILL.md, sidecar,
// references) nor the shared AGENTS.md block.
func TestDoctorSkillsCheckIsReadOnly(t *testing.T) {
	env := doctorSkillsEnv(t)
	installJentic(t, env, "claude", "1")
	installJentic(t, env, "generic", "1")

	before := snapshotTree(t, env.Home, env.Cwd)
	if len(before) == 0 {
		t.Fatal("snapshot found no files; the installs did not land")
	}
	if status, _, ok := runDoctorSkills(t, env); !ok || status != "warn" {
		t.Fatalf("doctor row = (%v, %q), want a stale warn", ok, status)
	}
	after := snapshotTree(t, env.Home, env.Cwd)

	for path, b := range before {
		a, ok := after[path]
		switch {
		case !ok:
			t.Errorf("doctor removed %s", path)
		case a != b:
			t.Errorf("doctor modified %s", path)
		}
	}
	for path := range after {
		if _, ok := before[path]; !ok {
			t.Errorf("doctor created %s", path)
		}
	}
}

// fileState is one file's bytes and modification time.
type fileState struct {
	data    string
	modTime int64
}

// snapshotTree records every regular file under roots.
func snapshotTree(t *testing.T, roots ...string) map[string]fileState {
	t.Helper()
	out := map[string]fileState{}
	for _, root := range roots {
		err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
			if err != nil || d.IsDir() {
				return err
			}
			info, err := d.Info()
			if err != nil {
				return err
			}
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			out[path] = fileState{data: string(data), modTime: info.ModTime().UnixNano()}
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	return out
}
