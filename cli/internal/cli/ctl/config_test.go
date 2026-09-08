package ctl

import (
	"strings"
	"testing"
)

func TestResolveSettings_PopulatesNestedSectionLeaves(t *testing.T) {
	got, err := ResolveSettings("", nil)
	if err != nil {
		t.Fatalf("ResolveSettings: %v", err)
	}
	// A scalar leaf inside a default_factory section must be present — the point
	// of the recursive $ref walk (a shallow read of root defaults would leave
	// every section empty).
	server, ok := asSettings(got["server"])
	if !ok {
		t.Fatalf("expected a server section in resolved defaults, got %v", got["server"])
	}
	if server["port"] == nil {
		t.Errorf("expected server.port to be defaulted from the schema, got %v", server)
	}
}

func TestResolveSettings_PresetOverridesOnlyNamedKeys(t *testing.T) {
	base, err := ResolveSettings("", nil)
	if err != nil {
		t.Fatalf("ResolveSettings(base): %v", err)
	}
	baseServer, _ := asSettings(base["server"])
	basePort := baseServer["port"]

	got, err := ResolveSettings("production", nil)
	if err != nil {
		t.Fatalf("ResolveSettings(production): %v", err)
	}
	runtime, ok := asSettings(got["runtime"])
	if !ok || runtime["debug"] != false {
		t.Errorf("production preset should set runtime.debug=false, got %v", got["runtime"])
	}
	// The preset names only runtime/server.reload; it must not wipe server.port.
	server, _ := asSettings(got["server"])
	if server["port"] != basePort {
		t.Errorf("preset clobbered an unrelated default: server.port = %v, want %v", server["port"], basePort)
	}
}

func TestResolveSettings_FlagOverridesWinOverPreset(t *testing.T) {
	got, err := ResolveSettings("production", Settings{
		"server": Settings{"port": float64(9999)},
	})
	if err != nil {
		t.Fatalf("ResolveSettings: %v", err)
	}
	server, _ := asSettings(got["server"])
	if server["port"] != float64(9999) {
		t.Errorf("flag override should win: server.port = %v, want 9999", server["port"])
	}
	// The preset's runtime.debug must survive alongside the flag override.
	runtime, _ := asSettings(got["runtime"])
	if runtime["debug"] != false {
		t.Errorf("preset value lost when a flag override was applied: %v", got["runtime"])
	}
}

func TestResolveSettings_UnknownPresetFailsLoud(t *testing.T) {
	if _, err := ResolveSettings("does-not-exist", nil); err == nil {
		t.Error("unknown preset must be a loud error")
	}
}

func TestPresets_ListsEmbedded(t *testing.T) {
	got := map[string]bool{}
	for _, p := range Presets() {
		got[p] = true
	}
	for _, want := range []string{"local-dev", "production"} {
		if !got[want] {
			t.Errorf("Presets() missing %q; got %v", want, Presets())
		}
	}
}

func TestSensitivePaths_FlagsSecretBearingLeaves(t *testing.T) {
	sensitive, err := SensitivePaths()
	if err != nil {
		t.Fatalf("SensitivePaths: %v", err)
	}
	// Known secret-bearing leaves in AppConfig must be marked so the installer
	// never binds them as CLI flags.
	for _, path := range []string{
		"databases.control.password",
		"admin.auth.jwt_secret",
		"admin.invite.pepper",
	} {
		if !sensitive[path] {
			t.Errorf("expected %q to be flagged sensitive; got set %v", path, sensitiveKeys(sensitive))
		}
	}
	// A plainly non-secret leaf must NOT be flagged (guard against over-broad
	// heuristics that would hide harmless knobs).
	if sensitive["server.port"] {
		t.Error("server.port must not be treated as sensitive")
	}
}

func sensitiveKeys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

// TestSensitiveHeuristicClassified is the GEN-4 classify-or-fail gate: every
// schema leaf the secret-NAME heuristic matches must either carry a schema
// marker (writeOnly/format=password) or a reviewed heuristicClassified entry.
// Without this, a benign new field (`token_ttl_seconds`, `max_tokens`) would
// silently vanish from the installer flags — no error, no test failure, the
// field just stays YAML-only.
func TestSensitiveHeuristicClassified(t *testing.T) {
	unclassified, err := UnclassifiedSensitiveNames()
	if err != nil {
		t.Fatalf("UnclassifiedSensitiveNames: %v", err)
	}
	if len(unclassified) > 0 {
		t.Errorf("secret-shaped config leaves without a schema marker or a reviewed classification:\n  %s\n"+
			"Fix: mark the field writeOnly/format=password in the backend Pydantic model (SecretStr), "+
			"OR add it to heuristicClassified in config.go — true if it is a real secret (excluded from flags), "+
			"false if it is a benign name (keeps its flag).", strings.Join(unclassified, "\n  "))
	}
}

// TestHeuristicClassifiedEntriesStillExist guards the other direction: a
// heuristicClassified entry naming a since-removed (or since-marker-annotated)
// leaf is stale and must be deleted, so the table shrinks toward empty as the
// backend annotation pass (GEN-9) lands.
func TestHeuristicClassifiedEntriesStillExist(t *testing.T) {
	hits, err := heuristicHitsWithoutMarker()
	if err != nil {
		t.Fatalf("heuristicHitsWithoutMarker: %v", err)
	}
	hitSet := map[string]bool{}
	for _, h := range hits {
		hitSet[h] = true
	}
	for path := range heuristicClassified {
		if !hitSet[path] {
			t.Errorf("heuristicClassified[%q] is stale — the leaf was removed or gained a schema marker; delete the entry", path)
		}
	}
}
