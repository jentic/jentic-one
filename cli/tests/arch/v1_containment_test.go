package arch

import (
	"go/ast"
	"testing"

	"golang.org/x/tools/go/packages"
)

// ARCH-21 Part B: V1 containment.
//
// The activation release made the CLI context-only and quarantined the V1
// ~/.jentic profile store behind a single reader — internal/cli/api/
// legacy_store.go — consumed only by `jentic migrate`. Part B made that
// quarantine explicit and minimal. This gate keeps it that way by import/AST
// analysis, not prose: the legacy-store READER API may be referenced only from
// the allowlisted files, so V1 can't creep back, and the day `migrate` retires
// the whole thing is a clean deletion.
//
// Scope note: the reader symbols all live in package `api`, so this is a
// FILE-granularity check (an AST walk + a file-path allowlist), mirroring the
// os.Stdout confinement in boundary_test.go. The remove-only paths in reset.go /
// uninstall.go reference the legacy *path* (config.Paths.ProfilesDir), NOT the
// reader API, so they are deliberately NOT on this allowlist — the gate guards
// who may READ the store, and deletion-by-path is a separate, safe concern.

// v1LegacyReaderSymbols are the identifiers that make up the legacy-store READ
// surface. Any reference to one of these from a file not in
// v1LegacyReaderAllowlist re-opens a V1 read path the teardown closed.
var v1LegacyReaderSymbols = map[string]bool{
	// reader API
	"listLegacyProfiles":   true,
	"viewLegacyProfile":    true,
	"legacyProfile":        true,
	"legacyMeta":           true,
	"legacyTokens":         true,
	"legacyDefaultProfile": true,
	// legacy on-disk name constants (frozen V1 filenames + default profile)
	"legacyProfileFile":        true,
	"legacyTokensFile":         true,
	"legacyKeyFile":            true,
	"legacyAPIKeyFile":         true,
	"legacyAuthModeAPIKey":     true,
	"legacyDefaultProfileName": true,
}

// v1LegacyReaderAllowlist is the set of files (rel-path form) permitted to name
// the reader symbols: the store itself and its sole consumer. Test files are
// exempt (loadCLI already excludes them).
var v1LegacyReaderAllowlist = map[string]bool{
	"internal/cli/api/legacy_store.go": true, // the reader itself
	"internal/cli/api/migrate.go":      true, // the only consumer
}

// TestV1_LegacyStoreHasOnlySanctionedReaders asserts no file outside the
// allowlist references a legacy-store reader symbol.
func TestV1_LegacyStoreHasOnlySanctionedReaders(t *testing.T) {
	pkgs := loadCLI(t)

	forEachFile(pkgs, func(pkgPath string) bool {
		// Only package api defines the reader symbols; a same-spelled identifier
		// in another package is unrelated. Restrict the walk to internal/cli/api.
		return underPrefixes(pkgPath, "internal/cli/api")
	}, func(p *packages.Package, file *ast.File, path string) {
		relFile := rel(p.PkgPath) + "/" + baseName(path)
		if v1LegacyReaderAllowlist[relFile] {
			return
		}
		ast.Inspect(file, func(n ast.Node) bool {
			id, ok := n.(*ast.Ident)
			if !ok || !v1LegacyReaderSymbols[id.Name] {
				return true
			}
			// Confirm the identifier resolves to a package-level object in api
			// (not a local variable of the same spelling) before flagging.
			if obj := p.TypesInfo.ObjectOf(id); obj != nil && obj.Pkg() != nil &&
				obj.Pkg().Path() == p.PkgPath && obj.Parent() == p.Types.Scope() {
				t.Errorf("V1 containment: %s references legacy-store reader symbol %q — "+
					"only %v may read the V1 store (ARCH-21 Part B). Route this through "+
					"`jentic migrate`, or delete the reference.",
					relFile, id.Name, keysOf(v1LegacyReaderAllowlist))
			}
			return true
		})
	})
}

// TestV1_MigrateIsTheOnlyLegacyConsumer is the non-vacuity guard: it proves
// migrate.go actually consumes the reader, so the containment ban above can't
// pass trivially because the reader was deleted or migrate stopped using it. If
// the legacy surface or migrate moves, this fails loudly.
func TestV1_MigrateIsTheOnlyLegacyConsumer(t *testing.T) {
	pkgs := loadCLI(t)

	var found, consumes bool
	forEachFile(pkgs, func(pkgPath string) bool {
		return underPrefixes(pkgPath, "internal/cli/api")
	}, func(p *packages.Package, file *ast.File, path string) {
		if rel(p.PkgPath)+"/"+baseName(path) != "internal/cli/api/migrate.go" {
			return
		}
		found = true
		ast.Inspect(file, func(n ast.Node) bool {
			if id, ok := n.(*ast.Ident); ok && id.Name == "listLegacyProfiles" {
				consumes = true
			}
			return true
		})
	})

	if !found {
		t.Fatal("V1 containment: internal/cli/api/migrate.go not found — the sole legacy " +
			"consumer moved; this non-vacuity guard must not pass silently (ARCH-21 Part B)")
	}
	if !consumes {
		t.Error("V1 containment: migrate.go no longer references listLegacyProfiles — the " +
			"legacy-reader ban would now pass vacuously; if migrate's legacy read moved, " +
			"update this guard and v1LegacyReaderAllowlist")
	}
}

// keysOf returns the keys of a set, for readable failure messages.
func keysOf(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
