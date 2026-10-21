package ctl

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ctl/generated"
	"gopkg.in/yaml.v3"
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

// TestBackendConfig_ExplicitZeroValuesSurviveRoundTrip pins #1832: fields whose
// schema default is non-zero must not carry omitempty/omitzero, so an explicit
// zero value (0.0 float, 0 int, false bool) survives a JSON or YAML marshal +
// unmarshal round-trip instead of reverting to its non-zero schema default.
func TestBackendConfig_ExplicitZeroValuesSurviveRoundTrip(t *testing.T) {
	const inputJSON = `{
		"databases": {
			"admin": {"name": "jentic", "pool_pre_ping": false, "pool_recycle_s": 0},
			"control": {"name": "jentic"},
			"registry": {"name": "jentic"}
		},
		"observability": {
			"tracing": {"sample_ratio": 0.0}
		},
		"admin": {
			"auth": {"failed_login_lockout_seconds": 0}
		},
		"control": {
			"connect": {"manual_flows_enabled": false, "rejection_cooldown_hours": 0}
		}
	}`

	var initial generated.BackendConfig
	if err := json.Unmarshal([]byte(inputJSON), &initial); err != nil {
		t.Fatalf("initial json.Unmarshal: %v", err)
	}

	assertZeroOverrides := func(t *testing.T, label string, cfg generated.BackendConfig) {
		t.Helper()
		if cfg.Observability == nil || cfg.Observability.Tracing == nil {
			t.Fatalf("%s: observability.tracing is nil", label)
		}
		if got := cfg.Observability.Tracing.SampleRatio; got != 0.0 {
			t.Errorf("%s: observability.tracing.sample_ratio = %v, want 0.0 (reverted to default?)", label, got)
		}
		if cfg.Admin == nil || cfg.Admin.Auth == nil {
			t.Fatalf("%s: admin.auth is nil", label)
		}
		if got := cfg.Admin.Auth.FailedLoginLockoutSeconds; got != 0 {
			t.Errorf("%s: admin.auth.failed_login_lockout_seconds = %v, want 0 (reverted to default?)", label, got)
		}
		if cfg.Control == nil || cfg.Control.Connect == nil {
			t.Fatalf("%s: control.connect is nil", label)
		}
		if cfg.Control.Connect.ManualFlowsEnabled {
			t.Errorf("%s: control.connect.manual_flows_enabled = true, want false (reverted to default?)", label)
		}
		if got := cfg.Control.Connect.RejectionCooldownHours; got != 0 {
			t.Errorf("%s: control.connect.rejection_cooldown_hours = %v, want 0 (reverted to default?)", label, got)
		}
		if cfg.Databases.Admin.PoolPrePing {
			t.Errorf("%s: databases.admin.pool_pre_ping = true, want false (reverted to default?)", label)
		}
		if got := cfg.Databases.Admin.PoolRecycleS; got != 0 {
			t.Errorf("%s: databases.admin.pool_recycle_s = %v, want 0 (reverted to default?)", label, got)
		}
	}

	assertZeroOverrides(t, "initial unmarshal", initial)

	// JSON round-trip: marshal BackendConfig -> unmarshal into fresh BackendConfig.
	marshalledJSON, err := json.Marshal(&initial)
	if err != nil {
		t.Fatalf("json.Marshal: %v", err)
	}
	var afterJSON generated.BackendConfig
	if err := json.Unmarshal(marshalledJSON, &afterJSON); err != nil {
		t.Fatalf("round-trip json.Unmarshal: %v", err)
	}
	assertZeroOverrides(t, "JSON round-trip", afterJSON)

	// YAML round-trip: marshal BackendConfig to YAML, decode YAML to a generic
	// map and re-encode to JSON so UnmarshalJSON's default-filling logic runs,
	// AND unmarshal YAML directly into BackendConfig.
	marshalledYAML, err := yaml.Marshal(&initial)
	if err != nil {
		t.Fatalf("yaml.Marshal: %v", err)
	}
	var yamlMap map[string]any
	if err := yaml.Unmarshal(marshalledYAML, &yamlMap); err != nil {
		t.Fatalf("yaml.Unmarshal to map: %v", err)
	}
	yamlAsJSON, err := json.Marshal(yamlMap)
	if err != nil {
		t.Fatalf("json.Marshal of YAML map: %v", err)
	}
	var afterYAMLudo generated.BackendConfig
	if err := json.Unmarshal(yamlAsJSON, &afterYAMLudo); err != nil {
		t.Fatalf("round-trip YAML->JSON Unmarshal: %v", err)
	}
	assertZeroOverrides(t, "YAML->UnmarshalJSON round-trip", afterYAMLudo)

	var afterYAMLDirect generated.BackendConfig
	if err := yaml.Unmarshal(marshalledYAML, &afterYAMLDirect); err != nil {
		t.Fatalf("direct yaml.Unmarshal: %v", err)
	}
	assertZeroOverrides(t, "direct YAML round-trip", afterYAMLDirect)

	// Also verify a positive-constrained int field with a non-zero default
	// (jwt_ttl_seconds, default 3600, exclusiveMinimum 0) emits 0 when set to 0
	// rather than dropping the field and silently reloading as 3600.
	initial.Admin.Auth.JwtTtlSeconds = 0
	withZeroJWT, err := json.Marshal(&initial)
	if err != nil {
		t.Fatalf("json.Marshal with jwt_ttl_seconds=0: %v", err)
	}
	var rejected generated.BackendConfig
	if err := json.Unmarshal(withZeroJWT, &rejected); err == nil {
		t.Errorf("expected jwt_ttl_seconds=0 to be preserved in JSON and fail >0 validation on reload, but unmarshal succeeded with jwt_ttl_seconds=%d",
			rejected.Admin.Auth.JwtTtlSeconds)
	}
}

