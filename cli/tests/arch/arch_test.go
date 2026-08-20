// Package arch holds the CLI's architectural guardrail tests (plan Phase 0,
// themes/cli-v2/impl/0.0 §1). They do not test business logic; they parse the
// module's own source and fail the build when a structural boundary is crossed.
//
// Scoping note (Phase 0): several boundaries in impl/0.0 name packages that the
// V2 refactor introduces later — client/... (Phase 1), internal/cli/api and
// internal/cli/ux (Phases 2–3). These tests are written to enforce the rule
// against whatever matching packages exist *today* and to light up
// automatically as those packages land, rather than hard-coding a path that
// does not yet compile. Where a target package is absent, the test logs that it
// is dormant instead of vacuously passing, so the guardrail is visible.
package arch

import (
	"go/ast"
	"go/token"
	"go/types"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/tools/go/packages"
)

// modulePath is the CLI Go module. Import paths are compared against it.
const modulePath = "github.com/jentic/jentic-one/cli"

// loadMode pulls in the syntax trees and import graph we need for AST walks and
// import-boundary assertions.
const loadMode = packages.NeedName |
	packages.NeedFiles |
	packages.NeedSyntax |
	packages.NeedTypes |
	packages.NeedTypesInfo |
	packages.NeedImports |
	packages.NeedDeps

// loadCLI loads every package in the module (patterns are relative to the cli/
// module root, which is the parent of tests/arch). Fails the test on load
// errors so a broken build never yields a silently-green guardrail.
//
// It returns the full flattened set — every package matched by ./... plus their
// in-module dependency closure — de-duplicated by PkgPath. (We can't rely on
// packages.Visit alone: it walks the import graph from the roots, and the arch
// test package imports almost nothing, so the roots' closure would miss the
// rest of the module.)
func loadCLI(t *testing.T) []*packages.Package {
	t.Helper()
	cfg := &packages.Config{
		Mode: loadMode,
		Dir:  "../..", // tests/arch (test CWD) -> cli/ (module root)
		// Tests: false — the guardrails constrain production code; test files
		// legitimately use fmt.Print, os.Stdout, etc.
		Tests: false,
	}
	roots, err := packages.Load(cfg, "./...")
	if err != nil {
		t.Fatalf("packages.Load: %v", err)
	}

	// Flatten roots + their in-module import closure, de-duped by path.
	all := map[string]*packages.Package{}
	var visit func(p *packages.Package)
	visit = func(p *packages.Package) {
		if p == nil || all[p.PkgPath] != nil {
			return
		}
		all[p.PkgPath] = p
		for _, dep := range p.Imports {
			if strings.HasPrefix(dep.PkgPath, modulePath) {
				visit(dep)
			}
		}
	}
	for _, r := range roots {
		visit(r)
	}

	var hard int
	out := make([]*packages.Package, 0, len(all))
	for _, p := range all {
		out = append(out, p)
		for _, e := range p.Errors {
			t.Errorf("load error in %s: %v", p.PkgPath, e)
			hard++
		}
	}
	if hard > 0 {
		t.Fatalf("%d package load errors; guardrails need a clean build", hard)
	}
	if len(out) == 0 {
		t.Fatal("no packages loaded")
	}
	return out
}

// rel strips the module prefix from an import path for readable failures.
func rel(importPath string) string {
	return strings.TrimPrefix(strings.TrimPrefix(importPath, modulePath), "/")
}

// underPrefixes reports whether the package path is at/under any of the given
// module-relative prefixes (e.g. "client", "internal/cli/api").
func underPrefixes(pkgPath string, prefixes ...string) bool {
	r := rel(pkgPath)
	for _, pre := range prefixes {
		if r == pre || strings.HasPrefix(r, pre+"/") {
			return true
		}
	}
	return false
}

// forEachFile invokes fn for every non-test .go file in the matched packages,
// giving the package, the file's AST, and its absolute path. The path is
// derived from the file's own token position (robust to any Syntax vs
// CompiledGoFiles ordering differences).
func forEachFile(pkgs []*packages.Package, match func(pkgPath string) bool, fn func(p *packages.Package, file *ast.File, path string)) {
	for _, p := range pkgs {
		if !strings.HasPrefix(p.PkgPath, modulePath) || !match(p.PkgPath) {
			continue
		}
		for _, f := range p.Syntax {
			path := p.Fset.Position(f.Pos()).Filename
			fn(p, f, path)
		}
	}
}

// baseName is the trailing path segment of a file, for concise messages.
func baseName(path string) string { return filepath.Base(path) }

// selectorCalls returns the source line numbers of every `pkg.Name` selector
// expression in the file where `pkg` resolves to the imported package `pkgName`
// (not a shadowed local of the same spelling). It covers both value references
// (os.Stdout) and calls (fmt.Println) since both are ast.SelectorExpr.
func selectorCalls(info *types.Info, fset *token.FileSet, file *ast.File, pkgName, name string) []int {
	var lines []int
	ast.Inspect(file, func(n ast.Node) bool {
		sel, ok := n.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		ident, ok := sel.X.(*ast.Ident)
		if !ok || ident.Name != pkgName || sel.Sel.Name != name {
			return true
		}
		if !identIsPackage(info, ident) {
			return true
		}
		lines = append(lines, fset.Position(sel.Pos()).Line)
		return true
	})
	return lines
}

// selectorPrefixCalls returns line numbers for any `pkg.*` selector against the
// named import package (e.g. every fmt.Print* call). funcMatch decides which
// selector names count.
func selectorPrefixCalls(info *types.Info, fset *token.FileSet, file *ast.File, pkgName string, funcMatch func(name string) bool) []int {
	var lines []int
	ast.Inspect(file, func(n ast.Node) bool {
		sel, ok := n.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		ident, ok := sel.X.(*ast.Ident)
		if !ok || ident.Name != pkgName || !identIsPackage(info, ident) {
			return true
		}
		if funcMatch(sel.Sel.Name) {
			lines = append(lines, fset.Position(sel.Pos()).Line)
		}
		return true
	})
	return lines
}

// identIsPackage reports whether an identifier resolves to an imported package
// name (as opposed to a local variable/field that happens to share the
// spelling). Uses type info when available and falls back to treating it as a
// package if unresolved (import-name idents are frequently absent from Uses).
func identIsPackage(info *types.Info, ident *ast.Ident) bool {
	if info == nil {
		return true
	}
	if obj, ok := info.Uses[ident]; ok {
		_, isPkg := obj.(*types.PkgName)
		return isPkg
	}
	// Not in Uses: could be a package name the checker recorded elsewhere, or a
	// local. Local variables are recorded in Defs/Uses, so an absent entry for
	// a lowercase stdlib-style selector base is almost certainly a package.
	return true
}
