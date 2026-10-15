package arch

import (
	"go/ast"
	"go/token"
	"strconv"
	"testing"

	"golang.org/x/tools/go/packages"
)

// jsonFlagResolvedElsewhere maps a file that registers a --json flag to the
// sibling file (same package) that resolves it through cmdcore.JSONOrPretty.
// Each entry is a reviewed split between command construction and rendering.
var jsonFlagResolvedElsewhere = map[string]string{
	"internal/cli/api/execute.go":         "internal/cli/api/execute_print.go",
	"internal/cli/localagentcmd/skill.go": "internal/cli/localagentcmd/skill_list.go",
}

// TestJSONFlagRoutesThroughModeSeam guards jentic-one#1414: a `jentic` command
// that registers a --json flag must resolve its output through
// cmdcore.JSONOrPretty, so agent mode emits the JSON envelope without --json.
// Branching on the bare flag makes --json a second output contract and leaks
// the human table to an agent.
//
// Scope: the `jentic` command roots. jenticctl (internal/cli/ctlcmd) has no
// agent mode, and its `logs --json` selects a log sink rather than an output
// format, so it is out of scope. The check is per file: it catches a new
// command file that forgets the seam, not a stray bare branch in a file that
// already uses it.
func TestJSONFlagRoutesThroughModeSeam(t *testing.T) {
	pkgs := loadCLI(t)
	roots := []string{"internal/cli/api", "internal/cli/localagentcmd", "internal/cli/cmdcore"}

	registers := map[string]bool{}
	resolves := map[string]bool{}
	forEachFile(pkgs, func(pkgPath string) bool { return underPrefixes(pkgPath, roots...) },
		func(p *packages.Package, file *ast.File, path string) {
			key := rel(p.PkgPath) + "/" + baseName(path)
			if registersJSONFlag(file) {
				registers[key] = true
			}
			if callsJSONOrPretty(file) {
				resolves[key] = true
			}
		})

	if len(registers) == 0 {
		t.Fatal("no --json flag registrations found; the scan is broken")
	}
	for file := range registers {
		if resolves[file] {
			continue
		}
		if sibling, ok := jsonFlagResolvedElsewhere[file]; ok {
			if !resolves[sibling] {
				t.Errorf("%s registers --json and defers to %s, which no longer calls cmdcore.JSONOrPretty", file, sibling)
			}
			continue
		}
		t.Errorf("%s registers a --json flag but never calls cmdcore.JSONOrPretty — "+
			"branch on JSONOrPretty(cmd, jsonFlag), not the bare flag, so agent mode gets JSON", file)
	}
}

// registersJSONFlag reports whether file registers a bool flag named "json"
// via (*pflag.FlagSet).Bool / BoolP / BoolVar / BoolVarP.
func registersJSONFlag(file *ast.File) bool {
	found := false
	ast.Inspect(file, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		nameIdx := -1
		switch sel.Sel.Name {
		case "Bool", "BoolP":
			nameIdx = 0
		case "BoolVar", "BoolVarP":
			nameIdx = 1
		}
		if nameIdx < 0 || len(call.Args) <= nameIdx {
			return true
		}
		if lit, ok := call.Args[nameIdx].(*ast.BasicLit); ok && lit.Kind == token.STRING {
			if v, err := strconv.Unquote(lit.Value); err == nil && v == "json" {
				found = true
			}
		}
		return true
	})
	return found
}

// callsJSONOrPretty reports whether file references JSONOrPretty, either as
// cmdcore.JSONOrPretty or unqualified inside package cmdcore.
func callsJSONOrPretty(file *ast.File) bool {
	found := false
	ast.Inspect(file, func(n ast.Node) bool {
		switch v := n.(type) {
		case *ast.SelectorExpr:
			if v.Sel.Name == "JSONOrPretty" {
				found = true
			}
		case *ast.Ident:
			if v.Name == "JSONOrPretty" {
				found = true
			}
		}
		return true
	})
	return found
}
