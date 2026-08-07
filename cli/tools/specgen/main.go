// Command specgen is the single generator for every spec-derived companion the
// SDK needs beyond the oapi-codegen client (impl/2.1 §4). Run under
// `make generate-api` once per plane:
//
//	go run ./tools/specgen <plane> <outputDir>
//
// where <plane> is the generated package name (control|broker) and <outputDir>
// is that plane's generated directory (which already holds the vendored
// spec.yaml the oapi-codegen client was built from). It emits, into <outputDir>:
//
//	required.gen.go   RequiredFields() companions   (impl/2.1 §4a)
//	sensitive.gen.go  SensitiveFields table         (impl/2.1 §4b, from x-sensitive)
//	ops.gen.go        operation index               (impl/2.1 §4, powers `jentic api ops`)
//
// It parses the SAME vendored spec.yaml with libopenapi — the parser the runtime
// `api describe` will reuse (Phase 5) — so the module has exactly one
// spec-parsing dependency. All three outputs are keyed by the component-schema
// name, which oapi-codegen uses verbatim as the generated Go type name (verified:
// e.g. `CredentialCreateResponse` in the spec becomes `type CredentialCreateResponse`).
package main

import (
	"bytes"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/pb33f/libopenapi"
	"github.com/pb33f/libopenapi/datamodel"
	base "github.com/pb33f/libopenapi/datamodel/high/base"
	v3 "github.com/pb33f/libopenapi/datamodel/high/v3"
	"github.com/pb33f/libopenapi/orderedmap"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: specgen <plane> <outputDir>")
		os.Exit(2)
	}
	plane, outDir := os.Args[1], os.Args[2]
	if err := run(plane, outDir); err != nil {
		fmt.Fprintf(os.Stderr, "specgen %s: %v\n", plane, err)
		os.Exit(1)
	}
}

func run(pkg, outDir string) error {
	specPath := filepath.Join(outDir, "spec.yaml")
	specBytes, err := os.ReadFile(specPath) //nolint:gosec // generator input: a vendored, checked-in spec under client/generated.
	if err != nil {
		return fmt.Errorf("reading vendored spec %s: %w", specPath, err)
	}

	doc, err := libopenapi.NewDocumentWithConfiguration(specBytes, &datamodel.DocumentConfiguration{
		// SkipExternalRefResolution: the broker spec points its error responses at a
		// GitHub-hosted problem-details.yaml via absolute-URL $refs. We must NOT fetch
		// remote documents — CI stays hermetic — and none of the companions we emit
		// consume those error schemas (we walk local component schemas + path
		// operationIds only). This leaves external refs as-is and lets the model build
		// deterministically for both planes.
		SkipExternalRefResolution: true,
		// Quiet the default Error-level logger: the skipped external refs would
		// otherwise spam stderr with resolution warnings that are expected here.
		Logger: slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
	if err != nil {
		return fmt.Errorf("parsing spec %s: %w", specPath, err)
	}
	model, err := doc.BuildV3Model()
	if err != nil {
		return fmt.Errorf("building v3 model for %s: %w", specPath, err)
	}

	required, sensitive := walkSchemas(model)
	ops := walkPaths(model)

	// Cross-check component-schema names against the types the oapi-codegen client
	// actually declared. Most component names map 1:1 to a generated Go type of the
	// same name, but module-qualified Pydantic exports (e.g.
	// "jentic_one__auth__web__schemas__agents__DenyRequest") are sanitized/deduped by
	// oapi-codegen into a DIFFERENT identifier, so emitting a method on the raw name
	// would not compile. We only emit companions for names that are real generated
	// types — this keeps the generated companions build-clean by construction, and a
	// name we skip is simply one no consumer could reference by that name anyway.
	declared, err := declaredTypes(filepath.Join(outDir, "client.go"))
	if err != nil {
		return err
	}
	required = filterByDeclared(required, declared)
	sensitive = filterByDeclared(sensitive, declared)

	if err := writeGo(filepath.Join(outDir, "required.gen.go"), renderRequired(pkg, required)); err != nil {
		return err
	}
	if err := writeGo(filepath.Join(outDir, "sensitive.gen.go"), renderSensitive(pkg, sensitive)); err != nil {
		return err
	}
	return writeGo(filepath.Join(outDir, "ops.gen.go"), renderOps(pkg, ops))
}

// declaredTypes parses the generated oapi-codegen client and returns the set of
// top-level type names it declares. specgen uses this to only emit companions for
// component schemas that became real Go types (see run's cross-check note).
func declaredTypes(clientPath string) (map[string]bool, error) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, clientPath, nil, parser.SkipObjectResolution)
	if err != nil {
		return nil, fmt.Errorf("parsing generated client %s: %w", clientPath, err)
	}
	types := map[string]bool{}
	for _, decl := range file.Decls {
		gen, ok := decl.(*ast.GenDecl)
		if !ok || gen.Tok != token.TYPE {
			continue
		}
		for _, spec := range gen.Specs {
			if ts, ok := spec.(*ast.TypeSpec); ok {
				types[ts.Name.Name] = true
			}
		}
	}
	return types, nil
}

