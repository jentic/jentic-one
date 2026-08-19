package arch

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
	"gopkg.in/yaml.v3"

	"github.com/jentic/jentic-one/cli/internal/cli/api"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// vendoredSpecs are the module-relative locations the Phase-1 codegen vendors
// the two OpenAPI specs to (impl/2.1). 1G/1H read these, not the source specs,
// because go:embed can only see files under the embedding package.
var vendoredSpecs = []string{
	"client/generated/control/spec.yaml",
	"client/generated/broker/spec.yaml",
}

// specsPresent returns the existing vendored-spec paths (Phase 1 artifact).
func specsPresent() (paths []string, ok bool) {
	for _, s := range vendoredSpecs {
		p := filepath.Join("..", "..", s) // tests/arch (test CWD) -> cli/
		if _, err := os.Stat(p); err == nil {
			paths = append(paths, p)
		}
	}
	return paths, len(paths) > 0
}

// curatedRegistryPresent reports whether the Phase-2 curated-command registry
// exists yet. 1G reflects over that registry (command -> generated struct ->
// notExposed), so its real dependency is the registry itself, NOT merely the
// presence of the internal/cli/api tree. Phase 8 relocated the shipped command
// tree into internal/cli/api (the binary split) WITHOUT introducing the curated
// registry, so a bare directory probe would trip 1G spuriously. We therefore
// probe for the registry artifact — a non-test `curated.go` in the api package
// that declares the command->struct->notExposed table — which is what impl/7.0
// §2 actually lands. Until that file appears, 1G has nothing to reflect over.
func curatedRegistryPresent() bool {
	// The curated-command registry lands as internal/cli/api/curated.go
	// (impl/7.0 §2, Phase 2). Its presence is the signal that there are curated
	// structs to reflect over. Until then 1G has nothing to check — note the
	// mere existence of the internal/cli/api package (Phase 8 binary split) is
	// NOT the signal.
	if _, err := os.Stat(filepath.Join("..", "..", "internal", "cli", "api", "curated.go")); err == nil {
		return true
	}
	return false
}

// Test1G_SpecFlagCoverageParity ensures every exported field of a curated
// command's generated params/body struct is either bound to a flag or listed in
// that command's notExposed set (impl/0.0 §1G). The compiler catches spec
// renames/removals; this catches *additions* — a new optional param that a
// curated command would otherwise silently never expose.
//
// Active since the curated-command registry (internal/cli/api/curated.go)
// landed: it reflects over each registry entry against the REAL assembled
// command tree, so a spec addition after `make generate-api` fails here until
// a human classifies the new field (bind it, or record why not).
func Test1G_SpecFlagCoverageParity(t *testing.T) {
	if !curatedRegistryPresent() {
		t.Skip("dormant: curated-command registry (internal/cli/api, Phase 2) not present — no commands to reflect over yet")
	}
	root := api.NewDocsRoot()
	for _, b := range api.CuratedBindings() {
		t.Run(strings.ReplaceAll(b.Command, " ", "_"), func(t *testing.T) {
			cmd := findCommand(t, root, b.Command)

			flags := map[string]bool{}
			cmd.Flags().VisitAll(func(f *pflag.Flag) { flags[f.Name] = true })

			fields := jsonFieldNames(t, reflect.TypeOf(b.Params))

			for _, name := range fields {
				flag, bound := b.Bind[name]
				_, excluded := b.NotExposed[name]
				switch {
				case bound && excluded:
					t.Errorf("field %q is in BOTH Bind and NotExposed — pick one", name)
				case !bound && !excluded:
					t.Errorf("NEW spec field %q on %T is unclassified: bind it to a flag on `jentic %s`, "+
						"or add it to NotExposed with a one-line reason (impl/0.0 §1G)", name, b.Params, b.Command)
				case bound && flag != api.PositionalArg && !flags[flag]:
					t.Errorf("field %q claims flag --%s, but `jentic %s` has no such flag", name, flag, b.Command)
				}
			}

			// Staleness: a Bind/NotExposed key naming a since-removed field means
			// the registry no longer matches the spec.
			known := map[string]bool{}
			for _, name := range fields {
				known[name] = true
			}
			for name := range b.Bind {
				if !known[name] {
					t.Errorf("Bind references field %q which %T no longer has — remove the stale entry", name, b.Params)
				}
			}
			for name := range b.NotExposed {
				if !known[name] {
					t.Errorf("NotExposed references field %q which %T no longer has — remove the stale entry", name, b.Params)
				}
			}
		})
	}
}

