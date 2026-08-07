package arch

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// vendoredSpecs are the module-relative locations the Phase-1 codegen vendors
// the two OpenAPI specs to (impl/2.1). 1G/1H read these, not the source specs,
// because go:embed can only see files under the embedding package.
var vendoredSpecs = []string{
	"client/generated/control/spec.yaml",
	"client/generated/broker/spec.yaml",
}

// specsPresent returns the existing vendored-spec paths (Phase 1 artifact).
func specsPresent() (paths []string, ok bool) {
	for _, s := range vendoredSpecs {
		p := filepath.Join("..", "..", s) // tests/arch (test CWD) -> cli/
		if _, err := os.Stat(p); err == nil {
			paths = append(paths, p)
		}
	}
	return paths, len(paths) > 0
}

// curatedRegistryPresent reports whether the Phase-2 curated-command registry
// exists yet. 1G reflects over that registry (command -> generated struct ->
// notExposed), so its real dependency is the registry itself, NOT merely the
// presence of the internal/cli/api tree. Phase 8 relocated the shipped command
// tree into internal/cli/api (the binary split) WITHOUT introducing the curated
// registry, so a bare directory probe would trip 1G spuriously. We therefore
// probe for the registry artifact — a non-test `curated.go` in the api package
// that declares the command->struct->notExposed table — which is what impl/7.0
// §2 actually lands. Until that file appears, 1G has nothing to reflect over.
func curatedRegistryPresent() bool {
	// The curated-command registry lands as internal/cli/api/curated.go
	// (impl/7.0 §2, Phase 2). Its presence is the signal that there are curated
	// structs to reflect over. Until then 1G has nothing to check — note the
	// mere existence of the internal/cli/api package (Phase 8 binary split) is
	// NOT the signal.
	if _, err := os.Stat(filepath.Join("..", "..", "internal", "cli", "api", "curated.go")); err == nil {
		return true
	}
	return false
}

// Test1G_SpecFlagCoverageParity ensures every exported field of a curated
// command's generated params/body struct is either bound to a flag or listed in
// that command's notExposed set (impl/0.0 §1G). The compiler catches spec
// renames/removals; this catches *additions* — a new optional param that a
// curated command would otherwise silently never expose.
//
// Activation: needs the curated-command registry (command -> struct type ->
// notExposed), a Phase-2 artifact (internal/cli/api). The vendored specs alone
// (Phase 1) are NOT enough — there are no curated commands to reflect over yet —
// so this stays dormant until that tree lands, then fails loud until upgraded.
func Test1G_SpecFlagCoverageParity(t *testing.T) {
	if !curatedRegistryPresent() {
		t.Skip("dormant: curated-command registry (internal/cli/api, Phase 2) not present — no commands to reflect over yet")
	}
	// Phase 2: reflect over each entry in the curated-command registry,
	// asserting every json field name is in the flag set or notExposed, and
	// that no notExposed entry references a since-removed field.
	t.Fatal("curated-command registry is present but Test1G_SpecFlagCoverageParity was not upgraded to reflect over it — see impl/0.0 §1G and impl/2.1 §3a/§4")
}

// sensitiveKeyExact mirrors impl/3.1 §1's exact-key allowlist. Kept in sync with
// the redaction engine's isSensitiveKey (a divergence would mean the sweep and
// the runtime redactor disagree on what "secret-shaped" means).
var sensitiveKeyExact = map[string]bool{
	"authorization": true,
	"x-api-key":     true,
	"jwt":           true,
	"assertion":     true,
	"cookie":        true,
	"set-cookie":    true,
	"password":      true,
	"passwd":        true,
	"secret":        true,
	"token":         true,
	"api_key":       true,
	"apikey":        true,
	"private_key":   true,
	"privatekey":    true,
	"signing_key":   true,
	"signingkey":    true,
	"credential":    true,
	"credentials":   true,
}

// sensitiveKeySuffixes mirrors impl/3.1 §1's suffix rules.
var sensitiveKeySuffixes = []string{
	"_token", "_secret", "_password", "_passwd", "_api_key", "_apikey",
	"_private_key", "_privatekey", "_signing_key", "_credential", "_credentials",
}

// isSensitiveKey reports whether a property name is secret-shaped per impl/3.1
// §1, normalizing camelCase to snake_case first.
func isSensitiveKey(key string) bool {
	k := camelToSnake(key)
	if sensitiveKeyExact[k] {
		return true
	}
	for _, suf := range sensitiveKeySuffixes {
		if strings.HasSuffix(k, suf) {
			return true
		}
	}
	return false
}

// camelToSnake is the acronym-aware normalizer from impl/3.1 §1.
func camelToSnake(key string) string {
	rs := []rune(key)
	var b strings.Builder
	for i, r := range rs {
		if r >= 'A' && r <= 'Z' {
			prevLower := i > 0 && (rs[i-1] >= 'a' && rs[i-1] <= 'z' || rs[i-1] >= '0' && rs[i-1] <= '9')
			nextLower := i+1 < len(rs) && rs[i+1] >= 'a' && rs[i+1] <= 'z'
			prevUpper := i > 0 && rs[i-1] >= 'A' && rs[i-1] <= 'Z'
			if prevLower || (prevUpper && nextLower) {
				b.WriteByte('_')
			}
			r += 'a' - 'A'
		}
		b.WriteRune(r)
	}
	return strings.ReplaceAll(strings.ToLower(b.String()), "__", "_")
}