// filterByDeclared drops any entry whose key is not a declared generated type.
func filterByDeclared(m map[string][]string, declared map[string]bool) map[string][]string {
	out := make(map[string][]string, len(m))
	for name, vals := range m {
		if declared[name] {
			out[name] = vals
		}
	}
	return out
}

// walkSchemas returns, keyed by component-schema (== generated Go type) name:
//   - required: the spec's `required:` array, verbatim json property names.
//   - sensitive: the json property names carrying `x-sensitive: true`.
//
// Only NAMED component schemas are emitted, because those are the ones
// oapi-codegen turns into a Go type with a matching name. Inline request/response
// schemas have no stable Go type name to attach a method or table entry to.
func walkSchemas(model *libopenapi.DocumentModel[v3.Document]) (required, sensitive map[string][]string) {
	required = map[string][]string{}
	sensitive = map[string][]string{}
	if model.Model.Components == nil || model.Model.Components.Schemas == nil {
		return required, sensitive
	}
	for pair := orderedmap.First(model.Model.Components.Schemas); pair != nil; pair = pair.Next() {
		name := pair.Key()
		proxy := pair.Value()
		if proxy == nil {
			continue
		}
		schema := proxy.Schema()
		if schema == nil {
			continue
		}
		if len(schema.Required) > 0 {
			req := append([]string(nil), schema.Required...)
			sort.Strings(req)
			required[name] = req
		}
		if props := sensitiveProps(schema); len(props) > 0 {
			sort.Strings(props)
			sensitive[name] = props
		}
	}
	return required, sensitive
}

// sensitiveProps returns the property names of schema marked `x-sensitive: true`.
func sensitiveProps(schema *base.Schema) []string {
	if schema.Properties == nil {
		return nil
	}
	var out []string
	for pair := orderedmap.First(schema.Properties); pair != nil; pair = pair.Next() {
		propProxy := pair.Value()
		if propProxy == nil {
			continue
		}
		prop := propProxy.Schema()
		if prop == nil || prop.Extensions == nil {
			continue
		}
		node, ok := prop.Extensions.Get("x-sensitive")
		if !ok || node == nil {
			continue
		}
		if strings.TrimSpace(node.Value) == "true" {
			out = append(out, pair.Key())
		}
	}
	return out
}

// op is one entry of the operation index.
type op struct {
	Method      string
	Path        string
	OperationID string
	Summary     string
}

// walkPaths returns the operation index: one entry per (method, path) with an
// operationId, sorted deterministically by path then method.
func walkPaths(model *libopenapi.DocumentModel[v3.Document]) []op {
	var ops []op
	if model.Model.Paths == nil || model.Model.Paths.PathItems == nil {
		return ops
	}
	for pair := orderedmap.First(model.Model.Paths.PathItems); pair != nil; pair = pair.Next() {
		path := pair.Key()
		item := pair.Value()
		if item == nil {
			continue
		}
		for method, operation := range item.GetOperations().FromOldest() {
			if operation == nil || operation.OperationId == "" {
				continue
			}
			ops = append(ops, op{
				Method:      strings.ToUpper(method),
				Path:        path,
				OperationID: operation.OperationId,
				Summary:     operation.Summary,
			})
		}
	}
	sort.Slice(ops, func(i, j int) bool {
		if ops[i].Path != ops[j].Path {
			return ops[i].Path < ops[j].Path
		}
		return ops[i].Method < ops[j].Method
	})
	return ops
}