// findCommand resolves a space-separated command path under the root.
func findCommand(t *testing.T, root *cobra.Command, path string) *cobra.Command {
	t.Helper()
	cmd := root
	for _, part := range strings.Fields(path) {
		var next *cobra.Command
		for _, sub := range cmd.Commands() {
			if sub.Name() == part {
				next = sub
				break
			}
		}
		if next == nil {
			t.Fatalf("command %q not found under %q (registry path stale?)", part, cmd.CommandPath())
		}
		cmd = next
	}
	return cmd
}

// jsonFieldNames returns the json names of a struct's exported, serialized
// fields (skipping `json:"-"` and untagged unexported plumbing like union's
// raw message).
func jsonFieldNames(t *testing.T, typ reflect.Type) []string {
	t.Helper()
	if typ.Kind() != reflect.Struct {
		t.Fatalf("curated Params must be a struct, got %s", typ)
	}
	var names []string
	for i := range typ.NumField() {
		f := typ.Field(i)
		if !f.IsExported() {
			continue
		}
		tag := f.Tag.Get("json")
		name := strings.Split(tag, ",")[0]
		if name == "-" {
			continue
		}
		if name == "" {
			name = f.Name
		}
		names = append(names, name)
	}
	return names
}

// isSensitiveKey delegates to the redaction engine's exported predicate
// (ux.IsSensitiveKey) so the sweep asserts against the SAME "secret-shaped"
// definition the runtime redactor uses — allowlist included (F8-35). A previous
// version kept a hand-maintained copy of the exact/suffix tables here, which
// omitted the runtime allowlist (next_token, has_api_key, …) and so could drift
// from what actually gets redacted. Sharing one function makes drift impossible.
func isSensitiveKey(key string) bool { return ux.IsSensitiveKey(key) }

// sensitiveSweepAllowlist is the reviewed false-positive set for
// Test1H_SensitiveAnnotationSweep. A property name here is exempt from the
// "must carry x-sensitive: true" requirement because it is secret-shaped but
// not a secret (a boolean flag, a count, a masked/redacted view).
//
// The backend `x-sensitive` annotation pass (impl/2.1 §4b, GEN-9) landed: every
// real secret-carrying field is annotated at its Pydantic source
// (`Field(json_schema_extra=SENSITIVE)`, see shared/web/sensitive.py), so this
// list holds ONLY genuine false positives. A new secret-shaped field must be
// annotated in the backend model — never added here unless it truly carries no
// secret value.
var sensitiveSweepAllowlist = map[string]bool{
	"has_api_key":          true, // presence flag, not the key
	"must_change_password": true, // policy boolean
	"clear_session_token":  true, // "clear the token?" boolean directive
}

