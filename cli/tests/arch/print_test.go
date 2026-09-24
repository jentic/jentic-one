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
// Phase-8 reality (impl/0.0 §1B, F8-37): the shipped command tree
// (internal/cli/{cmdcore,api,ctlcmd})
// routes all output through App.Out/App.Err. The recorded baseline is
// 0, so these packages are STRICT roots: any naked print fails outright (the
// intended §1B behavior), not merely a growth ratchet against a non-zero legacy
// count. The legacy ratchet is retained only as an inert backstop (empty root set)
// documenting that no grandfathered violations remain.
func Test1B_NakedPrint(t *testing.T) {
	pkgs := loadCLI(t)

	// The V2 command tree is strict — it ships clean (baseline 0) and must stay so.
	strictRoots := []string{"internal/cli/cmdcore", "internal/cli/api", "internal/cli/ctlcmd", "internal/cli/localagentcmd"}
	// No grandfathered packages remain; kept empty so the ratchet arm is inert but
	// present (a future legacy import could be added here with a non-zero baseline).
	legacyRoots := []string{}

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
		t.Fatalf("no files scanned under the V2 command roots %v — the strict naked-print gate "+
			"must not pass vacuously (did the package layout change?)", strictRoots)
	}

	// Legacy root: ratchet. Never let the count grow past the baseline. With no
	// legacy roots configured this arm is inert (total stays 0 == baseline).
	legacy := countIn(legacyRoots)
	total := 0
	for _, n := range legacy {
		total += n
	}
	if total > legacyBaseline {
		t.Errorf("legacy naked-print ratchet regressed: %d naked prints, baseline is %d.\n"+
			"New command code must use ux.Render. If you legitimately reduced this, lower legacyBaseline in ratchet_baseline.go to %d.",
			total, legacyBaseline, total)
	}
}

// slogSetDefaultAllowed is the single file permitted to call slog.SetDefault:
// the interceptor's diagnostics bootstrap (impl/3.2 §2d — "Nothing else in the
// process may call slog.SetDefault"). Concentrating it there guarantees every
// log line carries the mode-appropriate, redacted handler; a stray SetDefault
// elsewhere (e.g. an init() with a plain handler) would silently re-open the
// stdout/redaction contract.
func slogSetDefaultAllowed(pkgPath, file string) bool {
	return underPrefixes(pkgPath, "internal/cli/cmdcore") && baseName(file) == "slogsetup.go"
}

// Test1B2_SingleSlogSetDefault enforces the §2d invariant that exactly one
// site installs the default slog handler. It scans the whole CLI module (not
// just command packages) because the danger is any package's init/constructor
// reaching for slog.SetDefault with an un-redacted, non-mode-aware handler.
func Test1B2_SingleSlogSetDefault(t *testing.T) {
	pkgs := loadCLI(t)

	offenders := map[string]int{}
	sawAllowed := false
	forEachFile(pkgs, func(string) bool { return true }, func(p *packages.Package, file *ast.File, path string) {
		hits := selectorPrefixCalls(p.TypesInfo, p.Fset, file, "slog", func(s string) bool {
			return s == "SetDefault"
		})
		if len(hits) == 0 {
			return
		}
		if slogSetDefaultAllowed(p.PkgPath, path) {
			sawAllowed = true
			return
		}
		offenders[rel(p.PkgPath)+"/"+baseName(path)] += len(hits)
	})

	for file, n := range offenders {
		t.Errorf("slog.SetDefault called outside the interceptor bootstrap in %s (%d call(s)) — "+
			"only internal/cli/cmdcore/slogsetup.go may install the default handler (impl/3.2 §2d)", file, n)
	}
	if !sawAllowed {
		t.Fatal("no slog.SetDefault call found in internal/cli/cmdcore/slogsetup.go — the diagnostics " +
			"bootstrap (impl/3.2 §2d) regressed or moved; this guard must not pass vacuously")
	}
}