// sensitiveSweepAllowlist is the reviewed false-positive / pending-annotation set
// for Test1H_SensitiveAnnotationSweep. A property name here is exempt from the
// "must carry x-sensitive: true" requirement. Two kinds of entries live here:
//
//  1. GENUINE false positives — the name is secret-shaped but the field is not a
//     secret (a boolean flag, a count, a masked/redacted view). These stay
//     allowlisted permanently.
//  2. PRE-EXISTING unannotated secrets — real secret-shaped fields already in the
//     control spec at the time Phase 1 landed. The backend `x-sensitive`
//     annotation pass (impl/2.1 §4b) is a separate, not-yet-scheduled deliverable;
//     until it lands these are protected by redaction layers 2 (key heuristics)
//     and 3 (byte backstop), NOT layer 1. They are allowlisted so the sweep
//     hard-fails only on NEWLY-introduced secret-shaped fields — a new field can
//     never ship un-classified. FOLLOW-UP: annotate these in the backend Pydantic
//     models and delete them from this list, shrinking it toward only (1).
var sensitiveSweepAllowlist = map[string]bool{
	// (1) Genuine false positives: booleans/flags, not secrets.
	"has_api_key":          true, // presence flag, not the key
	"must_change_password": true, // policy boolean
	"clear_session_token":  true, // "clear the token?" boolean directive

	// (2) Pre-existing unannotated secret-shaped fields in the control spec as of
	// Phase 1. FOLLOW-UP: add `x-sensitive: true` in the backend models and remove
	// from this list. Covered by redaction layers 2/3 in the meantime.
	"access_token":              true,
	"api_key":                   true,
	"assertion":                 true,
	"client_secret":             true,
	"credential":                true,
	"current_password":          true,
	"id_token":                  true,
	"invite_token":              true,
	"new_password":              true,
	"password":                  true,
	"refresh_token":             true,
	"registration_access_token": true,
	"secret":                    true,
	"session_token":             true,
	"token":                     true,
}

// Test1H_SensitiveAnnotationSweep walks every schema property in the vendored
// specs and fails on any secret-shaped property name (per impl/3.1 §1's
// exact-key/suffix heuristics) that lacks `x-sensitive: true` and is not in the
// reviewed allowlist (impl/0.0 §1H). This makes someone classify every NEW
// secret-shaped field before it can ship, so Layer-1 typed redaction is driven
// by the spec, not by naming luck.
//
// Activation: needs the vendored specs (Phase 1) and the shared isSensitiveKey
// heuristic (mirrored above from impl/3.1 §1). Both exist, so this runs for real.
func Test1H_SensitiveAnnotationSweep(t *testing.T) {
	paths, ok := specsPresent()
	if !ok {
		t.Skip("dormant: vendored specs (Phase 1) not present — no schema properties to sweep")
	}

	type finding struct{ spec, schema, prop string }
	var unclassified []finding

	for _, p := range paths {
		data, err := os.ReadFile(p)
		if err != nil {
			t.Fatalf("read spec %s: %v", p, err)
		}
		var doc struct {
			Components struct {
				Schemas map[string]struct {
					Properties map[string]map[string]any `yaml:"properties"`
				} `yaml:"schemas"`
			} `yaml:"components"`
		}
		if err := yaml.Unmarshal(data, &doc); err != nil {
			t.Fatalf("parse spec %s: %v", p, err)
		}
		for schemaName, schema := range doc.Components.Schemas {
			for prop, propSchema := range schema.Properties {
				if !isSensitiveKey(prop) {
					continue
				}
				if xSensitiveTrue(propSchema) {
					continue // properly annotated — good
				}
				if sensitiveSweepAllowlist[prop] {
					continue // reviewed false positive / pending backend annotation
				}
				unclassified = append(unclassified, finding{spec: filepath.Base(filepath.Dir(p)), schema: schemaName, prop: prop})
			}
		}
	}

	if len(unclassified) > 0 {
		msgs := make([]string, 0, len(unclassified))
		for _, f := range unclassified {
			msgs = append(msgs, f.spec+" "+f.schema+"."+f.prop)
		}
		sort.Strings(msgs)
		t.Fatalf("secret-shaped spec properties without `x-sensitive: true` and not allowlisted (impl/0.0 §1H, impl/2.1 §4b):\n  %s\n"+
			"Fix: annotate the field with `x-sensitive: true` in the backend Pydantic model, OR add it to sensitiveSweepAllowlist "+
			"with a one-line reason if it is a genuine false positive.", strings.Join(msgs, "\n  "))
	}
}

// xSensitiveTrue reports whether a property schema carries `x-sensitive: true`.
func xSensitiveTrue(propSchema map[string]any) bool {
	v, ok := propSchema["x-sensitive"]
	if !ok {
		return false
	}
	b, ok := v.(bool)
	return ok && b
}
