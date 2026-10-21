// Command configgen post-processes the go-jsonschema output in
// internal/cli/ctl/generated/config.go so struct fields whose JSON Schema
// default is non-zero carry no omitempty/omitzero struct tag options (#1832).
//
// go-jsonschema emits `json:"...,omitempty,omitzero"` and `yaml:"...,omitempty"`
// on every non-required property, and emits an UnmarshalJSON method that fills
// in the schema default whenever a key is absent from the input JSON. For a
// field whose default is non-zero (e.g. `sample_ratio: 1.0`,
// `failed_login_lockout_seconds: 900`, `manual_flows_enabled: true`),
// omitempty/omitzero drops an explicit zero value (`0`, `0.0`, `false`, `""`)
// on marshal and UnmarshalJSON then restores the non-zero default on reload.
// Stripping omitempty and omitzero from those fields preserves explicit zero
// values across JSON and YAML round-trips.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"os"
	"reflect"
	"sort"
	"strconv"
	"strings"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: configgen <schema.json> <config.go>")
		os.Exit(2)
	}
	if err := run(os.Args[1], os.Args[2]); err != nil {
		fmt.Fprintf(os.Stderr, "configgen: %v\n", err)
		os.Exit(1)
	}
}

type schemaDoc struct {
	Properties map[string]schemaProp `json:"properties"`
	Defs       map[string]schemaDef  `json:"$defs"`
}

type schemaDef struct {
	Properties map[string]schemaProp `json:"properties"`
}

type schemaProp struct {
	Default json.RawMessage `json:"default"`
}

func run(schemaPath, goPath string) error {
	schemaBytes, err := os.ReadFile(schemaPath) //nolint:gosec // generator input: vendored config-schema.json.
	if err != nil {
		return fmt.Errorf("reading schema %s: %w", schemaPath, err)
	}
	var doc schemaDoc
	if err := json.Unmarshal(schemaBytes, &doc); err != nil {
		return fmt.Errorf("parsing schema %s: %w", schemaPath, err)
	}

	nonZeroDefaults := collectSchemaNonZeroDefaults(doc)

	src, err := os.ReadFile(goPath) //nolint:gosec // generator input: generated config.go under internal/cli/ctl/generated.
	if err != nil {
		return fmt.Errorf("reading %s: %w", goPath, err)
	}

	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, goPath, src, parser.ParseComments)
	if err != nil {
		return fmt.Errorf("parsing %s: %w", goPath, err)
	}

	collectASTNonZeroDefaults(file, nonZeroDefaults)
	propagateAliasDefaults(file, nonZeroDefaults)

	edits := collectTagEdits(fset, file, nonZeroDefaults)
	if len(edits) == 0 {
		return nil
	}

	patched := applyEdits(src, edits)
	formatted, err := format.Source(patched)
	if err != nil {
		return fmt.Errorf("gofmt %s: %w", goPath, err)
	}
	if err := os.WriteFile(goPath, formatted, 0o600); err != nil { //nolint:gosec // generator output under internal/cli/ctl/generated.
		return fmt.Errorf("writing %s: %w", goPath, err)
	}
	return nil
}

// collectSchemaNonZeroDefaults returns, per generated Go struct name, the set
// of JSON property names whose schema-declared default is non-zero.
func collectSchemaNonZeroDefaults(doc schemaDoc) map[string]map[string]bool {
	out := map[string]map[string]bool{}
	recordProps := func(typeName string, props map[string]schemaProp) {
		for propName, prop := range props {
			if hasNonZeroDefault(prop.Default) {
				if out[typeName] == nil {
					out[typeName] = map[string]bool{}
				}
				out[typeName][propName] = true
			}
		}
	}
	recordProps("ConfigSchemaJson", doc.Properties)
	for defName, def := range doc.Defs {
		recordProps(defName, def.Properties)
	}
	return out
}

// hasNonZeroDefault reports whether raw JSON represents a non-zero default
// value in Go (anything other than absent, null, false, 0, 0.0, "", [], {}).
func hasNonZeroDefault(raw json.RawMessage) bool {
	if len(raw) == 0 {
		return false
	}
	var v any
	if err := json.Unmarshal(raw, &v); err != nil || v == nil {
		return false
	}
	switch val := v.(type) {
	case bool:
		return val
	case float64:
		return val != 0
	case string:
		return val != ""
	case []any:
		return len(val) > 0
	case map[string]any:
		return len(val) > 0
	default:
		return false
	}
}

// collectASTNonZeroDefaults inspects every UnmarshalJSON method in the file and
// records any `if v, ok := raw["<prop>"]; !ok || v == nil { plain.<Field> = <lit> }`
// assignment whose right-hand side is not a Go zero literal.
func collectASTNonZeroDefaults(file *ast.File, out map[string]map[string]bool) {
	for _, decl := range file.Decls {
		fd, ok := decl.(*ast.FuncDecl)
		if !ok || fd.Recv == nil || fd.Name.Name != "UnmarshalJSON" || fd.Body == nil {
			continue
		}
		recvType := receiverTypeName(fd.Recv)
		if recvType == "" {
			continue
		}
		for _, stmt := range fd.Body.List {
			ifStmt, ok := stmt.(*ast.IfStmt)
			if !ok {
				continue
			}
			propName, rhs, ok := matchDefaultAssignment(ifStmt)
			if !ok || isZeroExpr(rhs) {
				continue
			}
			if out[recvType] == nil {
				out[recvType] = map[string]bool{}
			}
			out[recvType][propName] = true
		}
	}
}

