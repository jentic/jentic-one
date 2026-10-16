package skillgen

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// staleness_test.go pins #1342: an install records the content version it was
// rendered from (owned-file sidecar, hermes frontmatter, or the AGENTS.md
// block's version token), and InstallStates compares it with the bundled copy.

func TestCompareVersions(t *testing.T) {
	cases := []struct {
		name               string
		installed, current string
		want               Freshness
	}{
		{"equal", "6", "6", FreshnessCurrent},
		{"older", "1", "6", FreshnessStale},
		{"newer", "7", "6", FreshnessNewer},
		{"numeric not lexical", "9", "10", FreshnessStale},
		{"dotted older", "1.2", "1.10", FreshnessStale},
		{"dotted trailing zero", "2", "2.0", FreshnessCurrent},
		{"v prefix", "v6", "6", FreshnessCurrent},
		{"missing installed", "", "6", FreshnessUnknown},
		{"missing available", "6", "", FreshnessUnknown},
		{"unparseable installed", "three", "6", FreshnessUnknown},
		{"unparseable component", "1.x", "1.2", FreshnessUnknown},
		{"negative", "-1", "6", FreshnessUnknown},
		{"signed", "+6", "6", FreshnessUnknown},
	}
	for _, tc := range cases {
		if got := CompareVersions(tc.installed, tc.current); got != tc.want {
			t.Errorf("%s: CompareVersions(%q, %q) = %q, want %q", tc.name, tc.installed, tc.current, got, tc.want)
		}
	}
}

func TestVersionTokenRejectsMarkerInjection(t *testing.T) {
	for _, in := range []string{"6 -->", "6\nEND", "a b", "<!--", strings.Repeat("9", 33)} {
		if got := versionToken(in); got != "" {
			t.Errorf("versionToken(%q) = %q, want \"\"", in, got)
		}
	}
	if got := versionToken(` "6" `); got != "6" {
		t.Errorf("versionToken trims quotes/space: got %q", got)
	}
}

func TestBundledVersion(t *testing.T) {
	if got, want := BundledVersion("jentic"), jenticContent(t).Version; got != want || got == "" {
		t.Errorf("BundledVersion(jentic) = %q, want %q", got, want)
	}
	if got := BundledVersion("no-such-skill"); got != "" {
		t.Errorf("unknown skill version = %q, want \"\"", got)
	}
}

// stalenessEnv is a throwaway home+cwd detection env.
func stalenessEnv(t *testing.T) DetectEnv {
	t.Helper()
	return DetectEnv{Home: t.TempDir(), Cwd: t.TempDir()}
}

// withVersion returns the bundled jentic content stamped as version v.
func withVersion(t *testing.T, v string) Canonical {
	t.Helper()
	c := jenticContent(t)
	c.Version = v
	return c
}

func mustInstalled(t *testing.T, a Adapter, env DetectEnv) InstallState {
	t.Helper()
	st, ok := installed(a, "jentic", env)
	if !ok {
		t.Fatal("jentic should be installed")
	}
	return st
}

func TestInstallStateFreshnessOwnedFile(t *testing.T) {
	bundled := BundledVersion("jentic")
	ad, _ := DefaultRegistry().Resolve("claude")

	for _, tc := range []struct {
		name, applied string
		want          Freshness
	}{
		{"current", bundled, FreshnessCurrent},
		{"stale", "1", FreshnessStale},
		{"newer", "999", FreshnessNewer},
	} {
		t.Run(tc.name, func(t *testing.T) {
			env := stalenessEnv(t)
			if _, err := Apply(ad, withVersion(t, tc.applied), env, ApplyOptions{}); err != nil {
				t.Fatal(err)
			}
			st := mustInstalled(t, ad, env)
			if st.InstalledVersion != tc.applied || st.AvailableVersion != bundled || st.Freshness != tc.want {
				t.Errorf("state = {installed %q, available %q, %q}, want {%q, %q, %q}",
					st.InstalledVersion, st.AvailableVersion, st.Freshness, tc.applied, bundled, tc.want)
			}
			if st.Stale() != (tc.want == FreshnessStale) {
				t.Errorf("Stale() = %v for %q", st.Stale(), tc.want)
			}
		})
	}
}

