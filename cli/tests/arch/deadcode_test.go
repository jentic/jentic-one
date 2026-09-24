package arch

import (
	"go/ast"
	"go/types"
	"strings"
	"testing"
)

// deadcodeRoots are the module-relative prefixes the dead-code gate covers: the
// public SDK (client/...) and the CLI command/adapter layers (internal/cli/...).
// We scope to unexported functions here (see below) — those have no possible
// out-of-package consumer, so "no in-module caller" means genuinely dead. This is
// the anti-regression gate for ARCH-20, which removed the dead typed broker SDK
// client (NewBroker/GetBrokerClient/brokerOptions): a future dead private helper
// (the shape brokerOptions had) now fails CI instead of lingering.
//
// Exported symbols are deliberately NOT gated: client/... is a published SDK
// whose surface legitimately has downstream-only consumers (e.g. RequiredFields()
// — see GEN-22), so "unused in-tree" is not "dead" for them.
var deadcodeRoots = []string{"client", "internal/cli"}

// deadcodeAllowlist names unexported funcs that are intentionally never called
// in-tree (e.g. referenced only by go:generate, build-tagged siblings the loader
// didn't compile for this GOOS, or reflection). Keep empty; add with a reason.
var deadcodeAllowlist = map[string]bool{}

// TestNoDeadUnexportedFuncs fails if any unexported package-level function under
// deadcodeRoots is referenced nowhere in the module. It walks every loaded
// package's type-checker Uses map (which records every identifier resolution,
// including calls, method values, and func-value assignments) and subtracts the
// used set from the declared set. Methods, init, and blank funcs are exempt
// (methods can satisfy interfaces implicitly; init/_ are never "called").
func TestNoDeadUnexportedFuncs(t *testing.T) {
	pkgs := loadCLI(t)

	// declared: candidate unexported funcs, keyed by *types.Func identity.
	declared := map[*types.Func]deadDecl{}
	// used: every *types.Func the type checker saw referenced anywhere.
	used := map[*types.Func]bool{}

	for _, p := range pkgs {
		relp := rel(p.PkgPath)
		inScope := false
		for _, root := range deadcodeRoots {
			if relp == root || strings.HasPrefix(relp, root+"/") {
				inScope = true
				break
			}
		}

		// Record uses from EVERY package (a caller may live outside the scoped
		// roots — e.g. cmd/ mains — and still keep a scoped func alive).
		for _, obj := range p.TypesInfo.Uses {
			if fn, ok := obj.(*types.Func); ok {
				used[fn] = true
			}
		}
		if !inScope {
			continue
		}

		// Collect unexported, non-method, package-level func declarations.
		for _, file := range p.Syntax {
			for _, decl := range file.Decls {
				fd, ok := decl.(*ast.FuncDecl)
				if !ok || fd.Recv != nil { // skip methods
					continue
				}
				name := fd.Name.Name
				if name == "_" || name == "init" || ast.IsExported(name) {
					continue
				}
				if deadcodeAllowlist[name] {
					continue
				}
				obj := p.TypesInfo.Defs[fd.Name]
				fn, ok := obj.(*types.Func)
				if !ok {
					continue
				}
				declared[fn] = deadDecl{pkg: relp, name: name}
			}
		}
	}

	for fn, tk := range declared {
		if !used[fn] {
			t.Errorf("dead unexported func %s.%s has no caller anywhere in the module — "+
				"delete it (ARCH-20 anti-regression), or add it to deadcodeAllowlist with a reason.",
				tk.pkg, tk.name)
		}
	}
}

type deadDecl struct{ pkg, name string }
