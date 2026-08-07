package arch

import (
	"go/ast"
	"strings"
	"testing"

	"golang.org/x/tools/go/packages"
)

// nakedPrintAllowed exempts bootstrap/entry files where a direct print is
// legitimate (e.g. a pre-Cobra fatal, or the shipped output seam).
func nakedPrintAllowed(pkgPath, file string) bool {
	base := baseName(file)
	switch {
	case underPrefixes(pkgPath, "internal/cli/ux"):
		return true // the render layer is where printing happens
	case base == "main.go":
		return true
	case underPrefixes(pkgPath, "internal/cli/cmdcore") && base == "output.go":
		return true // shipped jsonOrPretty/render seam, pre-UX-package
	}
	return false
}

// Test1B_NakedPrint fails on direct fmt.Print*/println/os.Stdout writes and
// log.Fatal* in command packages, which would corrupt strict agent JSON.
//
// Phase-8 reality: the shipped command tree (formerly the flat internal/cmd
// package) was decomposed into internal/cli/{cmdcore,api,ctlcmd}. It already
// routes all output through the App.Out/App.Err writers, so this rule is
// effectively strict on those packages; the ratchet holds the count at the
// recorded baseline (0) so the migration can only reduce it. Each re-plumbed
// command keeps the baseline at zero; growth fails the guardrail.
func Test1B_NakedPrint(t *testing.T) {
	pkgs := loadCLI(t)

	strictRoots := []string{}
	legacyRoots := []string{"internal/cli/cmdcore", "internal/cli/api", "internal/cli/ctlcmd"}

	countIn := func(roots []string) map[string]int {
		perFile := map[string]int{}
		forEachFile(pkgs, func(pkgPath string) bool {
			return underPrefixes(pkgPath, roots...)
		}, func(p *packages.Package, file *ast.File, path string) {
			if nakedPrintAllowed(p.PkgPath, path) {
				return
			}
			n := 0
			n += len(selectorPrefixCalls(p.TypesInfo, p.Fset, file, "fmt", func(s string) bool {
				return strings.HasPrefix(s, "Print") // Print, Printf, Println
			}))
			n += len(selectorPrefixCalls(p.TypesInfo, p.Fset, file, "log", func(s string) bool {
				return strings.HasPrefix(s, "Fatal") || strings.HasPrefix(s, "Print")
			}))
			// os.Stdout is governed by the stdout/stderr boundary test (1F),
			// which has the precise render/bootstrap/subprocess allowlist. We do
			// not re-count it here or the two rules would disagree on the legit
			// TTY-probe / child-stdio / AppContainer uses.
			if n > 0 {
				perFile[rel(p.PkgPath)+"/"+baseName(path)] = n
			}
		})
		return perFile
	}

	// Strict roots: any violation fails.
	strict := countIn(strictRoots)
	seenStrict := 0
	forEachFile(pkgs, func(pkgPath string) bool { return underPrefixes(pkgPath, strictRoots...) },
		func(*packages.Package, *ast.File, string) { seenStrict++ })
	for file, n := range strict {
		t.Errorf("naked print in V2 command file %s (%d occurrences) — route through ux.Render", file, n)
	}
	if seenStrict == 0 {
		t.Logf("dormant: no V2 command packages yet (%v) — strict rule activates as commands migrate", strictRoots)
	}

	// Legacy root: ratchet. Never let the count grow past the baseline.
	legacy := countIn(legacyRoots)
	total := 0
	for _, n := range legacy {
		total += n
	}
	if total > legacyBaseline {
		t.Errorf("legacy naked-print ratchet regressed: internal/cli command tree has %d naked prints, baseline is %d.\n"+
			"New command code must use ux.Render. If you legitimately reduced this, lower legacyBaseline in ratchet_baseline.go to %d.",
			total, legacyBaseline, total)
	}
	if total < legacyBaseline {
		t.Logf("legacy naked-print count dropped to %d (baseline %d) — lower legacyBaseline to lock in the win", total, legacyBaseline)
	}
}