// Test1H_SensitiveAnnotationSweep walks every schema property in the vendored
// specs — recursing into nested inline objects, array `items`, and
// `additionalProperties` sub-schemas (GEN-6) — and fails on any secret-shaped
// property name (per impl/3.1 §1's exact-key/suffix heuristics) that lacks
// `x-sensitive: true` and is not in the reviewed allowlist (impl/0.0 §1H). This
// makes someone classify every NEW secret-shaped field before it can ship, so
// Layer-1 typed redaction is driven by the spec, not by naming luck.
//
// Activation: needs the vendored specs (Phase 1) and the shared isSensitiveKey
// heuristic (delegated to ux.IsSensitiveKey, the runtime redactor's own
// predicate — F8-35). Both exist, so this runs for real.
func Test1H_SensitiveAnnotationSweep(t *testing.T) {
	paths, ok := specsPresent()
	if !ok {
		t.Skip("dormant: vendored specs (Phase 1) not present — no schema properties to sweep")
	}

	type finding struct{ spec, schema, prop string }
	var unclassified []finding

	for _, p := range paths {
		data, err := os.ReadFile(p)
		if err != nil {
			t.Fatalf("read spec %s: %v", p, err)
		}
		var doc struct {
			Components struct {
				Schemas map[string]map[string]any `yaml:"schemas"`
			} `yaml:"components"`
		}
		if err := yaml.Unmarshal(data, &doc); err != nil {
			t.Fatalf("parse spec %s: %v", p, err)
		}
		specName := filepath.Base(filepath.Dir(p))
		for schemaName, schema := range doc.Components.Schemas {
			sweepSchema(schema, "", func(propPath string, propSchema map[string]any) {
				prop := lastPathSegment(propPath)
				if !isSensitiveKey(prop) {
					return
				}
				if xSensitiveTrue(propSchema) {
					return // properly annotated — good
				}
				if sensitiveSweepAllowlist[prop] {
					return // reviewed false positive / pending backend annotation
				}
				unclassified = append(unclassified, finding{spec: specName, schema: schemaName, prop: propPath})
			})
		}
	}

	if len(unclassified) > 0 {
		msgs := make([]string, 0, len(unclassified))
		for _, f := range unclassified {
			msgs = append(msgs, f.spec+" "+f.schema+"."+f.prop)
		}
		sort.Strings(msgs)
		t.Fatalf("secret-shaped spec properties without `x-sensitive: true` and not allowlisted (impl/0.0 §1H, impl/2.1 §4b):\n  %s\n"+
			"Fix: annotate the field with `x-sensitive: true` in the backend Pydantic model, OR add it to sensitiveSweepAllowlist "+
			"with a one-line reason if it is a genuine false positive.", strings.Join(msgs, "\n  "))
	}
}

// sweepSchema recursively visits every property of a YAML schema node,
// including nested inline objects, array `items`, and `additionalProperties`
// sub-schemas (GEN-6). $ref'ed sub-schemas are skipped — the referenced named
// component is swept as its own schemas entry. visit receives the dotted
// property path and the property's schema node.
func sweepSchema(node map[string]any, prefix string, visit func(path string, propSchema map[string]any)) {
	if node == nil || node["$ref"] != nil {
		return
	}
	if props, ok := node["properties"].(map[string]any); ok {
		for name, raw := range props {
			propSchema, isMap := raw.(map[string]any)
			if !isMap {
				continue
			}
			path := name
			if prefix != "" {
				path = prefix + "." + name
			}
			if propSchema["$ref"] == nil {
				visit(path, propSchema)
			}
			sweepSchema(propSchema, path, visit)
		}
	}
	if items, ok := node["items"].(map[string]any); ok {
		sweepSchema(items, prefix+"[]", visit)
	}
	if extra, ok := node["additionalProperties"].(map[string]any); ok {
		sweepSchema(extra, prefix+"{}", visit)
	}
}

// lastPathSegment returns the property NAME from a dotted sweep path
// ("connect.client_secret" -> "client_secret"), stripping the []/{} markers.
func lastPathSegment(path string) string {
	if i := strings.LastIndex(path, "."); i >= 0 {
		path = path[i+1:]
	}
	path = strings.TrimSuffix(path, "[]")
	return strings.TrimSuffix(path, "{}")
}

// xSensitiveTrue reports whether a property schema carries `x-sensitive: true`.
func xSensitiveTrue(propSchema map[string]any) bool {
	v, ok := propSchema["x-sensitive"]
	if !ok {
		return false
	}
	b, ok := v.(bool)
	return ok && b
}

