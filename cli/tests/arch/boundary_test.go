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
// surface this boundary protects. client/ lands in Phase 1; until then the test
// is dormant (logged, not vacuously green).
var sdkRoots = []string{"client"}

// forbiddenSDKImports are import prefixes an SDK package may not pull in,
// because doing so would break headless third-party importers or leak the CLI's
// private surface into the public API.
var forbiddenSDKImports = []struct {
	prefix, why string
}{
	{modulePath + "/internal", "SDK must not import CLI-private internal/ packages"},
	{"github.com/spf13/cobra", "SDK must not depend on the Cobra CLI framework"},
	{"github.com/charmbracelet", "SDK must not depend on UI libraries (bubbletea/lipgloss/huh)"},
}

// Test1A_SDKBoundary asserts client/... and the public composition packages
// import nothing from internal/..., cobra, or charmbracelet.
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
		t.Logf("dormant: no SDK packages present yet (%v) — activates when Phase 1 lands", sdkRoots)
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
		"internal/cmd/output.go":  true, // jsonOrPretty TTY probe + shipped render seam
		"internal/cmd/header.go":  true, // term.IsTerminal(os.Stdout.Fd()) — TTY detection
		"internal/cmd/run.go":     true, // child-process c.Stdout = os.Stdout (must be real fd)
		"internal/cmd/root.go":    true, // AppContainer{Out: os.Stdout} — the bootstrap capture point
		"internal/cmd/install.go": true, // banner writer handed to an install helper
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