const genHeader = "// Code generated by tools/specgen; DO NOT EDIT.\n"

func renderRequired(pkg string, required map[string][]string) []byte {
	var b bytes.Buffer
	b.WriteString(genHeader)
	fmt.Fprintf(&b, "\npackage %s\n\n", pkg)
	b.WriteString("// RequiredFields companions expose each component schema's spec `required:`\n")
	b.WriteString("// array (verbatim json property names) so hand-written command validation and any\n")
	b.WriteString("// future binder can enforce required fields without re-parsing the spec (impl/2.1 §4a).\n\n")
	for _, name := range sortedKeys(required) {
		fmt.Fprintf(&b, "func (%s) RequiredFields() []string { return %s }\n", name, goStringSlice(required[name]))
	}
	return b.Bytes()
}

func renderSensitive(pkg string, sensitive map[string][]string) []byte {
	var b bytes.Buffer
	b.WriteString(genHeader)
	fmt.Fprintf(&b, "\npackage %s\n\n", pkg)
	b.WriteString("// SensitiveFields maps a generated type name to the json property names the spec\n")
	b.WriteString("// marks `x-sensitive: true`. The layer-1 typed redaction pass (impl/3.1 §1) treats\n")
	b.WriteString("// membership here exactly like a `redact:\"true\"` struct tag, so generated SDK types\n")
	b.WriteString("// (which carry no custom tags) are redacted from the spec, not by naming luck.\n")
	b.WriteString("var SensitiveFields = map[string][]string{\n")
	for _, name := range sortedKeys(sensitive) {
		fmt.Fprintf(&b, "\t%q: %s,\n", name, goStringSlice(sensitive[name]))
	}
	b.WriteString("}\n")
	return b.Bytes()
}

func renderOps(pkg string, ops []op) []byte {
	var b bytes.Buffer
	b.WriteString(genHeader)
	fmt.Fprintf(&b, "\npackage %s\n\n", pkg)
	b.WriteString("// Op is one entry of the operation index: the HTTP method, templated path,\n")
	b.WriteString("// operationId, and summary for a single spec operation.\n")
	b.WriteString("type Op struct {\n")
	b.WriteString("\tMethod      string\n")
	b.WriteString("\tPath        string\n")
	b.WriteString("\tOperationID string\n")
	b.WriteString("\tSummary     string\n")
	b.WriteString("}\n\n")
	b.WriteString("// Ops is the full operation index for this plane, powering `jentic api ops` and the\n")
	b.WriteString("// passthrough path allowlist (impl/5.0 §6a). Sorted by path then method.\n")
	b.WriteString("var Ops = []Op{\n")
	for _, o := range ops {
		fmt.Fprintf(&b, "\t{Method: %q, Path: %q, OperationID: %q, Summary: %q},\n", o.Method, o.Path, o.OperationID, o.Summary)
	}
	b.WriteString("}\n")
	return b.Bytes()
}

func sortedKeys(m map[string][]string) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func goStringSlice(ss []string) string {
	if len(ss) == 0 {
		return "[]string{}"
	}
	quoted := make([]string, len(ss))
	for i, s := range ss {
		quoted[i] = fmt.Sprintf("%q", s)
	}
	return "[]string{" + strings.Join(quoted, ", ") + "}"
}

func writeGo(path string, src []byte) error {
	formatted, err := format.Source(src)
	if err != nil {
		// Emit the unformatted source so the failure is diagnosable.
		_ = os.WriteFile(path, src, 0o600) //nolint:gosec // best-effort dump for debugging a generator bug.
		return fmt.Errorf("gofmt %s: %w", path, err)
	}
	if err := os.WriteFile(path, formatted, 0o600); err != nil { //nolint:gosec // generated output under client/generated.
		return fmt.Errorf("writing %s: %w", path, err)
	}
	return nil
}
