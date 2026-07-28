package skillgen

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// installed reports whether any placement scope of the adapter holds a managed
// block, returning the first installed state when so. Test-side convenience
// over InstallStates (production callers want every state — a user and a
// project install can coexist).
func installed(a Adapter, env DetectEnv) (InstallState, bool) {
	for _, st := range InstallStates(a, env) {
		if st.Installed {
			return st, true
		}
	}
	return InstallState{}, false
}

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
	if _, ok := installed(ad, env); ok {
		t.Fatal("nothing written yet; installed must be false even when detected")
	}

	if _, err := Apply(ad, testContent(), env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	st, ok := installed(ad, env)
	if !ok {
		t.Fatal("skill written; installed must be true")
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

	// Install into the NON-default (project) scope; it must still be found.
	if _, err := Apply(ad, testContent(), env, ApplyOptions{Scope: ScopeProject}); err != nil {
		t.Fatal(err)
	}
	st, ok := installed(ad, env)
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

	st, ok := installed(ad, env)
	if !ok {
		t.Fatal("edited block is still installed")
	}
	if !st.UserEdits {
		t.Error("in-block edit must be reported as UserEdits")
	}
}

// TestInstallStatesSharedTarget pins the deliberate cross-operator semantics:
// codex and generic splice into the same project AGENTS.md, so a block written
// via one reports as installed for both — "installed" describes the artifact
// both runtimes load, not which operator name wrote it.
func TestInstallStatesSharedTarget(t *testing.T) {
	env := DetectEnv{Home: t.TempDir(), Cwd: t.TempDir()}
	reg := DefaultRegistry()
	codex, _ := reg.Resolve("codex")
	generic, _ := reg.Resolve("generic")
	if codex.Target(ScopeProject, env) != generic.Target(ScopeProject, env) {
		t.Fatal("codex and generic no longer share the project AGENTS.md; update the shared-target semantics in InstallStates")
	}

	if _, err := Apply(codex, testContent(), env, ApplyOptions{Scope: ScopeProject}); err != nil {
		t.Fatal(err)
	}
	st, ok := installed(generic, env)
	if !ok || st.Scope != ScopeProject {
		t.Fatalf("generic must report the codex-written project AGENTS.md as installed, got ok=%v state=%+v", ok, st)
	}
}

// TestInstallStatesDedupesEqualTargets covers Home == Cwd (running from $HOME):
// both scopes resolve to the same path, so only one state — labeled with the
// adapter's default scope, which is probed first — must be reported.
func TestInstallStatesDedupesEqualTargets(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")

	states := InstallStates(ad, env)
	if len(states) != 1 {
		t.Fatalf("equal targets must collapse to one state, got %d", len(states))
	}
	if states[0].Scope != ad.DefaultScope() {
		t.Errorf("collapsed state labeled %q, want the default scope %q", states[0].Scope, ad.DefaultScope())
	}
}

// TestInstallStatesReadErrorDegrades pins the documented policy that a probe
// read error means "not installed" rather than failing the listing.
func TestInstallStatesReadErrorDegrades(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("root ignores file permissions")
	}
	home := t.TempDir()
	env := DetectEnv{Home: home, Cwd: t.TempDir()}
	ad, _ := DefaultRegistry().Resolve("claude")

	out, err := Apply(ad, testContent(), env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(out.Path, 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(out.Path, 0o644) })

	for _, st := range InstallStates(ad, env) {
		if st.Path == out.Path && st.Installed {
			t.Error("unreadable target must degrade to not-installed")
		}
	}
}
