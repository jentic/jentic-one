package arch

import (
	"testing"
)

// ARCH-2: intra-internal/cli leaf-layering.
//
// The internal/cli subtree has a deliberate dependency order that keeps the two
// binary command trees (api = `jentic`, ctlcmd = `jenticctl`) buildable in
// isolation and free of import cycles:
//
//	leaves:  clictx, prompt, apispec, binder   (no sibling internal/cli deps)
//	ux:      may use prompt (a leaf) only
//	cmdcore: the SHARED base — may use leaves + ux, but NOT a command tree
//	api / ctlcmd: the binary trees — must not import EACH OTHER
//
// clictx exists specifically to break the cycle that arose when command
// packages and the client-construction helpers referenced each other; if any of
// these leaves regrows a dependency on cmdcore/api/ctl(cmd), the cycle is back.
// The binary-boundary gate already proves the agent surface excludes ctl-only
// packages; this test guards the finer-grained *intra-tree* arrows that gate
// does not express.

// cliLeafPackages are the internal/cli packages that must stay dependency-free
// of every sibling internal/cli command/UX package (they sit at the bottom).
var cliLeafPackages = []string{
	"internal/cli/clictx",
	"internal/cli/prompt",
	"internal/cli/apispec",
	"internal/cli/binder",
}

// cliCommandTrees are sibling internal/cli packages a leaf must never import.
// ux is included because a leaf pulling in the audience/mode layer would invert
// the arrow (ux → prompt, never the reverse) and re-open a cycle risk.
var cliSiblingUpperLayers = []string{
	"internal/cli/cmdcore",
	"internal/cli/api",
	"internal/cli/ctl",
	"internal/cli/ctlcmd",
	"internal/cli/ux",
}

// TestCLILeafPackagesStayLeaves asserts each leaf imports no sibling upper-layer
// internal/cli package (ARCH-2). A leaf may of course import client/..., stdlib,
// and third-party libs; only the sibling command/UX packages are forbidden.
func TestCLILeafPackagesStayLeaves(t *testing.T) {
	pkgs := loadCLI(t)

	var seen int
	for _, p := range pkgs {
		if !underPrefixes(p.PkgPath, cliLeafPackages...) {
			continue
		}
		seen++
		for imp := range p.Imports {
			for _, upper := range cliSiblingUpperLayers {
				if underPrefixes(imp, upper) {
					t.Errorf("leaf-layering: %s imports %q — a leaf package must not depend on a sibling "+
						"command/UX package (ARCH-2); this re-opens the import cycle clictx exists to break",
						rel(p.PkgPath), rel(imp))
				}
			}
		}
	}
	if seen == 0 {
		t.Fatalf("leaf-layering check found no packages under %v — a rename moved the leaves; "+
			"this gate must not pass vacuously (ARCH-2)", cliLeafPackages)
	}
}

// TestCmdcoreDoesNotImportCommandTrees asserts the shared base (cmdcore) never
// imports either binary's command tree (ARCH-2). cmdcore is embedded by BOTH
// `jentic` and `jenticctl`; if it reached up into api/ or ctl(cmd)/, the agent
// binary would transitively link operator-only code (and vice versa) and the
// two trees could no longer be built independently.
func TestCmdcoreDoesNotImportCommandTrees(t *testing.T) {
	pkgs := loadCLI(t)

	forbidden := []string{
		"internal/cli/api",
		"internal/cli/ctl",
		"internal/cli/ctlcmd",
	}
	var seen int
	for _, p := range pkgs {
		if !underPrefixes(p.PkgPath, "internal/cli/cmdcore") {
			continue
		}
		seen++
		for imp := range p.Imports {
			for _, up := range forbidden {
				if underPrefixes(imp, up) {
					t.Errorf("leaf-layering: shared base %s imports %q — cmdcore is embedded by both binaries and "+
						"must not reach into a command tree (ARCH-2)", rel(p.PkgPath), rel(imp))
				}
			}
		}
	}
	if seen == 0 {
		t.Fatal("leaf-layering: no internal/cli/cmdcore packages found — the shared base moved; " +
			"this gate must not pass vacuously (ARCH-2)")
	}
}

// TestCommandTreesDoNotImportEachOther asserts `jentic`'s tree (api) and
// `jenticctl`'s tree (ctl/ctlcmd) never import one another (ARCH-2). This is the
// intra-internal/cli complement to the binary-boundary gate: it holds even for
// helper code that a whole-binary import-graph walk might not attribute to a
// specific main.
func TestCommandTreesDoNotImportEachOther(t *testing.T) {
	pkgs := loadCLI(t)

	ctlTrees := []string{"internal/cli/ctl", "internal/cli/ctlcmd"}
	var seenAPI, seenCtl int
	for _, p := range pkgs {
		switch {
		case underPrefixes(p.PkgPath, "internal/cli/api"):
			seenAPI++
			for imp := range p.Imports {
				if underPrefixes(imp, ctlTrees...) {
					t.Errorf("leaf-layering: agent tree %s imports operator tree %q — the two command trees "+
						"must stay independent (ARCH-2)", rel(p.PkgPath), rel(imp))
				}
			}
		case underPrefixes(p.PkgPath, ctlTrees...):
			seenCtl++
			for imp := range p.Imports {
				if underPrefixes(imp, "internal/cli/api") {
					t.Errorf("leaf-layering: operator tree %s imports agent tree %q — the two command trees "+
						"must stay independent (ARCH-2)", rel(p.PkgPath), rel(imp))
				}
			}
		}
	}
	if seenAPI == 0 || seenCtl == 0 {
		t.Fatalf("leaf-layering: expected both command trees present (api=%d, ctl=%d) — a rename moved one; "+
			"this gate must not pass vacuously (ARCH-2)", seenAPI, seenCtl)
	}
}
