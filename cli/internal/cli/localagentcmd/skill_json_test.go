package localagentcmd

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// skill_json_test.go pins the #830 machine contract of the mutating skill
// verbs: `skill init/update/remove` emit one `{schema_version, results}`
// document on stdout under --json or agent mode, with human side reports on
// stderr, an empty selection as `[]` (never null), per-target failures as
// "error" results, and command-level failures as a returned error with an
// untouched stdout.

type skillJSONResult struct {
	Operator   string `json:"operator"`
	Skill      string `json:"skill"`
	Scope      string `json:"scope"`
	Path       string `json:"path"`
	Action     string `json:"action"`
	DryRun     bool   `json:"dry_run"`
	UserEdits  bool   `json:"user_edits"`
	Error      string `json:"error"`
	AppliedVia string `json:"applied_via"`
}

type skillJSONEnvelope struct {
	SchemaVersion string            `json:"schema_version"`
	Results       []skillJSONResult `json:"results"`
}

// skillJSONHarness runs skill subcommands against a fixed home+cwd with
// separate stdout/stderr buffers, so tests can prove stdout carries only JSON.
type skillJSONHarness struct {
	t         *testing.T
	home, cwd string
	detected  []string
}

func newSkillJSONHarness(t *testing.T, detected ...string) *skillJSONHarness {
	t.Helper()
	home := t.TempDir()
	cwd := t.TempDir()
	t.Setenv("HOME", home)
	t.Chdir(cwd)
	return &skillJSONHarness{t: t, home: home, cwd: cwd, detected: detected}
}

// exec runs args through build (newHumanSkillCmd or newMachineSkillCmd) and
// returns stdout, stderr, and the command error.
func (h *skillJSONHarness) exec(build func(*testing.T, *Cmd) *cobra.Command, args ...string) (stdout, stderr string, err error) {
	h.t.Helper()
	out, errOut := new(bytes.Buffer), new(bytes.Buffer)
	app := testApp(h.t)
	stubDetect(h.t, app, h.home, h.cwd, h.detected...)
	app.Out, app.Err = out, errOut
	cmd := build(h.t, app)
	// The real root silences cobra's usage/error echo; mirror it so stdout
	// assertions see only what the command itself wrote.
	cmd.SilenceUsage, cmd.SilenceErrors = true, true
	cmd.SetOut(out)
	cmd.SetErr(errOut)
	cmd.SetArgs(args)
	err = cmd.Execute()
	return out.String(), errOut.String(), err
}

// mustJSON runs args and decodes stdout as exactly one envelope.
func (h *skillJSONHarness) mustJSON(build func(*testing.T, *Cmd) *cobra.Command, args ...string) (skillJSONEnvelope, string) {
	h.t.Helper()
	stdout, stderr, err := h.exec(build, args...)
	if err != nil {
		h.t.Fatalf("skill %v: %v\nstderr: %s", args, err, stderr)
	}
	return decodeSkillEnvelope(h.t, stdout), stdout
}

func decodeSkillEnvelope(t *testing.T, stdout string) skillJSONEnvelope {
	t.Helper()
	dec := json.NewDecoder(strings.NewReader(stdout))
	dec.DisallowUnknownFields()
	var env skillJSONEnvelope
	if err := dec.Decode(&env); err != nil {
		t.Fatalf("stdout is not a single JSON envelope: %v\n%s", err, stdout)
	}
	if dec.More() {
		t.Fatalf("stdout carries more than one JSON document:\n%s", stdout)
	}
	if env.SchemaVersion != "1" {
		t.Errorf("schema_version = %q, want \"1\"", env.SchemaVersion)
	}
	return env
}

