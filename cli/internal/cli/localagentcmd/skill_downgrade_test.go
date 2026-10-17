package localagentcmd

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// skill_downgrade_test.go pins #1342's downgrade guard: an install recording a
// newer content version than this CLI bundles (written by a newer CLI on the
// same machine) is reported as skipped_newer and left intact by init and
// update, and only --force rewrites it.

func TestSkillUpdateDoesNotDowngradeNewerInstall(t *testing.T) {
	h := newSkillJSONHarness(t)
	h.mustJSON(newHumanSkillCmd, "init", "--operator", "claude", "--skill", "jentic", "--json")
	skill := filepath.Join(h.home, ".claude", "skills", "jentic", "SKILL.md")
	sidecar := filepath.Join(filepath.Dir(skill), ".jentic-skill.json")
	available := skillgenBundledVersion(t)
	newer := available + "0"
	setSidecarVersion(t, skill, newer)

	for _, verb := range []string{"update", "init"} {
		env, stdout := h.mustJSON(newHumanSkillCmd, verb, "--operator", "claude", "--skill", "jentic", "--json")
		want := skillJSONResult{
			Operator: "claude", Skill: "jentic", Scope: "user", Path: skill,
			Action: "skipped_newer", InstalledVersion: newer, AvailableVersion: available,
		}
		if len(env.Results) != 1 || env.Results[0] != want {
			t.Errorf("%s results = %+v, want [%+v]\n%s", verb, env.Results, want, stdout)
		}
		data, err := os.ReadFile(sidecar)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(data), `"version":"`+newer+`"`) {
			t.Errorf("%s downgraded the newer install's sidecar:\n%s", verb, data)
		}
	}

	pretty, _, err := h.exec(newHumanSkillCmd, "update", "--operator", "claude", "--skill", "jentic")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(pretty, "v"+newer+" installed is newer than bundled v"+available+"; re-run with --force to downgrade") {
		t.Errorf("human update should explain the skipped downgrade:\n%s", pretty)
	}

	env, _ := h.mustJSON(newHumanSkillCmd, "update", "--operator", "claude", "--skill", "jentic", "--force", "--json")
	if len(env.Results) != 1 || env.Results[0].Action != "updated" {
		t.Errorf("forced update = %+v, want updated", env.Results)
	}
	data, err := os.ReadFile(sidecar)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), `"version": "`+available+`"`) {
		t.Errorf("--force should rewrite the install at the bundled version:\n%s", data)
	}
}

func TestSkillUpdateDoesNotDowngradeNewerSharedBlock(t *testing.T) {
	h := newSkillJSONHarness(t)
	h.mustJSON(newHumanSkillCmd, "init", "--operator", "generic", "--skill", "jentic", "--json")
	agents := filepath.Join(h.cwd, "AGENTS.md")
	newer := skillgenBundledVersion(t) + "0"
	stampSharedBlockVersion(t, agents, newer)

	env, stdout := h.mustJSON(newHumanSkillCmd, "update", "--operator", "codex,generic", "--skill", "jentic", "--json")
	if len(env.Results) != 2 {
		t.Fatalf("want a row per sharer, got:\n%s", stdout)
	}
	if r := env.Results[0]; r.Operator != "codex" || r.Action != "skipped_newer" {
		t.Errorf("acting row = %+v, want codex skipped_newer", r)
	}
	if r := env.Results[1]; r.Operator != "generic" || r.Action != "skipped_newer" || r.AppliedVia != "codex" {
		t.Errorf("mirrored row = %+v, want generic skipped_newer applied_via codex", r)
	}
	data, err := os.ReadFile(agents)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), "version="+newer) {
		t.Errorf("update downgraded the newer AGENTS.md block:\n%s", data)
	}
}
