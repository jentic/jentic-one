package arch

import (
	"strings"
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
// imports either binary's command tree, nor the local-agent feature package
// (ARCH-1/ARCH-2). cmdcore is embedded by BOTH `jentic` and `jenticctl`; if it
// reached up into api/, ctl(cmd)/, or localagentcmd/, the shared base would
// depend on its own consumers — inverting the arrow (localagentcmd → cmdcore)
// and re-linking operator/agent-only code into every binary.
func TestCmdcoreDoesNotImportCommandTrees(t *testing.T) {
	pkgs := loadCLI(t)

	forbidden := []string{
		"internal/cli/api",
		"internal/cli/ctl",
		"internal/cli/ctlcmd",
		"internal/cli/localagentcmd",
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

// agentopsForbiddenImports are the direct imports internal/agentops must never
// grow (phase-0 §0.2 purity constraints): no cobra (command wiring), no os
// (stdin/TTY/env/exit-code logic stays caller-side — under stdio MCP, stdin is
// the JSON-RPC wire), no terminal detection. Prefix-matched so subpackages
// (e.g. os/exec) are fenced too.
var agentopsForbiddenImports = []string{
	"github.com/spf13/cobra",
	"github.com/charmbracelet/x/term",
	"os",
}

// TestAgentopsStaysUXFree fences the extracted execute/inspect core
// (internal/agentops): it must not import cobra, os, or the terminal detector
// (agentopsForbiddenImports), and the ONLY internal/cli sibling it may use is
// ux — the sanctioned CodedError/envelope/directive types (plan 0.2). This
// turns the package doc's prose dependency rules into an enforced gate: an
// agentops → cmdcore/api/ctl(cmd) import would re-fuse the UX-free core to a
// command tree, and an os/term import would smuggle the stdin/TTY logic back
// in. (agentops → ux → theme is deliberate and stays allowed.)
func TestAgentopsStaysUXFree(t *testing.T) {
	pkgs := loadCLI(t)

	var seen int
	for _, p := range pkgs {
		if !underPrefixes(p.PkgPath, "internal/agentops") {
			continue
		}
		seen++
		for imp := range p.Imports {
			for _, forbidden := range agentopsForbiddenImports {
				if imp == forbidden || strings.HasPrefix(imp, forbidden+"/") {
					t.Errorf("agentops fence: %s imports %q — the UX-free core must not depend on "+
						"cobra/os/term (phase-0 §0.2); keep flag/stdin/TTY/exit logic caller-side",
						rel(p.PkgPath), imp)
				}
			}
			if underPrefixes(imp, "internal/cli") && !underPrefixes(imp, "internal/cli/ux") {
				t.Errorf("agentops fence: %s imports %q — the only internal/cli package the core may "+
					"use is ux (the CodedError/envelope contract types); anything more re-fuses the "+
					"core to the command layer (phase-0 §0.2)",
					rel(p.PkgPath), rel(imp))
			}
		}
	}
	if seen == 0 {
		t.Fatal("agentops fence found no internal/agentops packages — the core moved; " +
			"this gate must not pass vacuously (phase-0 §0.2)")
	}
}
