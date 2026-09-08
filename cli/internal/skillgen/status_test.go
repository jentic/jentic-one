package skillgen

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// installed reports whether any placement scope of the adapter holds the named
// skill, returning the first installed state when so.
func installed(a Adapter, name string, env DetectEnv) (InstallState, bool) {
	for _, st := range InstallStates(a, name, env) {
		if st.Installed {
			return st, true
		}
	}
	return InstallState{}, false
}

// TestDefaultScopePolicy pins the placement policy ratified in #552.
func TestDefaultScopePolicy(t *testing.T) {
	want := map[Operator]Scope{
		OpClaude:  ScopeUser,
		OpCursor:  ScopeUser,
		OpHermes:  ScopeUser,
		OpCodex:   ScopeProject,
		OpGeneric: ScopeProject,
	}
	reg := DefaultRegistry()
	if len(reg.Adapters()) != len(want) {
		t.Fatalf("registry has %d adapters, policy table has %d — add the new operator to the #552 policy", len(reg.Adapters()), len(want))
	}
	for op, scope := range want {
		ad, ok := reg.Resolve(string(op))
		if !ok {
			t.Fatalf("operator %s missing from registry", op)
		}
		if got := ad.DefaultScope(); got != scope {
			t.Errorf("%s DefaultScope = %q, want %q (ratified in #552)", op, got, scope)
		}
	}
}

func TestInstallStatesSeparatesDetectionFromInstall(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	env := DetectEnv{
		Home:   home,
		Cwd:    cwd,
		Lookup: func(name string) bool { return name == "claude" },
		Stat:   func(p string) bool { _, err := os.Stat(p); return err == nil },
	}
	ad, _ := DefaultRegistry().Resolve("claude")
	if !ad.Detect(env) {
		t.Fatal("claude should be detected via PATH")
	}
	if _, ok := installed(ad, "jentic", env); ok {
		t.Fatal("nothing written yet; installed must be false even when detected")
	}

	if _, err := Apply(ad, jenticContent(t), env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	st, ok := installed(ad, "jentic", env)
	if !ok {
		t.Fatal("skill written; installed must be true")
	}
	if st.Skill != "jentic" {
		t.Errorf("state Skill = %q, want jentic", st.Skill)
	}
	if st.Scope != ScopeUser {
		t.Errorf("installed scope = %q, want user", st.Scope)
	}
	if want := filepath.Join(home, ".claude", "skills", "jentic", "SKILL.md"); st.Path != want {
		t.Errorf("installed path = %q, want %q", st.Path, want)
	}
	if st.UserEdits {
		t.Error("fresh install must not report user edits")
	}
}

// TestInstallStatesPerSkill proves state is tracked per skill: installing one
// leaves the others reported as not installed.
func TestInstallStatesPerSkill(t *testing.T) {
	home := t.TempDir()
	env := DetectEnv{Home: home, Cwd: t.TempDir()}
	ad, _ := DefaultRegistry().Resolve("claude")

	if _, err := Apply(ad, jenticContent(t), env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	if _, ok := installed(ad, "jentic", env); !ok {
		t.Error("jentic should be installed")
	}
	if _, ok := installed(ad, "import-new-api", env); ok {
		t.Error("import-new-api was not installed; must report not installed")
	}

	all := InstallStatesForSkills(ad, BundledNames(), env)
	var jenticInstalled, otherInstalled bool
	for _, st := range all {
		if st.Skill == "jentic" && st.Installed {
			jenticInstalled = true
		}
		if st.Skill != "jentic" && st.Installed {
			otherInstalled = true
		}
	}
	if !jenticInstalled {
		t.Error("flattened states missing installed jentic")
	}
	if otherInstalled {
		t.Error("only jentic was installed")
	}
}

func TestInstallStatesProbesBothScopes(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	env := DetectEnv{Home: home, Cwd: cwd}
	ad, _ := DefaultRegistry().Resolve("cursor")

	if _, err := Apply(ad, jenticContent(t), env, ApplyOptions{Scope: ScopeProject}); err != nil {
		t.Fatal(err)
	}
	st, ok := installed(ad, "jentic", env)
	if !ok {
		t.Fatal("project-scoped install not found")
	}
	if st.Scope != ScopeProject {
		t.Errorf("installed scope = %q, want project", st.Scope)
	}
	states := InstallStates(ad, "jentic", env)
	if len(states) != 2 {
		t.Fatalf("cursor has two distinct targets, got %d states", len(states))
	}
}

func TestInstallStatesReportsUserEdits(t *testing.T) {
	home := t.TempDir()
	env := DetectEnv{Home: home, Cwd: t.TempDir()}
	ad, _ := DefaultRegistry().Resolve("generic")

	out, err := Apply(ad, jenticContent(t), env, ApplyOptions{Scope: ScopeUser})
	if err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(out.Path)
	tampered := strings.Replace(string(data), "See the full skill", "See the TAMPERED skill", 1)
	if tampered == string(data) {
		t.Fatal("tamper target not found in written skill")
	}
	if err := os.WriteFile(out.Path, []byte(tampered), 0o644); err != nil {
		t.Fatal(err)
	}
	st, ok := installed(ad, "jentic", env)
	if !ok {
		t.Fatal("edited block is still installed")
	}
	if !st.UserEdits {
		t.Error("in-block edit must be reported as UserEdits")
	}
}

// TestInstallStatesSharedTarget: codex and generic share the project AGENTS.md.
func TestInstallStatesSharedTarget(t *testing.T) {
	env := DetectEnv{Home: t.TempDir(), Cwd: t.TempDir()}
	reg := DefaultRegistry()
	codex, _ := reg.Resolve("codex")
	generic, _ := reg.Resolve("generic")
	if codex.Target(ScopeProject, "jentic", env) != generic.Target(ScopeProject, "jentic", env) {
		t.Fatal("codex and generic no longer share the project AGENTS.md")
	}
	if _, err := Apply(codex, jenticContent(t), env, ApplyOptions{Scope: ScopeProject}); err != nil {
		t.Fatal(err)
	}
	st, ok := installed(generic, "jentic", env)
	if !ok || st.Scope != ScopeProject {
		t.Fatalf("generic must report the codex-written AGENTS.md as installed, got ok=%v state=%+v", ok, st)
	}
}

func TestInstallStatesDedupesEqualTargets(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	states := InstallStates(ad, "jentic", env)
	if len(states) != 1 {
		t.Fatalf("equal targets must collapse to one state, got %d", len(states))
	}
	if states[0].Scope != ad.DefaultScope() {
		t.Errorf("collapsed state labeled %q, want the default scope %q", states[0].Scope, ad.DefaultScope())
	}
}

func TestInstallStatesReadErrorDegrades(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("root ignores file permissions")
	}
	home := t.TempDir()
	env := DetectEnv{Home: home, Cwd: t.TempDir()}
	ad, _ := DefaultRegistry().Resolve("claude")

	out, err := Apply(ad, jenticContent(t), env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(out.Path, 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(out.Path, 0o644) })

	for _, st := range InstallStates(ad, "jentic", env) {
		if st.Path == out.Path && st.Installed {
			t.Error("unreadable target must degrade to not-installed")
		}
	}
}
