package arch

import (
	"testing"

	"golang.org/x/tools/go/packages"
)

// operatorOnlyPkgs are the operator/installer packages that must belong to the
// jenticctl lifecycle tool ALONE. The jentic (agent/API) binary is a pure data
// plane — it talks to an already-running control plane — so it must never link
// Docker/compose orchestration, process supervision, or self-update machinery.
// This is the binary decoupling of 06 §1 / 07 §3 (impl/1.1 §1a): a `jentic`
// build stays small and unprivileged, and an installer regression can't leak
// into the agent surface.
var operatorOnlyPkgs = []string{
	modulePath + "/internal/install",
	modulePath + "/internal/proc",
	modulePath + "/internal/update",
}

// agentSurfaceRoots are the module-relative package trees that make up the
// `jentic` binary: its command tree (internal/cli/api) and the shared base it
// embeds (internal/cli/cmdcore). cmd/jentic itself is checked directly below.
// If ANY package under these roots pulls in an operator-only package
// (transitively), the agent binary would link the installer — the exact
// deviation Phase 8 exists to correct and lock down.
var agentSurfaceRoots = []string{"internal/cli/api", "internal/cli/cmdcore"}

// transitiveImports returns the full in-module + operator-package import closure
// of p, keyed by import path. packages.Package.Imports is direct-only, so we
// walk it. We only need to detect operator packages, but we recurse through the
// whole graph so a deep/indirect edge (api -> X -> ... -> internal/install) is
// still caught.
func transitiveImports(p *packages.Package) map[string]bool {
	seen := map[string]bool{}
	var walk func(cur *packages.Package)
	walk = func(cur *packages.Package) {
		for path, dep := range cur.Imports {
			if seen[path] {
				continue
			}
			seen[path] = true
			walk(dep)
		}
	}
	walk(p)
	return seen
}

// firstOperatorImport reports the first operator-only package present in imps,
// or "" if none. Deterministic order isn't needed — any hit fails the test.
func firstOperatorImport(imps map[string]bool) string {
	for _, op := range operatorOnlyPkgs {
		if imps[op] {
			return op
		}
	}
	return ""
}

// Test8_AgentBinaryExcludesOperatorPackages is the Phase 8 binary-boundary gate
// (F8-1; impl/1.1 §1a, 06 §1). It proves — by import-graph analysis, not prose —
// that the jentic agent surface (cmd/jentic and everything under
// internal/cli/api + internal/cli/cmdcore) never links the operator-only
// installer packages. Before Phase 8 the flat internal/cmd package forced the
// jentic binary to link internal/install et al.; the api/ctl decomposition fixed
// that, and this gate keeps it fixed: re-introducing an installer import into
// the agent tree (e.g. a "convenient" install.Foo call in an api command) fails
// here loudly instead of silently re-bloating and re-privileging the agent
// binary.
func Test8_AgentBinaryExcludesOperatorPackages(t *testing.T) {
	pkgs := loadCLI(t)

	// (a) The agent command surface: every package under api/ + cmdcore/.
	var surfaceSeen int
	for _, p := range pkgs {
		if !underPrefixes(p.PkgPath, agentSurfaceRoots...) {
			continue
		}
		surfaceSeen++
		if op := firstOperatorImport(transitiveImports(p)); op != "" {
			t.Errorf("agent surface: %s transitively imports operator-only package %q — "+
				"the jentic binary must not link installer/proc/update code (06 §1, impl/1.1 §1a); "+
				"move the shared logic to a neutral package or gate it behind the jenticctl (ctlcmd) tree",
				rel(p.PkgPath), rel(op))
		}
	}
	if surfaceSeen == 0 {
		t.Fatalf("no packages found under %v — the agent command surface is missing or was moved; "+
			"this gate must not pass vacuously", agentSurfaceRoots)
	}

	// (b) The jentic main package itself — the actual linked binary. This is the
	// ground truth: even if the surface roots moved, cmd/jentic is what ships.
	var jenticSeen bool
	for _, p := range pkgs {
		if rel(p.PkgPath) != "cmd/jentic" {
			continue
		}
		jenticSeen = true
		if op := firstOperatorImport(transitiveImports(p)); op != "" {
			t.Errorf("cmd/jentic transitively links operator-only package %q — the agent binary "+
				"must be installer-free (06 §1, impl/1.1 §1a)", rel(op))
		}
	}
	if !jenticSeen {
		t.Fatal("cmd/jentic package not found — cannot verify the agent binary boundary")
	}

	// (c) Sanity/anti-vacuity: jenticctl SHOULD link the operator packages. If it
	// doesn't, either the operator paths above are wrong or the lifecycle tool
	// stopped doing its job — either way the gate above would be meaningless.
	for _, p := range pkgs {
		if rel(p.PkgPath) != "cmd/jenticctl" {
			continue
		}
		if firstOperatorImport(transitiveImports(p)) == "" {
			t.Errorf("cmd/jenticctl links NONE of %v — the operator-package list is stale, "+
				"which would make Test8 pass vacuously; fix operatorOnlyPkgs", operatorOnlyPkgsRel())
		}
	}
}

// operatorOnlyPkgsRel is the module-relative form of operatorOnlyPkgs for
// readable failure messages.
func operatorOnlyPkgsRel() []string {
	out := make([]string, 0, len(operatorOnlyPkgs))
	for _, p := range operatorOnlyPkgs {
		out = append(out, rel(p))
	}
	return out
}