// TestSensitiveTablesRegistered drift-gates the GEN-2 wiring: specgen emits a
// SensitiveFields table per plane, but the tables are INERT unless something
// hands them to ux.RegisterSensitiveFields at composition time. This walks the
// non-test sources of internal/cli/cmdcore (the composition point both binaries
// and downstream embedders link) and requires a registration call for each
// plane. Without this gate the wiring could be deleted and every `x-sensitive`
// annotation the backend ever adds would silently stop reaching layer-1
// redaction — the exact dormancy Test1H's sweep assumes cannot happen.
func TestSensitiveTablesRegistered(t *testing.T) {
	dir := filepath.Join("..", "..", "internal", "cli", "cmdcore")
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("read cmdcore dir: %v", err)
	}
	var src strings.Builder
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		data, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatalf("read %s: %v", name, err)
		}
		src.Write(data)
	}
	for _, want := range []string{
		"ux.RegisterSensitiveFields(",
		"control.SensitiveFields)",
		"broker.SensitiveFields)",
	} {
		if !strings.Contains(src.String(), want) {
			t.Errorf("cmdcore no longer registers a generated sensitive-fields table: missing %q\n"+
				"Fix: restore the init in internal/cli/cmdcore/redaction.go — specgen's x-sensitive output is inert without it (GEN-2).", want)
		}
	}
}

// curatedMigrationAllowlist lists generated *Params/*Request types that ARE
// constructed by a command but are deliberately NOT (yet) in CuratedBindings()
// (QA-3/GEN-4). Two legitimate reasons: (a) the type is used by a non-command
// helper (e.g. pagination inside `jentic api`/negotiation) where there is no
// single user-facing flag surface to reflect over, or (b) the command is still
// on internal/apiclient and migrates into the registry later. Every entry needs
// a one-line reason; the meta-test fails on any UNLISTED constructed type so a
// new curated-eligible command cannot silently skip the 1G parity gate.
//
// Currently EMPTY: every generated request struct constructed in the command
// package is registered in CuratedBindings(). A new one must be registered
// (preferred) or added here with a reason.
var curatedMigrationAllowlist = map[string]string{
	// admin config providers set: the body's only field is Config (a free-form
	// map[string]any validated server-side by provider name), not spec-derived
	// scalar fields — so binder flag-coverage parity (Test1G) does not apply. The
	// command builds Config from named flags (--project-id/--client-id/…) plus a
	// prompted client_secret (ARCH-21 A1, migrated off internal/adminclient).
	"control.SetProviderConfigJSONRequestBody": "free-form Config map, no reflectable scalar fields; built from named admin-provider flags",
	// access request items: AccessRequestItemRequest is a NESTED builder inside
	// AccessRequestFileRequest (registered as a CuratedBinding) — its fields are
	// access-plan mechanics (resource_type/action enums, resource_reference,
	// rules) synthesised by compose()/buildProvisionPlan() from the request
	// command's target flags, not 1:1 CLI flags. The file-request binding already
	// gives 1G a reflectable surface (ARCH-21 A3, off internal/accessclient).
	"control.AccessRequestItemRequest": "nested plan-item builder under the access-request file body; fields synthesised from request-command flags, not 1:1 flags",
}