func TestSkillInitJSONEnvelope(t *testing.T) {
	h := newSkillJSONHarness(t)
	env, stdout := h.mustJSON(newHumanSkillCmd, "init", "--operator", "generic", "--skill", "jentic", "--json")

	if len(env.Results) != 1 {
		t.Fatalf("want 1 result, got %d:\n%s", len(env.Results), stdout)
	}
	r := env.Results[0]
	want := skillJSONResult{
		Operator: "generic", Skill: "jentic", Scope: "project",
		Path: filepath.Join(h.cwd, "AGENTS.md"), Action: "created",
	}
	if r != want {
		t.Errorf("result = %+v, want %+v", r, want)
	}
	if _, err := os.Stat(want.Path); err != nil {
		t.Errorf("init --json must still write the skill: %v", err)
	}
	if strings.Contains(stdout, "Jentic skills") {
		t.Errorf("human heading leaked into JSON stdout:\n%s", stdout)
	}

	// A re-run reports the idempotent no-op.
	env, _ = h.mustJSON(newHumanSkillCmd, "init", "--operator", "generic", "--skill", "jentic", "--json")
	if got := env.Results[0].Action; got != "unchanged" {
		t.Errorf("re-run action = %q, want unchanged", got)
	}
}

func TestSkillInitJSONDryRun(t *testing.T) {
	h := newSkillJSONHarness(t)
	env, _ := h.mustJSON(newHumanSkillCmd, "init", "--operator", "claude", "--json", "--dry-run")
	if len(env.Results) == 0 {
		t.Fatal("dry run must still report the would-be outcomes")
	}
	for _, r := range env.Results {
		if !r.DryRun || r.Action != "created" {
			t.Errorf("dry-run result = %+v, want action created + dry_run true", r)
		}
	}
	if _, err := os.Stat(filepath.Join(h.home, ".claude")); !os.IsNotExist(err) {
		t.Error("dry run must not write anything")
	}
}

// TestSkillInitMachineModeAutoJSON proves agent mode selects the envelope
// without --json, and that the #755 defaulted-target echo moves to stderr so
// stdout stays one JSON document.
func TestSkillInitMachineModeAutoJSON(t *testing.T) {
	h := newSkillJSONHarness(t, "claude")
	stdout, stderr, err := h.exec(newMachineSkillCmd, "init", "--yes", "--skill", "jentic")
	if err != nil {
		t.Fatalf("init in agent mode: %v\nstderr: %s", err, stderr)
	}
	env := decodeSkillEnvelope(t, stdout)
	if len(env.Results) != 1 || env.Results[0].Operator != "claude" || env.Results[0].Action != "created" {
		t.Errorf("unexpected results: %+v", env.Results)
	}
	if !strings.Contains(stderr, "defaulting to detected operators") {
		t.Errorf("defaulted-target echo should go to stderr in JSON mode; stderr:\n%s", stderr)
	}
}

// TestSkillInitHumanModeStaysPretty pins the inverse: an explicit human mode
// without --json keeps the human report.
func TestSkillInitHumanModeStaysPretty(t *testing.T) {
	h := newSkillJSONHarness(t)
	stdout, _, err := h.exec(newHumanSkillCmd, "init", "--operator", "generic", "--skill", "jentic")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(stdout, "Jentic skills") || strings.Contains(stdout, `"schema_version"`) {
		t.Errorf("explicit human mode should render the human report:\n%s", stdout)
	}
}

