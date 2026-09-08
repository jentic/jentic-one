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
//
// NOTE: no ops.gen.go operation index is emitted (GEN-8): `jentic api ops`/
// `api describe`/the
// passthrough allowlist all parse the embedded spec.yaml at runtime via
// internal/cli/apispec — one data path that also serves `--live` (a server's
// spec fetched at runtime, which no build-time index could cover). Keeping an
// unconsumed generated index would invite divergence between documented and real
// data flow.
//
// It parses the SAME vendored spec.yaml with libopenapi — the parser the runtime
// `api describe` reuses — so the module has exactly one spec-parsing
// dependency. All outputs are keyed by the component-schema name, which
// oapi-codegen uses verbatim as the generated Go type name (verified:
// e.g. `CredentialCreateResponse` in the spec becomes `type CredentialCreateResponse`).
package main

import (
	"bytes"
	"errors"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
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

	// Fail loud on spec constructs the companions do NOT walk (GEN-7): silently
	// contributing nothing to ops/sensitive/required is exactly the class of
	// quiet drift this generator exists to prevent.
	if err := guardUnsupported(model, specBytes); err != nil {
		return fmt.Errorf("%s: %w", specPath, err)
	}

	required, sensitive, err := walkSchemas(model)
	if err != nil {
		return fmt.Errorf("%s: %w", specPath, err)
	}

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
	// Remove a stale ops.gen.go from an earlier specgen version (GEN-8): the
	// operation index was dropped in favor of the runtime apispec parse.
	if err := os.Remove(filepath.Join(outDir, "ops.gen.go")); err != nil && !os.IsNotExist(err) { //nolint:gosec // generator housekeeping under the checked-in client/generated dir.
		return fmt.Errorf("removing retired ops.gen.go: %w", err)
	}
	return nil
}

// allowedExternalRefPrefix is the ONE external $ref source the specs are known
// to use: the pinned problem-details schemas. specgen deliberately skips
// external resolution (hermetic CI), which is safe for these because no emitted
// companion consumes error-response schemas. Any OTHER external ref would
// silently contribute nothing (GEN-7), so guardUnsupported fails on it.
const allowedExternalRefPrefix = "https://raw.githubusercontent.com/jentic/api-problem-details/"

// externalRefPattern matches absolute-URL $refs in the raw spec text. Raw-text
// scanning (rather than model walking) is deliberate: with external resolution
// skipped, unresolved refs are exactly what the model may NOT surface.
var externalRefPattern = regexp.MustCompile(`\$ref:\s*['"]?(https?://[^'"\s]+)`)

// guardUnsupported fails loud on spec constructs specgen does not walk:
// webhooks, callbacks, and external $refs outside the problem-details
// allowlist (GEN-7). Each would otherwise contribute nothing to the emitted
// companions with no diagnostic.
func guardUnsupported(model *libopenapi.DocumentModel[v3.Document], specBytes []byte) error {
	if model.Model.Webhooks != nil && model.Model.Webhooks.Len() > 0 {
		return errors.New("spec declares webhooks, which specgen does not walk — extend walkPaths/walkSchemas (or explicitly exempt them here) before regenerating")
	}
	if model.Model.Paths != nil && model.Model.Paths.PathItems != nil {
		for pair := orderedmap.First(model.Model.Paths.PathItems); pair != nil; pair = pair.Next() {
			item := pair.Value()
			if item == nil {
				continue
			}
			for method, operation := range item.GetOperations().FromOldest() {
				if operation != nil && operation.Callbacks != nil && operation.Callbacks.Len() > 0 {
					return fmt.Errorf("operation %s %s declares callbacks, which specgen does not walk — extend the generator before regenerating", strings.ToUpper(method), pair.Key())
				}
			}
		}
	}
	for _, m := range externalRefPattern.FindAllStringSubmatch(string(specBytes), -1) {
		if !strings.HasPrefix(m[1], allowedExternalRefPrefix) {
			return fmt.Errorf("external $ref %q is outside the problem-details allowlist (%s) — specgen skips external resolution, so its schemas would silently contribute nothing; vendor the schema or extend the allowlist consciously", m[1], allowedExternalRefPrefix)
		}
	}
	return nil
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
// schemas have no stable Go type name to attach a method or table entry to —
// so an `x-sensitive` annotation on a NESTED inline property (an inline object,
// array items, or additionalProperties sub-schema) cannot be expressed in the
// table and is a HARD ERROR (GEN-6): the fix is to hoist the nested object into
// a named component (in Pydantic, a named model), never to let the annotation
// silently do nothing.
func walkSchemas(model *libopenapi.DocumentModel[v3.Document]) (required, sensitive map[string][]string, err error) {
	required = map[string][]string{}
	sensitive = map[string][]string{}
	if model.Model.Components == nil || model.Model.Components.Schemas == nil {
		return required, sensitive, nil
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
		if nested := nestedAnnotatedPaths(schema); len(nested) > 0 {
			return nil, nil, fmt.Errorf(
				"schema %s carries x-sensitive on nested inline properties (%s) that the SensitiveFields table cannot express — hoist the nested object into a named component schema (Pydantic: a named model)",
				name, strings.Join(nested, ", "))
		}
	}
	return required, sensitive, nil
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
		if prop == nil {
			continue
		}
		if isXSensitive(prop) {
			out = append(out, pair.Key())
		}
	}
	return out
}

