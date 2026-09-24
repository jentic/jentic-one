package arch

import (
	"go/ast"
	"strings"
	"testing"

	"golang.org/x/tools/go/packages"
)

// sdkRoots are the module-relative prefixes that form the public, cobra-free
// API SDK surface (impl/0.0 §1A, impl/7.0 §2/§3). They must never depend on
// CLI-private packages or UI/CLI frameworks.
//
// NOTE: pkg/core and pkg/clitree are deliberately NOT here. They are the public
// *command-tree composition* layer (impl/7.0 §3): they compose the Cobra tree
// for downstream binaries and therefore legitimately import cobra and the
// internal command implementations. Only client/... is the framework-free API
// surface this boundary protects.
var sdkRoots = []string{"client"}

// forbiddenSDKImports are import prefixes an SDK package may not pull in,
// because doing so would break headless third-party importers or leak the CLI's
// private surface into the public API (impl/7.0 §7.1). The internal/ ban already
// covers internal/cli/ux and internal/theme transitively; the explicit ux/theme
// entries below are belt-and-suspenders so a future non-internal home for either
// still trips the gate ("Mode and Theme never leak into the SDK", §2).
var forbiddenSDKImports = []struct {
	prefix, why string
}{
	{modulePath + "/internal", "SDK must not import CLI-private internal/ packages"},
	{modulePath + "/internal/cli/ux", "SDK must not import the UX audience/mode layer"},
	{modulePath + "/internal/theme", "SDK must not import the CLI theme layer"},
	{modulePath + "/pkg", "SDK (client/) is the lowest layer: pkg/ depends on client/, never the reverse (impl/7.0 §2)"},
	{"github.com/spf13/cobra", "SDK must not depend on the Cobra CLI framework"},
	{"github.com/charmbracelet", "SDK must not depend on UI libraries (bubbletea/lipgloss/huh)"},
}

// Test1A_SDKBoundary asserts client/... imports nothing from internal/...,
// cobra, charmbracelet, or the ux/theme UX layers — the public-SDK contract of
// impl/7.0 §7. It fails (never silently passes) if no SDK packages are found, so
// a rename that moves client/ out from under sdkRoots can't turn the gate
// vacuous.
func Test1A_SDKBoundary(t *testing.T) {
	pkgs := loadCLI(t)

	var seen int
	for _, p := range pkgs {
		if !underPrefixes(p.PkgPath, sdkRoots...) {
			continue
		}
		seen++
		for imp := range p.Imports {
			for _, f := range forbiddenSDKImports {
				if imp == f.prefix || strings.HasPrefix(imp, f.prefix+"/") {
					t.Errorf("SDK boundary: %s imports %q — %s", rel(p.PkgPath), imp, f.why)
				}
			}
		}
	}

	if seen == 0 {
		t.Fatalf("SDK boundary check found no packages under %v — the public client/ surface "+
			"is missing or was moved; this gate must not pass vacuously (impl/7.0 §7)", sdkRoots)
	}
}

// pkgRoots are the public command-tree *composition* packages (impl/7.0 §3):
// downstream binaries (e.g. the enterprise CLI) embed and extend the Cobra
// tree through them. Unlike client/, they MAY import cobra and internal command
// implementations; the one hard rule is the layering direction below.
var pkgRoots = []string{"pkg"}