func receiverTypeName(recv *ast.FieldList) string {
	if recv == nil || len(recv.List) != 1 {
		return ""
	}
	star, ok := recv.List[0].Type.(*ast.StarExpr)
	if !ok {
		return ""
	}
	ident, ok := star.X.(*ast.Ident)
	if !ok {
		return ""
	}
	return ident.Name
}

// matchDefaultAssignment matches the go-jsonschema default-fill pattern:
//
//	if v, ok := raw["<prop>"]; !ok || v == nil {
//	    plain.<Field> = <rhs>
//	}
func matchDefaultAssignment(ifStmt *ast.IfStmt) (propName string, rhs ast.Expr, ok bool) {
	assign, isAssign := ifStmt.Init.(*ast.AssignStmt)
	if !isAssign || len(assign.Rhs) != 1 {
		return "", nil, false
	}
	idx, isIdx := assign.Rhs[0].(*ast.IndexExpr)
	if !isIdx {
		return "", nil, false
	}
	keyLit, isLit := idx.Index.(*ast.BasicLit)
	if !isLit || keyLit.Kind != token.STRING {
		return "", nil, false
	}
	key, err := strconv.Unquote(keyLit.Value)
	if err != nil {
		return "", nil, false
	}
	if ifStmt.Body == nil || len(ifStmt.Body.List) != 1 {
		return "", nil, false
	}
	bodyAssign, isBodyAssign := ifStmt.Body.List[0].(*ast.AssignStmt)
	if !isBodyAssign || len(bodyAssign.Lhs) != 1 || len(bodyAssign.Rhs) != 1 {
		return "", nil, false
	}
	sel, isSel := bodyAssign.Lhs[0].(*ast.SelectorExpr)
	if !isSel {
		return "", nil, false
	}
	xIdent, isIdent := sel.X.(*ast.Ident)
	if !isIdent || xIdent.Name != "plain" {
		return "", nil, false
	}
	return key, bodyAssign.Rhs[0], true
}

func isZeroExpr(expr ast.Expr) bool {
	switch e := expr.(type) {
	case *ast.Ident:
		return e.Name == "false" || e.Name == "nil"
	case *ast.BasicLit:
		switch e.Kind {
		case token.STRING:
			s, err := strconv.Unquote(e.Value)
			return err == nil && s == ""
		case token.INT, token.FLOAT:
			f, err := strconv.ParseFloat(e.Value, 64)
			return err == nil && f == 0
		}
	}
	return false
}

// propagateAliasDefaults copies non-zero default sets across go-jsonschema's
// `<Struct>_0 = <Target>` type aliases so inline anyOf wrapper structs inherit
// their referenced definition's defaults.
func propagateAliasDefaults(file *ast.File, out map[string]map[string]bool) {
	for _, decl := range file.Decls {
		gd, ok := decl.(*ast.GenDecl)
		if !ok || gd.Tok != token.TYPE {
			continue
		}
		for _, spec := range gd.Specs {
			ts, ok := spec.(*ast.TypeSpec)
			if !ok || !ts.Assign.IsValid() || !strings.HasSuffix(ts.Name.Name, "_0") {
				continue
			}
			targetIdent, ok := ts.Type.(*ast.Ident)
			if !ok {
				continue
			}
			srcSet := out[targetIdent.Name]
			if len(srcSet) == 0 {
				continue
			}
			wrapperName := strings.TrimSuffix(ts.Name.Name, "_0")
			if out[wrapperName] == nil {
				out[wrapperName] = map[string]bool{}
			}
			for k, v := range srcSet {
				out[wrapperName][k] = v
			}
		}
	}
}

type byteEdit struct {
	start int
	end   int
	repl  string
}

func collectTagEdits(fset *token.FileSet, file *ast.File, nonZeroDefaults map[string]map[string]bool) []byteEdit {
	var edits []byteEdit
	for _, decl := range file.Decls {
		gd, ok := decl.(*ast.GenDecl)
		if !ok || gd.Tok != token.TYPE {
			continue
		}
		for _, spec := range gd.Specs {
			ts, ok := spec.(*ast.TypeSpec)
			if !ok {
				continue
			}
			st, ok := ts.Type.(*ast.StructType)
			if !ok || st.Fields == nil {
				continue
			}
			props := nonZeroDefaults[ts.Name.Name]
			if len(props) == 0 {
				continue
			}
			for _, field := range st.Fields.List {
				if field.Tag == nil {
					continue
				}
				rawTag, err := strconv.Unquote(field.Tag.Value)
				if err != nil {
					continue
				}
				jsonTag := reflect.StructTag(rawTag).Get("json")
				propName := strings.Split(jsonTag, ",")[0]
				if propName == "" || !props[propName] {
					continue
				}
				newTag := fmt.Sprintf("`json:%q yaml:%q mapstructure:%q`", propName, propName, propName)
				if newTag == field.Tag.Value {
					continue
				}
				edits = append(edits, byteEdit{
					start: fset.Position(field.Tag.Pos()).Offset,
					end:   fset.Position(field.Tag.End()).Offset,
					repl:  newTag,
				})
			}
		}
	}
	sort.Slice(edits, func(i, j int) bool {
		return edits[i].start < edits[j].start
	})
	return edits
}

func applyEdits(src []byte, edits []byteEdit) []byte {
	var buf bytes.Buffer
	cursor := 0
	for _, e := range edits {
		buf.Write(src[cursor:e.start])
		buf.WriteString(e.repl)
		cursor = e.end
	}
	buf.Write(src[cursor:])
	return buf.Bytes()
}