// TestBackendConfig_NonZeroDefaultFieldsHaveNoOmitTags verifies across every
// reachable struct in generated.BackendConfig that no field with a non-zero
// schema default carries omitempty or omitzero on its json/yaml/mapstructure tags.
func TestBackendConfig_NonZeroDefaultFieldsHaveNoOmitTags(t *testing.T) {
	defaults, err := defaultSettings()
	if err != nil {
		t.Fatalf("defaultSettings: %v", err)
	}

	var walk func(rt reflect.Type, defMap map[string]any, prefix string)
	walk = func(rt reflect.Type, defMap map[string]any, prefix string) {
		if rt.Kind() == reflect.Pointer {
			rt = rt.Elem()
		}
		if rt.Kind() != reflect.Struct {
			return
		}
		for i := range rt.NumField() {
			field := rt.Field(i)
			jsonTag := field.Tag.Get("json")
			name := strings.Split(jsonTag, ",")[0]
			if name == "" || name == "-" {
				continue
			}
			path := name
			if prefix != "" {
				path = prefix + "." + name
			}
			ft := field.Type
			if ft.Kind() == reflect.Pointer {
				ft = ft.Elem()
			}
			if ft.Kind() == reflect.Struct {
				subMap, _ := asSettings(defMap[name])
				walk(ft, subMap, path)
				continue
			}
			defVal, hasDef := defMap[name]
			if !hasDef || isZeroDefaultValue(defVal) {
				continue
			}
			for _, tagKey := range []string{"json", "yaml", "mapstructure"} {
				tagVal := field.Tag.Get(tagKey)
				if strings.Contains(tagVal, "omitempty") || strings.Contains(tagVal, "omitzero") {
					t.Errorf("field %s (%s.%s, default=%v) has %s:%q containing omitempty/omitzero",
						path, rt.Name(), field.Name, defVal, tagKey, tagVal)
				}
			}
		}
	}

	walk(reflect.TypeOf(generated.BackendConfig{}), defaults, "")
}

func isZeroDefaultValue(v any) bool {
	if v == nil {
		return true
	}
	switch val := v.(type) {
	case bool:
		return !val
	case float64:
		return val == 0
	case string:
		return val == ""
	case []any:
		return len(val) == 0
	case map[string]any:
		return len(val) == 0
	default:
		return false
	}
}