// rewriteSidecar edits the sidecar next to skillMD through fn.
func rewriteSidecar(t *testing.T, skillMD string, fn func(map[string]any)) {
	t.Helper()
	path := sidecarPath(skillMD)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatal(err)
	}
	fn(m)
	out, _ := json.Marshal(m)
	if err := os.WriteFile(path, out, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestInstallStateFreshnessUnknownVersion(t *testing.T) {
	ad, _ := DefaultRegistry().Resolve("claude")
	for _, tc := range []struct {
		name string
		edit func(map[string]any)
	}{
		{"sidecar predates version", func(m map[string]any) { delete(m, "version") }},
		{"unparseable version", func(m map[string]any) { m["version"] = "three" }},
		{"marker-unsafe version", func(m map[string]any) { m["version"] = "6 -->" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			env := stalenessEnv(t)
			out, err := Apply(ad, jenticContent(t), env, ApplyOptions{})
			if err != nil {
				t.Fatal(err)
			}
			rewriteSidecar(t, out.Path, tc.edit)
			st := mustInstalled(t, ad, env)
			if st.Freshness != FreshnessUnknown || st.Stale() {
				t.Errorf("freshness = %q (stale %v), want unknown", st.Freshness, st.Stale())
			}
		})
	}
}

// TestInstallStateFreshnessHermesFrontmatterFallback: hermes stamps `version:`
// in its SKILL.md frontmatter, which is read when the sidecar lacks one.
func TestInstallStateFreshnessHermesFrontmatterFallback(t *testing.T) {
	env := stalenessEnv(t)
	ad, _ := DefaultRegistry().Resolve("hermes")
	out, err := Apply(ad, withVersion(t, "1"), env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	rewriteSidecar(t, out.Path, func(m map[string]any) { delete(m, "version") })
	st := mustInstalled(t, ad, env)
	if st.InstalledVersion != "1" || st.Freshness != FreshnessStale {
		t.Errorf("hermes frontmatter fallback = {%q, %q}, want {1, stale}", st.InstalledVersion, st.Freshness)
	}
}

func TestInstallStateFreshnessSharedFile(t *testing.T) {
	env := stalenessEnv(t)
	ad, _ := DefaultRegistry().Resolve("generic")
	out, err := Apply(ad, withVersion(t, "1"), env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(out.Path)
	if !strings.Contains(string(data), " version=1\n") {
		t.Fatalf("AGENTS.md BEGIN line should carry the version token:\n%s", data)
	}
	if st := mustInstalled(t, ad, env); st.Freshness != FreshnessStale || st.InstalledVersion != "1" {
		t.Errorf("shared-file state = {%q, %q}, want {1, stale}", st.InstalledVersion, st.Freshness)
	}

	// A block written without the token (an install that predates it) is
	// unknown, not an error.
	stripped := strings.Replace(string(data), " version=1", "", 1)
	if err := os.WriteFile(out.Path, []byte(stripped), 0o644); err != nil {
		t.Fatal(err)
	}
	if st := mustInstalled(t, ad, env); st.Freshness != FreshnessUnknown || st.UserEdits {
		t.Errorf("token-less block = {%q, edits %v}, want {unknown, false}", st.Freshness, st.UserEdits)
	}
}

// TestReapplyRefreshesVersionStamp: a version-only bump (identical rendered
// bytes) is still a write, so `skill update`/`init` clear staleness.
func TestReapplyRefreshesVersionStamp(t *testing.T) {
	for _, op := range []string{"claude", "generic"} {
		t.Run(op, func(t *testing.T) {
			env := stalenessEnv(t)
			ad, _ := DefaultRegistry().Resolve(op)
			if _, err := Apply(ad, withVersion(t, "1"), env, ApplyOptions{}); err != nil {
				t.Fatal(err)
			}
			dry, err := Apply(ad, jenticContent(t), env, ApplyOptions{DryRun: true})
			if err != nil {
				t.Fatal(err)
			}
			if !dry.Changed {
				t.Error("dry run must report the pending version-stamp refresh")
			}
			if st := mustInstalled(t, ad, env); !st.Stale() {
				t.Fatal("dry run must not refresh the stamp")
			}
			out, err := Apply(ad, jenticContent(t), env, ApplyOptions{})
			if err != nil {
				t.Fatal(err)
			}
			if !out.Changed {
				t.Error("version-only refresh should report a change")
			}
			if st := mustInstalled(t, ad, env); st.Freshness != FreshnessCurrent {
				t.Errorf("after re-apply freshness = %q, want current", st.Freshness)
			}
			again, err := Apply(ad, jenticContent(t), env, ApplyOptions{})
			if err != nil {
				t.Fatal(err)
			}
			if again.Changed {
				t.Error("a current install must stay idempotent")
			}
		})
	}
}

// TestInstalledStatesDedupesSharedTargets: codex and generic share the
// project AGENTS.md, so one block is reported once.
func TestInstalledStatesDedupesSharedTargets(t *testing.T) {
	env := stalenessEnv(t)
	reg := DefaultRegistry()
	ad, _ := reg.Resolve("generic")
	if _, err := Apply(ad, jenticContent(t), env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	states := InstalledStates(reg, env)
	if len(states) != 1 || states[0].Path != filepath.Join(env.Cwd, "AGENTS.md") {
		t.Fatalf("InstalledStates = %+v, want the single shared AGENTS.md block", states)
	}
	// Attributed to every operator that loads it, in registry order —
	// regardless of which one wrote it.
	if got := states[0].Operators; len(got) != 2 || got[0] != OpCodex || got[1] != OpGeneric {
		t.Errorf("shared block operators = %v, want [codex generic]", got)
	}
}

// TestApplyRefusesToDowngradeNewerInstall: an install recording a newer
// version than the content being applied is left byte-for-byte intact (an
// older CLI must not downgrade it) unless Force; a user edit still reports
// first, since it needs --force whatever the version.
func TestApplyRefusesToDowngradeNewerInstall(t *testing.T) {
	for _, op := range []string{"claude", "hermes", "generic"} {
		t.Run(op, func(t *testing.T) {
			env := stalenessEnv(t)
			ad, _ := DefaultRegistry().Resolve(op)
			bundled := BundledVersion("jentic")
			if _, err := Apply(ad, withVersion(t, "999"), env, ApplyOptions{}); err != nil {
				t.Fatal(err)
			}
			target := ad.Target(ad.DefaultScope(), "jentic", env)
			before, err := os.ReadFile(target)
			if err != nil {
				t.Fatal(err)
			}

			for _, dry := range []bool{true, false} {
				out, err := Apply(ad, jenticContent(t), env, ApplyOptions{DryRun: dry})
				if err != nil {
					t.Fatal(err)
				}
				if !out.NewerInstalled || out.Changed || out.InstalledVersion != "999" || out.AppliedVersion != bundled {
					t.Errorf("dry=%v outcome = %+v, want NewerInstalled 999 over %s and no change", dry, out, bundled)
				}
			}
			after, _ := os.ReadFile(target)
			if string(after) != string(before) {
				t.Error("a newer install must not be rewritten without Force")
			}
			if st := mustInstalled(t, ad, env); st.Freshness != FreshnessNewer {
				t.Errorf("freshness = %q, want newer", st.Freshness)
			}

			out, err := Apply(ad, jenticContent(t), env, ApplyOptions{Force: true})
			if err != nil {
				t.Fatal(err)
			}
			if out.NewerInstalled || !out.Changed {
				t.Errorf("forced outcome = %+v, want a downgrade write", out)
			}
			if st := mustInstalled(t, ad, env); st.Freshness != FreshnessCurrent {
				t.Errorf("after forced apply freshness = %q, want current", st.Freshness)
			}
		})
	}

	t.Run("user edits report first", func(t *testing.T) {
		env := stalenessEnv(t)
		ad, _ := DefaultRegistry().Resolve("claude")
		if _, err := Apply(ad, withVersion(t, "999"), env, ApplyOptions{}); err != nil {
			t.Fatal(err)
		}
		target := ad.Target(ad.DefaultScope(), "jentic", env)
		if err := os.WriteFile(target, []byte("my notes\n"), 0o644); err != nil {
			t.Fatal(err)
		}
		out, err := Apply(ad, jenticContent(t), env, ApplyOptions{})
		if err != nil {
			t.Fatal(err)
		}
		if !out.UserEdits || out.NewerInstalled {
			t.Errorf("outcome = %+v, want UserEdits (not NewerInstalled)", out)
		}
	})
}

// TestProbeAllMatchesPerAdapterProbes: the single probe pass yields exactly
// the per-operator rows InstallStatesForSkills reports and the deduplicated
// set InstalledStates reports.
func TestProbeAllMatchesPerAdapterProbes(t *testing.T) {
	env := stalenessEnv(t)
	reg := DefaultRegistry()
	for _, op := range []string{"claude", "generic"} {
		ad, _ := reg.Resolve(op)
		if _, err := Apply(ad, jenticContent(t), env, ApplyOptions{}); err != nil {
			t.Fatal(err)
		}
	}
	p := ProbeAll(reg, env)
	for _, ad := range reg.Adapters() {
		want := InstallStatesForSkills(ad, BundledNames(), env)
		got := p.ByOperator[ad.Operator()]
		if len(got) != len(want) {
			t.Fatalf("%s: %d rows, want %d", ad.Operator(), len(got), len(want))
		}
		for i := range want {
			if got[i] != want[i] {
				t.Errorf("%s row %d = %+v, want %+v", ad.Operator(), i, got[i], want[i])
			}
		}
	}
	if len(p.Installed) != 2 {
		t.Errorf("installed artifacts = %+v, want the claude SKILL.md and the shared AGENTS.md block", p.Installed)
	}
}
