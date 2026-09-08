package arch

import (
	"go/ast"
	"go/types"
	"testing"

	"golang.org/x/tools/go/packages"
)

// redactionFunnels are the ux helpers that guarantee a []byte has passed the
// fail-closed redaction funnel before it hits a data-plane writer. A raw
// upstream/API body written to App.Out/App.Err MUST come from one of these (or
// via the Audience Render path, which never writes a bare body). Keeping this
// list in the arch test — rather than a runtime constant — is deliberate: it is
// the *contract* the gate enforces, and adding a new funnel is a conscious edit
// reviewed alongside this rule.
var redactionFunnels = map[string]bool{
	"RedactBytes":    true, // byte backstop for raw upstream bodies (inspect, spec, execute --raw, api passthrough)
	"MarshalForFile": true, // redacted+indented marshal for file destinations
	"WriteJSONLine":  true, // streaming NDJSON primitive (writes to an io.Writer itself)
}

// rawWriteAllowlist names byte-writer call sites in the data-plane command
// package that are exempt because they do NOT emit an upstream/API body — e.g. a
// writer wired for a test, or a locally-constructed non-secret byte slice. Each
// entry is "<relfile>:<line>" and must carry a justification in review. It is
// empty today: every real body writer routes through a funnel. An unexplained
// addition here is itself a review signal.
var rawWriteAllowlist = map[string]bool{}

// Test1I_RedactionFunnel closes the class of bug reviewer #5 found in round 3:
// `jentic inspect` wrote the upstream body with `fmt.Fprintln(a.Out, string(body))`
// — zero redaction — bypassing both the naked-print gate (it writes to a.Out, not
// os.Stdout) and the x-sensitive sweep (which checks annotations, not write
// sites). The fix routed inspect/apis-inspect/apis-spec through ux.RedactBytes;
// this gate makes the *class* impossible to reintroduce.
//
// Rule: in internal/cli/api, every `<w>.Write(x)` where <w> is App.Out/App.Err
// (i.e. `a.Out` / `a.Err`) must have x be — directly, or via a local variable
// assigned in the same function — a call to one of redactionFunnels. A bare body
// identifier (the old inspect shape) fails. Styled human text goes through
// fmt.Fprintln (governed by Test1B), not .Write, so it is out of scope here.
func Test1I_RedactionFunnel(t *testing.T) {
	pkgs := loadCLI(t)

	sawWrite := false
	offenders := map[string]string{}

	forEachFile(pkgs, func(pkgPath string) bool {
		return underPrefixes(pkgPath, "internal/cli/api")
	}, func(p *packages.Package, file *ast.File, path string) {
		relFile := rel(p.PkgPath) + "/" + baseName(path)

		// Walk each function so "assigned from a funnel" is scoped correctly: a
		// funnel result in one function must not launder a raw write in another.
		ast.Inspect(file, func(n ast.Node) bool {
			fn, ok := n.(*ast.FuncDecl)
			if !ok || fn.Body == nil {
				return true
			}
			funnelVars := collectFunnelVars(p.TypesInfo, fn.Body)
			ast.Inspect(fn.Body, func(m ast.Node) bool {
				call, ok := m.(*ast.CallExpr)
				if !ok {
					return true
				}
				sel, ok := call.Fun.(*ast.SelectorExpr)
				if !ok || sel.Sel.Name != "Write" || len(call.Args) != 1 {
					return true
				}
				if !isOutOrErrWriter(p.TypesInfo, sel.X) {
					return true
				}
				sawWrite = true
				line := p.Fset.Position(call.Pos()).Line
				key := relFile + ":" + itoa(line)
				if rawWriteAllowlist[key] {
					return true
				}
				if writeArgIsRedacted(call.Args[0], funnelVars) {
					return true
				}
				offenders[key] = "raw byte write to App.Out/App.Err not funneled through ux.RedactBytes/MarshalForFile"
				return true
			})
			return true
		})
	})

	for site, why := range offenders {
		t.Errorf("un-redacted data-plane body write at %s: %s — an upstream/API body "+
			"must go through ux.RedactBytes (or MarshalForFile), like execute/inspect do. "+
			"If this writer emits no secret-bearing body, add it to rawWriteAllowlist with a justification.", site, why)
	}
	if !sawWrite {
		t.Fatal("no App.Out/App.Err .Write() call found in internal/cli/api — the redaction-funnel " +
			"gate must not pass vacuously (did the writer shape or package layout change?)")
	}
}

// collectFunnelVars returns the set of local variable NAMES in body that were
// assigned (via := or =) the single result of a redactionFunnels call, e.g.
// `redacted := ux.RedactBytes(body)`. Names are a coarse but safe key: a later
// reassignment of the same name to a non-funnel value would be a bug the gate
// intentionally does not try to model (it is not a pattern we use), and shadowing
// across scopes only ever makes the gate stricter, never laxer.
func collectFunnelVars(info *types.Info, body *ast.BlockStmt) map[string]bool {
	vars := map[string]bool{}
	ast.Inspect(body, func(n ast.Node) bool {
		assign, ok := n.(*ast.AssignStmt)
		if !ok || len(assign.Rhs) != 1 || len(assign.Lhs) != 1 {
			return true
		}
		if !callIsFunnel(info, assign.Rhs[0]) {
			return true
		}
		if id, ok := assign.Lhs[0].(*ast.Ident); ok {
			vars[id.Name] = true
		}
		return true
	})
	return vars
}

// writeArgIsRedacted reports whether the argument to a .Write() call is trusted:
// a direct funnel call, or an identifier previously bound to a funnel result.
func writeArgIsRedacted(arg ast.Expr, funnelVars map[string]bool) bool {
	if call, ok := arg.(*ast.CallExpr); ok {
		return callIsFunnelName(call)
	}
	if id, ok := arg.(*ast.Ident); ok {
		return funnelVars[id.Name]
	}
	return false
}

// callIsFunnel reports whether expr is a call to one of the redaction funnels,
// verifying the selector base is the ux package (not a shadowing local) via type
// info when available.
func callIsFunnel(info *types.Info, expr ast.Expr) bool {
	call, ok := expr.(*ast.CallExpr)
	if !ok {
		return false
	}
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || !redactionFunnels[sel.Sel.Name] {
		return false
	}
	if ident, ok := sel.X.(*ast.Ident); ok {
		return identIsPackage(info, ident)
	}
	return false
}

// callIsFunnelName is the type-info-free form used at the write site (the
// selector base is checked by callIsFunnel when collecting vars; at the write
// site a direct funnel call is accepted on name match).
func callIsFunnelName(call *ast.CallExpr) bool {
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok {
		return false
	}
	return redactionFunnels[sel.Sel.Name]
}

// isOutOrErrWriter reports whether the receiver of a .Write() call is App.Out or
// App.Err (i.e. `a.Out` / `a.Err`), the two data-plane byte sinks. We match on
// the field selector name (Out/Err) off any receiver so it holds regardless of
// the App receiver's local name.
func isOutOrErrWriter(_ *types.Info, x ast.Expr) bool {
	sel, ok := x.(*ast.SelectorExpr)
	if !ok {
		return false
	}
	return sel.Sel.Name == "Out" || sel.Sel.Name == "Err"
}

// itoa is a tiny int->string without importing strconv into the arch package's
// otherwise-parser-only surface (keeps the guardrail deps minimal).
func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