// Test7_CompositionLayerLayering enforces the impl/7.0 §2 dependency arrow for
// the public composition tree: `pkg/ depends on client/, never the reverse`.
// The client↛pkg half is covered by the SDK boundary gate (pkg is a forbidden
// SDK import); this test guards the other properties — that the composition
// packages actually exist (so the public surface can't be silently deleted) and
// that they don't smuggle in the UX/theme layer the SDK forbids either. Cobra
// and internal/ imports ARE allowed here by design.
func Test7_CompositionLayerLayering(t *testing.T) {
	pkgs := loadCLI(t)

	forbidden := []struct{ prefix, why string }{
		{modulePath + "/internal/cli/ux", "the public composition layer must not surface the UX audience/mode layer"},
		{modulePath + "/internal/theme", "the public composition layer must not surface the CLI theme layer"},
	}

	var seen int
	for _, p := range pkgs {
		if !underPrefixes(p.PkgPath, pkgRoots...) {
			continue
		}
		seen++
		for imp := range p.Imports {
			for _, f := range forbidden {
				if imp == f.prefix || strings.HasPrefix(imp, f.prefix+"/") {
					t.Errorf("composition layer: %s imports %q — %s", rel(p.PkgPath), imp, f.why)
				}
			}
		}
	}

	if seen == 0 {
		t.Fatalf("no packages found under %v — the public pkg/ composition surface is missing "+
			"or was moved; this gate must not pass vacuously (impl/7.0 §3)", pkgRoots)
	}
}

// Test1F_StdoutStderrBoundary enforces two rules (impl/0.0 §1F):
//   - No production package may import the unstructured stdlib "log"; only
//     "log/slog" is permitted (slog writes to stderr by construction).
//   - Direct os.Stdout references are confined to the UX render layer and
//     bootstrap entrypoints; everything else must route output through the
//     Audience/Render seam so the JSON pipeline can't be corrupted.
func Test1F_StdoutStderrBoundary(t *testing.T) {
	pkgs := loadCLI(t)

	// (a) stdlib "log" ban.
	for _, p := range pkgs {
		if !strings.HasPrefix(p.PkgPath, modulePath) {
			continue
		}
		for imp := range p.Imports {
			if imp == "log" {
				t.Errorf("%s imports stdlib \"log\"; use \"log/slog\" (stderr-only, structured)", rel(p.PkgPath))
			}
		}
	}

	// os.Stdout confinement. Allowed in the render layer, bootstrap entrypoints,
	// and a small allowlist of legacy internal/cmd files whose os.Stdout use is
	// NOT application output: TTY probes, subprocess stdio wiring, and the
	// AppContainer bootstrap that captures stdout INTO the render seam. As the
	// V2 refactor moves these, the allowlist shrinks; new code in the V2 command
	// packages gets no exemption.
	legacyStdoutAllowlist := map[string]bool{
		"internal/cli/cmdcore/output.go":    true, // jsonOrPretty TTY probe + shipped render seam
		"internal/cli/cmdcore/header.go":    true, // term.IsTerminal(os.Stdout.Fd()) — TTY detection
		"internal/theme/resolve.go":         true, // term.IsTerminal(os.Stdout.Fd()) — auto no-color TTY probe (OPS-1)
		"internal/cli/localagentcmd/run.go": true, // child-process c.Stdout = os.Stdout (must be real fd)
		"internal/cli/cmdcore/root.go":      true, // AppContainer{Out: os.Stdout} — the bootstrap capture point
		"internal/cli/ctlcmd/install.go":    true, // banner writer handed to an install helper
	}
	osStdoutAllowed := func(pkgPath, file string) bool {
		if underPrefixes(pkgPath, "internal/cli/ux") {
			return true
		}
		if underPrefixes(pkgPath, "cmd/jentic", "cmd/jenticctl", "cmd/clidocs") {
			return true
		}
		return legacyStdoutAllowlist[rel(pkgPath)+"/"+baseName(file)]
	}

	forEachFile(pkgs, func(pkgPath string) bool {
		return strings.HasPrefix(pkgPath, modulePath)
	}, func(p *packages.Package, file *ast.File, path string) {
		if osStdoutAllowed(p.PkgPath, path) {
			return
		}
		for _, line := range selectorCalls(p.TypesInfo, p.Fset, file, "os", "Stdout") {
			t.Errorf("%s:%d: direct os.Stdout outside the render/bootstrap layer — route through ux.Render",
				rel(p.PkgPath)+"/"+baseName(path), line)
		}
	})
}
