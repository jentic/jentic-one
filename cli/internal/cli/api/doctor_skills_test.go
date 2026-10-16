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
	st := func(skill string, f skillgen.Freshness, installed string) skillgen.InstallState {
		return skillgen.InstallState{Skill: skill, Installed: true, Freshness: f, InstalledVersion: installed, AvailableVersion: "6"}
	}
	cases := []struct {
		name       string
		states     []skillgen.InstallState
		wantStatus agentCheckStatus
		wantDetail []string
	}{
		{"all current", []skillgen.InstallState{st("jentic", skillgen.FreshnessCurrent, "6")}, agentPass, []string{"1 installed skill(s) up to date"}},
		{"stale", []skillgen.InstallState{
			st("jentic", skillgen.FreshnessStale, "1"),
			st("import-new-api", skillgen.FreshnessCurrent, "6"),
		}, agentWarn, []string{"1 of 2", "jentic v1→v6"}},
		{"newer is not stale", []skillgen.InstallState{st("jentic", skillgen.FreshnessNewer, "7")}, agentPass, []string{"up to date"}},
		{"unknown counted, not failed", []skillgen.InstallState{
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
	home, cwd := t.TempDir(), t.TempDir()
	t.Setenv("HOME", home)
	env := skillgen.DetectEnv{Home: home, Cwd: cwd}
	if version != "" {
		c, err := skillgen.Bundled("jentic", "")
		if err != nil {
			t.Fatal(err)
		}
		c.Version = version
		ad, _ := skillgen.DefaultRegistry().Resolve("claude")
		if _, err := skillgen.Apply(ad, c, env, skillgen.ApplyOptions{}); err != nil {
			t.Fatal(err)
		}
	}

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
	t.Run("nothing installed adds no row", func(t *testing.T) {
		if _, _, ok := skillsCheck(t, ""); ok {
			t.Error("doctor should not add a Skills row with nothing installed")
		}
	})
}

// TestDoctorSkillsCheckIsReadOnly: probing a stale install must not refresh it.
func TestDoctorSkillsCheckIsReadOnly(t *testing.T) {
	skillsCheck(t, "1")
	home, _ := os.UserHomeDir()
	data, err := os.ReadFile(filepath.Join(home, ".claude", "skills", "jentic", ".jentic-skill.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), `"version": "1"`) {
		t.Errorf("doctor rewrote the sidecar:\n%s", data)
	}
}