// isXSensitive reports whether a schema carries `x-sensitive: true`.
func isXSensitive(s *base.Schema) bool {
	if s == nil || s.Extensions == nil {
		return false
	}
	node, ok := s.Extensions.Get("x-sensitive")
	return ok && node != nil && strings.TrimSpace(node.Value) == "true"
}

// nestedAnnotatedPaths walks BELOW the direct-property level of a named schema
// — inline object properties, array `items`, `additionalProperties` — and
// returns the dotted paths of any x-sensitive annotation found there (GEN-6).
// $ref'ed sub-schemas are skipped: a named component is covered by its own
// table entry, so recursing into it would double-report.
func nestedAnnotatedPaths(schema *base.Schema) []string {
	var out []string
	collectNested(schema, "", 0, &out)
	sort.Strings(out)
	return out
}

// collectNested recurses through inline sub-schemas. depth counts property
// levels below the named schema: annotations at depth >= 2 (a property of an
// inline property, or anything under items/additionalProperties) are invisible
// to the table and reported.
func collectNested(schema *base.Schema, prefix string, depth int, out *[]string) {
	if schema == nil || depth > 16 { // cycle guard; inline schemas cannot recurse but stay bounded
		return
	}
	if schema.Properties != nil {
		for pair := orderedmap.First(schema.Properties); pair != nil; pair = pair.Next() {
			proxy := pair.Value()
			if proxy == nil || proxy.IsReference() {
				continue // named component — covered by its own entry
			}
			sub := proxy.Schema()
			if sub == nil {
				continue
			}
			path := pair.Key()
			if prefix != "" {
				path = prefix + "." + pair.Key()
			}
			if depth >= 1 && isXSensitive(sub) {
				*out = append(*out, path)
			}
			collectNested(sub, path, depth+1, out)
		}
	}
	if schema.Items != nil && schema.Items.IsA() {
		if proxy := schema.Items.A; proxy != nil && !proxy.IsReference() {
			if sub := proxy.Schema(); sub != nil {
				// Item properties belong to a DIFFERENT generated type than the
				// named schema, so any annotation below here is invisible: bump
				// depth so even the items' direct properties report.
				collectNested(sub, prefix+"[]", max(depth, 1), out)
			}
		}
	}
	if schema.AdditionalProperties != nil && schema.AdditionalProperties.IsA() {
		if proxy := schema.AdditionalProperties.A; proxy != nil && !proxy.IsReference() {
			if sub := proxy.Schema(); sub != nil {
				collectNested(sub, prefix+"{}", max(depth, 1), out)
			}
		}
	}
}

const genHeader = "// Code generated by tools/specgen; DO NOT EDIT.\n"

func renderRequired(pkg string, required map[string][]string) []byte {
	var b bytes.Buffer
	b.WriteString(genHeader)
	fmt.Fprintf(&b, "\npackage %s\n\n", pkg)
	b.WriteString("// RequiredFields companions expose each component schema's spec `required:`\n")
	b.WriteString("// array (verbatim json property names). This is a PUBLIC SDK-consumer helper:\n")
	b.WriteString("// downstream code building requests against the generated models can enforce\n")
	b.WriteString("// required fields without re-parsing the spec. The CLI itself does NOT consume\n")
	b.WriteString("// it — `api describe`'s required_fields come from the runtime spec parse\n")
	b.WriteString("// (internal/cli/apispec), so this surface has no in-tree caller by design.\n\n")
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