// TestCuratedRegistryCoversGeneratedStructs is the QA-3/GEN-4 meta-test: it
// AST-scans the shipped command package (internal/cli/api) for every
// construction of a generated control.*/broker.* request struct (a composite
// literal of a type whose name ends in "Params" or "Request") and requires each
// such type to be EITHER registered in CuratedBindings() OR present in the
// reviewed curatedMigrationAllowlist. Without it, a new command could construct
// a generated params struct, gain optional spec fields over time, and silently
// never expose them — the exact drift Test1G exists to prevent, but which 1G
// only checks for commands someone remembered to register.
func TestCuratedRegistryCoversGeneratedStructs(t *testing.T) {
	dir := filepath.Join("..", "..", "internal", "cli", "api")
	if _, err := os.Stat(filepath.Join(dir, "curated.go")); err != nil {
		t.Skip("dormant: curated registry not present")
	}

	registered := map[string]bool{}
	for _, b := range api.CuratedBindings() {
		registered[reflect.TypeOf(b.Params).String()] = true
	}

	constructed := scanGeneratedStructConstructions(t, dir)

	var missing []string
	for typ := range constructed {
		if registered[typ] {
			continue
		}
		if _, ok := curatedMigrationAllowlist[typ]; ok {
			continue
		}
		missing = append(missing, typ)
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		t.Fatalf("generated request struct(s) constructed in internal/cli/api but neither registered in "+
			"CuratedBindings() nor allowlisted (QA-3/GEN-4):\n  %s\n"+
			"Fix: add a CuratedBinding for the command that builds it (so Test1G reflects over its fields), "+
			"or add it to curatedMigrationAllowlist with a one-line reason.", strings.Join(missing, "\n  "))
	}

	// Staleness: an allowlist entry for a type nobody constructs anymore is dead.
	for typ := range curatedMigrationAllowlist {
		if !constructed[typ] {
			t.Errorf("curatedMigrationAllowlist has stale entry %q — no longer constructed in internal/cli/api; remove it", typ)
		}
	}
}

// scanGeneratedStructConstructions parses every non-test .go file in dir and
// returns the set of `control.X{...}` / `broker.X{...}` composite-literal type
// names that carry user-bindable request data. It matches:
//   - the generated request PARAMS/BODY structs (name ends "Params"/"Request"), and
//   - request-body UNION MEMBER structs (GEN-20): types a command hand-builds as
//     the actual wire payload (e.g. control.ApiSourceUrl / ApiSourceInline). These
//     don't carry the Params/Request suffix, so the old suffix-only filter made
//     them invisible to BOTH the 1G parity gate and this coverage net — a new
//     optional field on one (submitted_by) shipped silently unwired.
//
// It excludes generated union CONTAINER wrappers (names containing "_Item"):
// those are the oapi-codegen union envelope (a raw json.RawMessage), have no
// user-facing fields to bind, and are populated only via their From*/Merge*
// methods — reflecting over them would be meaningless.
//
// It is deliberately syntactic — no type checking — so it stays fast and has no
// build dependency on the analysed package.
func scanGeneratedStructConstructions(t *testing.T, dir string) map[string]bool {
	t.Helper()
	fset := token.NewFileSet()
	found := map[string]bool{}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("read api dir: %v", err)
	}
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		f, err := parser.ParseFile(fset, filepath.Join(dir, name), nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", name, err)
		}
		ast.Inspect(f, func(n ast.Node) bool {
			cl, ok := n.(*ast.CompositeLit)
			if !ok {
				return true
			}
			sel, ok := cl.Type.(*ast.SelectorExpr)
			if !ok {
				return true
			}
			pkg, ok := sel.X.(*ast.Ident)
			if !ok || (pkg.Name != "control" && pkg.Name != "broker") {
				return true
			}
			typ := sel.Sel.Name
			// Skip oapi-codegen union container wrappers: no bindable fields.
			if strings.Contains(typ, "_Item") {
				return true
			}
			// A generated struct construction is in scope if it is a request
			// params/body (suffix) OR a request-body union member with fields set
			// in the literal (GEN-20). An empty composite literal (control.Foo{})
			// is a zero-value registration in curated.go, already covered by the
			// suffix arm when applicable; a NON-empty literal with fields is a real
			// payload build we must cover.
			if strings.HasSuffix(typ, "Params") || strings.HasSuffix(typ, "Request") || len(cl.Elts) > 0 {
				found[pkg.Name+"."+typ] = true
			}
			return true
		})
	}
	return found
}
