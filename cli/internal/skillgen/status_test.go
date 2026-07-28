package skillgen

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestDefaultScopePolicy pins the placement policy ratified in #552: dir-skill
// runtimes (claude/cursor) and hermes install user-globally so the skill is
// available from any directory; AGENTS.md operators (codex/generic) install
// into the project. Changing a DefaultScope must be a deliberate decision —
// update this test AND the #552 discussion, not just the adapter.
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
		Home: home,
		Cwd:  cwd,
		// Detection fires (binary on PATH) even though nothing is installed —
		// the exact #752 scenario.
		Lookup: func(name string) bool { return name == "claude" },
		Stat:   func(p string) bool { _, err := os.Stat(p); return err == nil },
	}
	ad, _ := DefaultRegistry().Resolve("claude")
	if !ad.Detect(env) {
		t.Fatal("claude should be detected via PATH")
	}
	if _, ok := Installed(ad, env); ok {
		t.Fatal("nothing written yet; Installed must be false even when detected")
	}

	if _, err := Apply(ad, testContent(), env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	st, ok := Installed(ad, env)
	if !ok {
		t.Fatal("skill written; Installed must be true")
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

func TestInstallStatesProbesBothScopes(t *testing.T) {
	home := t.TempDir()
	cwd := t.TempDir()
	env := DetectEnv{Home: home, Cwd: cwd}
	ad, _ := DefaultRegistry().Resolve("cursor")

	// Install into the NON-default (project) scope; Installed must still find it.
	if _, err := Apply(ad, testContent(), env, ApplyOptions{Scope: ScopeProject}); err != nil {
		t.Fatal(err)
	}
	st, ok := Installed(ad, env)
	if !ok {
		t.Fatal("project-scoped install not found")
	}
	if st.Scope != ScopeProject {
		t.Errorf("installed scope = %q, want project", st.Scope)
	}

	states := InstallStates(ad, env)
	if len(states) != 2 {
		t.Fatalf("cursor has two distinct targets, got %d states", len(states))
	}
}

func TestInstallStatesReportsUserEdits(t *testing.T) {
	home := t.TempDir()
	env := DetectEnv{Home: home, Cwd: t.TempDir()}
	ad, _ := DefaultRegistry().Resolve("generic")

	out, err := Apply(ad, testContent(), env, ApplyOptions{Scope: ScopeUser})
	if err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(out.Path)
	tampered := strings.Replace(string(data), "jentic register", "jentic register --tampered", 1)
	if tampered == string(data) {
		t.Fatal("tamper target not found in written skill")
	}
	if err := os.WriteFile(out.Path, []byte(tampered), 0o644); err != nil {
		t.Fatal(err)
	}

	st, ok := Installed(ad, env)
	if !ok {
		t.Fatal("edited block is still installed")
	}
	if !st.UserEdits {
		t.Error("in-block edit must be reported as UserEdits")
	}
}