func TestSkillInitJSONUserEdits(t *testing.T) {
	h := newSkillJSONHarness(t)
	h.mustJSON(newHumanSkillCmd, "init", "--operator", "claude", "--skill", "jentic", "--json")
	skill := filepath.Join(h.home, ".claude", "skills", "jentic", "SKILL.md")
	if err := os.WriteFile(skill, []byte("my own notes\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	env, _ := h.mustJSON(newHumanSkillCmd, "init", "--operator", "claude", "--skill", "jentic", "--json")
	r := env.Results[0]
	if r.Action != "user_edits" || !r.UserEdits {
		t.Errorf("edited install result = %+v, want action user_edits + user_edits true", r)
	}
}

// TestSkillInitJSONPerTargetError proves a per-target failure becomes an
// "error" result carrying the message while the command itself still succeeds
// (exit-code semantics are unchanged from the human path, which warns and
// continues).
func TestSkillInitJSONPerTargetError(t *testing.T) {
	h := newSkillJSONHarness(t)
	// A directory where AGENTS.md should be makes the read fail.
	if err := os.Mkdir(filepath.Join(h.cwd, "AGENTS.md"), 0o755); err != nil {
		t.Fatal(err)
	}
	env, _ := h.mustJSON(newHumanSkillCmd, "init", "--operator", "generic", "--skill", "jentic", "--json")
	r := env.Results[0]
	if r.Action != "error" || r.Error == "" {
		t.Errorf("result = %+v, want action error with a message", r)
	}
}

// TestSkillMutatingVerbsJSONCommandErrors proves command-level failures are
// returned (so the root renders the error envelope and exit code) and never
// leave a partial document on stdout.
func TestSkillMutatingVerbsJSONCommandErrors(t *testing.T) {
	h := newSkillJSONHarness(t)
	for _, args := range [][]string{
		{"init", "--operator", "bogus", "--json"},
		{"init", "--operator", "claude", "--all", "--json"},
		{"init", "--operator", "claude", "--scope", "galaxy", "--json"},
		{"update", "--operator", "bogus", "--json"},
		{"remove", "--json"},
		{"remove", "--operator", "claude", "--all", "--json"},
		{"remove", "--operator", "claude", "--skill", "nope", "--json"},
	} {
		stdout, _, err := h.exec(newMachineSkillCmd, args...)
		if err == nil {
			t.Errorf("skill %v: expected an error", args)
		}
		if stdout != "" {
			t.Errorf("skill %v: stdout must stay empty on a command error, got:\n%s", args, stdout)
		}
	}
}

// TestSkillMutatingVerbsJSONEmptyResults pins `[]` (never null) when nothing
// was touched.
func TestSkillMutatingVerbsJSONEmptyResults(t *testing.T) {
	h := newSkillJSONHarness(t)
	for _, args := range [][]string{
		{"update", "--json"},
		{"remove", "--operator", "claude", "--json"},
	} {
		env, stdout := h.mustJSON(newHumanSkillCmd, args...)
		if env.Results == nil || len(env.Results) != 0 {
			t.Errorf("skill %v: want empty results, got %+v", args, env.Results)
		}
		if !strings.Contains(stdout, `"results": []`) {
			t.Errorf("skill %v: empty results must encode as [], got:\n%s", args, stdout)
		}
	}
}

func TestSkillRemoveJSON(t *testing.T) {
	h := newSkillJSONHarness(t)
	h.mustJSON(newHumanSkillCmd, "init", "--operator", "cursor", "--skill", "jentic", "--json")

	// Dry run first: reported, nothing deleted.
	env, _ := h.mustJSON(newHumanSkillCmd, "remove", "--operator", "cursor", "--json", "--dry-run")
	skill := filepath.Join(h.home, ".cursor", "skills", "jentic", "SKILL.md")
	if len(env.Results) != 1 || env.Results[0].Action != "removed" || !env.Results[0].DryRun {
		t.Fatalf("dry-run remove results = %+v", env.Results)
	}
	if _, err := os.Stat(skill); err != nil {
		t.Fatalf("dry-run remove deleted the skill: %v", err)
	}

	// Machine mode, no --json: real removal.
	stdout, _, err := h.exec(newMachineSkillCmd, "remove", "--operator", "cursor")
	if err != nil {
		t.Fatal(err)
	}
	env = decodeSkillEnvelope(t, stdout)
	want := skillJSONResult{Operator: "cursor", Skill: "jentic", Scope: "user", Path: skill, Action: "removed"}
	if len(env.Results) != 1 || env.Results[0] != want {
		t.Errorf("remove results = %+v, want [%+v]", env.Results, want)
	}
	if _, err := os.Stat(skill); !os.IsNotExist(err) {
		t.Error("remove --json must still delete the skill")
	}

	// An explicit --scope with nothing there reports not_installed per skill.
	env, _ = h.mustJSON(newHumanSkillCmd, "remove", "--operator", "cursor", "--skill", "jentic", "--scope", "user", "--json")
	if len(env.Results) != 1 || env.Results[0].Action != "not_installed" {
		t.Errorf("explicit-scope remove of a missing skill = %+v, want not_installed", env.Results)
	}
}

func TestSkillUpdateJSON(t *testing.T) {
	h := newSkillJSONHarness(t)
	h.mustJSON(newHumanSkillCmd, "init", "--operator", "claude", "--skill", "jentic", "--base-url", "http://one.test", "--json")

	env, _ := h.mustJSON(newHumanSkillCmd, "update", "--operator", "claude", "--skill", "jentic", "--base-url", "http://two.test", "--json")
	if len(env.Results) != 1 || env.Results[0].Action != "updated" || env.Results[0].Scope != "user" {
		t.Fatalf("update results = %+v, want one updated user-scope row", env.Results)
	}

	stdout, _, err := h.exec(newMachineSkillCmd, "update", "--operator", "claude", "--skill", "jentic", "--base-url", "http://two.test")
	if err != nil {
		t.Fatal(err)
	}
	env = decodeSkillEnvelope(t, stdout)
	if len(env.Results) != 1 || env.Results[0].Action != "unchanged" {
		t.Errorf("idempotent update results = %+v, want unchanged", env.Results)
	}
}

// setSidecarVersion rewrites the recorded version in an owned-file install's
// sidecar (delete it when v is empty), simulating an install made from older
// (or unstamped) bundled content.
func setSidecarVersion(t *testing.T, skillMD, v string) {
	t.Helper()
	path := filepath.Join(filepath.Dir(skillMD), ".jentic-skill.json")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatal(err)
	}
	if v == "" {
		delete(m, "version")
	} else {
		m["version"] = v
	}
	out, _ := json.Marshal(m)
	if err := os.WriteFile(path, out, 0o644); err != nil {
		t.Fatal(err)
	}
}

type listInstallRow struct {
	Skill            string  `json:"skill"`
	Scope            string  `json:"scope"`
	Installed        bool    `json:"installed"`
	Stale            bool    `json:"stale"`
	Freshness        *string `json:"freshness"`
	InstalledVersion *string `json:"installed_version"`
	AvailableVersion string  `json:"available_version"`
}

// claudeJenticRows decodes `skill list --json` and returns claude's jentic rows.
func claudeJenticRows(t *testing.T, stdout string) []listInstallRow {
	t.Helper()
	var payload struct {
		Operators []struct {
			Operator string           `json:"operator"`
			Installs []listInstallRow `json:"installs"`
		} `json:"operators"`
	}
	if err := json.Unmarshal([]byte(stdout), &payload); err != nil {
		t.Fatalf("list --json invalid: %v\n%s", err, stdout)
	}
	var rows []listInstallRow
	for _, op := range payload.Operators {
		if op.Operator != "claude" {
			continue
		}
		for _, r := range op.Installs {
			if r.Skill == "jentic" {
				rows = append(rows, r)
			}
		}
	}
	if len(rows) == 0 {
		t.Fatalf("no claude/jentic rows in:\n%s", stdout)
	}
	return rows
}

// TestSkillListReportsStaleness pins #1342 on `skill list`: additive JSON
// version fields per install row, a human stale marker, and a refresh nudge.
func TestSkillListReportsStaleness(t *testing.T) {
	h := newSkillJSONHarness(t)
	h.mustJSON(newHumanSkillCmd, "init", "--operator", "claude", "--skill", "jentic", "--json")
	skill := filepath.Join(h.home, ".claude", "skills", "jentic", "SKILL.md")
	available := skillgenBundledVersion(t)

	// Current install.
	stdout, _, err := h.exec(newHumanSkillCmd, "list", "--json")
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range claudeJenticRows(t, stdout) {
		if r.AvailableVersion != available {
			t.Errorf("%s row available_version = %q, want %q", r.Scope, r.AvailableVersion, available)
		}
		if !r.Installed {
			if r.Stale || r.Freshness != nil || r.InstalledVersion != nil {
				t.Errorf("uninstalled %s row must carry no installed version fields: %+v", r.Scope, r)
			}
			continue
		}
		if r.Stale || deref(r.Freshness) != "current" || deref(r.InstalledVersion) != available {
			t.Errorf("current install row = %+v", r)
		}
	}

	// Stale install.
	setSidecarVersion(t, skill, "1")
	stdout, _, err = h.exec(newHumanSkillCmd, "list", "--json")
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range claudeJenticRows(t, stdout) {
		if r.Installed && (!r.Stale || deref(r.Freshness) != "stale" || deref(r.InstalledVersion) != "1") {
			t.Errorf("stale install row = %+v", r)
		}
	}
	pretty, _, err := h.exec(newHumanSkillCmd, "list")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(pretty, "stale: v1 installed, v"+available+" available") {
		t.Errorf("human list missing the stale marker:\n%s", pretty)
	}
	if !strings.Contains(pretty, "jentic skill update") {
		t.Errorf("human list missing the refresh nudge:\n%s", pretty)
	}

	// Unstamped install: unknown, never an error.
	setSidecarVersion(t, skill, "")
	stdout, _, err = h.exec(newHumanSkillCmd, "list", "--json")
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range claudeJenticRows(t, stdout) {
		if r.Installed && (r.Stale || deref(r.Freshness) != "unknown" || deref(r.InstalledVersion) != "unknown") {
			t.Errorf("unstamped install row = %+v", r)
		}
	}
	pretty, _, _ = h.exec(newHumanSkillCmd, "list")
	if !strings.Contains(pretty, "version unknown") || strings.Contains(pretty, "jentic skill update") {
		t.Errorf("unstamped install should read 'version unknown' with no nudge:\n%s", pretty)
	}

	// `skill update` refreshes the stamp and clears staleness.
	setSidecarVersion(t, skill, "1")
	env, _ := h.mustJSON(newHumanSkillCmd, "update", "--operator", "claude", "--skill", "jentic", "--json")
	if len(env.Results) != 1 || env.Results[0].Action != "updated" {
		t.Errorf("update of a stale install = %+v, want updated", env.Results)
	}
	stdout, _, _ = h.exec(newHumanSkillCmd, "list", "--json")
	for _, r := range claudeJenticRows(t, stdout) {
		if r.Installed && r.Stale {
			t.Errorf("update should clear staleness: %+v", r)
		}
	}
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

func skillgenBundledVersion(t *testing.T) string {
	t.Helper()
	v := skillgen.BundledVersion("jentic")
	if v == "" {
		t.Fatal("bundled jentic skill has no version")
	}
	return v
}

// stampSharedBlockVersion rewrites the version token on the jentic managed
// block's BEGIN line in path, simulating an install from older content.
func stampSharedBlockVersion(t *testing.T, path, v string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(string(data), "\n")
	var stamped bool
	for i, l := range lines {
		if strings.HasPrefix(l, "<!-- BEGIN JENTIC MANAGED SKILL: jentic ") {
			fields := strings.Fields(l)
			for j, f := range fields {
				if strings.HasPrefix(f, "version=") {
					fields[j] = "version=" + v
					stamped = true
				}
			}
			lines[i] = strings.Join(fields, " ")
		}
	}
	if !stamped {
		t.Fatalf("no version token on the jentic BEGIN line in %s", path)
	}
	if err := os.WriteFile(path, []byte(strings.Join(lines, "\n")), 0o644); err != nil {
		t.Fatal(err)
	}
}

// TestSkillListSharedAgentsMDCountedOnce pins the review fix on PR #1670: a
// stale block in the project AGENTS.md that codex and generic both load is
// listed under each operator (marked shared) but counted once, matching
// `jentic doctor`.
func TestSkillListSharedAgentsMDCountedOnce(t *testing.T) {
	h := newSkillJSONHarness(t)
	h.mustJSON(newHumanSkillCmd, "init", "--operator", "generic", "--skill", "jentic", "--json")
	agents := filepath.Join(h.cwd, "AGENTS.md")
	stampSharedBlockVersion(t, agents, "1")

	pretty, _, err := h.exec(newHumanSkillCmd, "list")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(pretty, "1 installed skill(s) are older than the bundled copy") {
		t.Errorf("shared stale block must be counted once:\n%s", pretty)
	}
	if !strings.Contains(pretty, "(shared with generic)") || !strings.Contains(pretty, "(shared with codex)") {
		t.Errorf("each operator's row should name the other sharer:\n%s", pretty)
	}

	stdout, _, err := h.exec(newHumanSkillCmd, "list", "--json")
	if err != nil {
		t.Fatal(err)
	}
	var payload struct {
		Operators []struct {
			Operator string `json:"operator"`
			Installs []struct {
				Skill      string   `json:"skill"`
				Path       string   `json:"path"`
				Installed  bool     `json:"installed"`
				Stale      bool     `json:"stale"`
				SharedWith []string `json:"shared_with"`
			} `json:"installs"`
		} `json:"operators"`
	}
	if err := json.Unmarshal([]byte(stdout), &payload); err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"codex": "generic", "generic": "codex"}
	for _, op := range payload.Operators {
		for _, r := range op.Installs {
			if !r.Installed || r.Skill != "jentic" || r.Path != agents {
				continue
			}
			if !r.Stale || len(r.SharedWith) != 1 || r.SharedWith[0] != want[op.Operator] {
				t.Errorf("%s shared row = %+v, want stale + shared_with [%s]", op.Operator, r, want[op.Operator])
			}
			delete(want, op.Operator)
		}
	}
	if len(want) != 0 {
		t.Errorf("missing shared AGENTS.md rows for %v", want)
	}
}

// TestSkillUpdateSharedAgentsMDActsOnce: `skill update` across codex and
// generic rewrites the shared AGENTS.md once and mirrors that outcome onto
// the second operator, instead of reporting "updated" then "unchanged".
func TestSkillUpdateSharedAgentsMDActsOnce(t *testing.T) {
	h := newSkillJSONHarness(t)
	h.mustJSON(newHumanSkillCmd, "init", "--operator", "generic", "--skill", "jentic", "--json")
	stampSharedBlockVersion(t, filepath.Join(h.cwd, "AGENTS.md"), "1")

	env, stdout := h.mustJSON(newHumanSkillCmd, "update", "--operator", "codex,generic", "--skill", "jentic", "--json")
	var project []skillJSONResult
	for _, r := range env.Results {
		if r.Scope == "project" {
			project = append(project, r)
		}
	}
	if len(project) != 2 {
		t.Fatalf("want one project row per sharer, got:\n%s", stdout)
	}
	if project[0].Operator != "codex" || project[0].Action != "updated" || project[0].AppliedVia != "" {
		t.Errorf("acting row = %+v, want codex updated", project[0])
	}
	if project[1].Operator != "generic" || project[1].Action != "updated" || project[1].AppliedVia != "codex" {
		t.Errorf("mirrored row = %+v, want generic updated applied_via codex", project[1])
	}

	pretty, _, err := h.exec(newHumanSkillCmd, "update", "--operator", "codex,generic", "--skill", "jentic")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(pretty, "same file as codex/jentic") {
		t.Errorf("human update should say the generic row shares codex's file:\n%s", pretty)
	}
}

// TestSkillRemoveSharedAgentsMDActsOnce: `skill remove --all` strips the
// shared block once and reports it for both sharers (no silent skip).
func TestSkillRemoveSharedAgentsMDActsOnce(t *testing.T) {
	for _, args := range [][]string{
		{"remove", "--operator", "codex,generic", "--skill", "jentic", "--json"},
		{"remove", "--operator", "codex,generic", "--skill", "jentic", "--scope", "project", "--json"},
	} {
		h := newSkillJSONHarness(t)
		h.mustJSON(newHumanSkillCmd, "init", "--operator", "generic", "--skill", "jentic", "--json")
		env, stdout := h.mustJSON(newHumanSkillCmd, args...)
		if len(env.Results) != 2 {
			t.Fatalf("%v: want a row per sharer, got:\n%s", args, stdout)
		}
		if r := env.Results[0]; r.Operator != "codex" || r.Action != "removed" || r.AppliedVia != "" {
			t.Errorf("%v: acting row = %+v", args, r)
		}
		if r := env.Results[1]; r.Operator != "generic" || r.Action != "removed" || r.AppliedVia != "codex" {
			t.Errorf("%v: mirrored row = %+v, want removed applied_via codex", args, r)
		}
	}
}

// TestSkillListJSONEnvelope pins the list document's schema_version and that
// installs is always an array.
func TestSkillListJSONEnvelope(t *testing.T) {
	h := newSkillJSONHarness(t)
	stdout, _, err := h.exec(newHumanSkillCmd, "list", "--json")
	if err != nil {
		t.Fatal(err)
	}
	var payload struct {
		SchemaVersion string                       `json:"schema_version"`
		Operators     []map[string]json.RawMessage `json:"operators"`
	}
	if err := json.Unmarshal([]byte(stdout), &payload); err != nil {
		t.Fatal(err)
	}
	if payload.SchemaVersion != "1" {
		t.Errorf("schema_version = %q, want \"1\"", payload.SchemaVersion)
	}
	for _, op := range payload.Operators {
		if raw := string(op["installs"]); raw == "" || raw == "null" || raw[0] != '[' {
			t.Errorf("installs must be an array, got %s", raw)
		}
	}
}
