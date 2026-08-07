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
	case underPrefixes(pkgPath, "internal/cmd") && base == "output.go":
		return true // shipped jsonOrPretty/render seam, pre-UX-package
	}
	return false
}

// Test1B_NakedPrint fails on direct fmt.Print*/println/os.Stdout writes and
// log.Fatal* in command packages, which would corrupt strict agent JSON.
//
// Phase-0 reality: the shipped internal/cmd tree predates the ux.Render seam and
// still prints directly in many files. Failing on all of it now would block the
// guardrail from landing before the refactor it is meant to protect. So this
// test enforces the rule strictly on the *new* V2 command packages
// (internal/cli/api, internal/cli/context) and, for the legacy internal/cmd
// tree, asserts a ratchet: the count of naked prints must not exceed a recorded
// baseline, so the migration can only reduce it. Each re-plumbed command drops
// the baseline; when it hits zero, internal/cmd graduates to strict.
func Test1B_NakedPrint(t *testing.T) {
	pkgs := loadCLI(t)

	strictRoots := []string{"internal/cli/api", "internal/cli/context"}
	legacyRoots := []string{"internal/cmd"}

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
		t.Errorf("legacy naked-print ratchet regressed: internal/cmd has %d naked prints, baseline is %d.\n"+
			"New command code must use ux.Render. If you legitimately reduced this, lower legacyBaseline in ratchet_baseline.go to %d.",
			total, legacyBaseline, total)
	}
	if total < legacyBaseline {
		t.Logf("legacy naked-print count dropped to %d (baseline %d) — lower legacyBaseline to lock in the win", total, legacyBaseline)
	}
}
