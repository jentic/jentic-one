package arch

import (
	"os"
	"path/filepath"
	"testing"
)

// vendoredSpecs are the module-relative locations the Phase-1 codegen vendors
// the two OpenAPI specs to (impl/2.1). 1G/1H read these, not the source specs,
// because go:embed can only see files under the embedding package.
var vendoredSpecs = []string{
	"client/generated/control/spec.yaml",
	"client/generated/broker/spec.yaml",
}

// specsPresent reports whether the vendored specs exist yet (Phase 1 artifact).
func specsPresent() (paths []string, ok bool) {
	for _, s := range vendoredSpecs {
		p := filepath.Join("..", "..", s) // tests/arch (test CWD) -> cli/
		if _, err := os.Stat(p); err == nil {
			paths = append(paths, p)
		}
	}
	return paths, len(paths) > 0
}

// Test1G_SpecFlagCoverageParity ensures every exported field of a curated
// command's generated params/body struct is either bound to a flag or listed in
// that command's notExposed set (impl/0.0 §1G). The compiler catches spec
// renames/removals; this catches *additions* — a new optional param that a
// curated command would otherwise silently never expose.
//
// Activation: needs the generated structs and the curated-command registry
// (command -> struct type -> notExposed), both Phase 1/2 artifacts. Dormant
// until the vendored specs and generated client exist.
func Test1G_SpecFlagCoverageParity(t *testing.T) {
	if _, ok := specsPresent(); !ok {
		t.Skip("dormant: vendored specs / generated client (Phase 1) not present — nothing to reflect over yet")
	}
	// Phase 1/2: reflect over each entry in the curated-command registry,
	// asserting every json field name is in the flag set or notExposed, and
	// that no notExposed entry references a since-removed field.
	t.Fatal("vendored specs are present but Test1G_SpecFlagCoverageParity was not upgraded to reflect over the curated-command registry — see impl/0.0 §1G and impl/2.1 §3a/§4")
}

// Test1H_SensitiveAnnotationSweep walks every schema property in the vendored
// specs and fails on any secret-shaped property name (per impl/3.1 §1's
// exact-key/suffix heuristics) that lacks `x-sensitive: true` and is not in the
// reviewed false-positive allowlist (impl/0.0 §1H). This makes someone classify
// every new secret-shaped field before it can ship, so Layer-1 typed redaction
// is driven by the spec, not by naming luck.
//
// Activation: needs the vendored specs (Phase 1) and the shared isSensitiveKey
// heuristic (Phase 2/3, impl/3.1 §1). Dormant until both exist.
func Test1H_SensitiveAnnotationSweep(t *testing.T) {
	paths, ok := specsPresent()
	if !ok {
		t.Skip("dormant: vendored specs (Phase 1) not present — no schema properties to sweep")
	}
	_ = paths
	// Phase 1+: parse each spec, walk schema properties, apply isSensitiveKey,
	// and fail on unannotated, un-allowlisted matches.
	t.Fatal("vendored specs are present but Test1H_SensitiveAnnotationSweep was not upgraded to walk schema properties with isSensitiveKey — see impl/0.0 §1H, impl/3.1 §1, impl/2.1 §4b")
}
