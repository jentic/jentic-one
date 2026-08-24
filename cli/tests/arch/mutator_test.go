package arch

import (
	"go/ast"
	"testing"

	"golang.org/x/tools/go/packages"
)

// configWriterAllowlist names the files permitted to call os.WriteFile /
// os.Rename against config/state/key material (impl/0.0 §1E). The V2 target set
// is client/config/writer.go, client/auth/tokens.go, client/auth/keys.go. Until
// those land, the shipped atomic writers below are the legitimate owners. As
// the migration moves each writer, update this list — an unlisted writer is a
// test failure, which is the point: it stops an agent hand-rolling a
// flock-bypassing writer that corrupts config.yaml under concurrent registration.
var configWriterAllowlist = map[string]bool{
	// V2 targets (Phase 1) — listed ahead of time so they pass on arrival.
	"client/config/writer.go": true,
	"client/auth/tokens.go":   true,
	"client/auth/keys.go":     true,
	"client/auth/apikey.go":   true, // API-key credential (0600 secret under XDG state; sibling of tokens.go)
	"client/auth/atomic.go":   true, // shared atomic writer (temp+fsync+rename) for the 0600 credential files above (F6, review round-3 #7)

	// Phase 3 migration writer: copies validated legacy key material into the XDG
	// layout and drops the MIGRATED marker. It writes SECRET/STATE material (0600
	// key files) and a marker, not config.yaml — the config.yaml mutation itself
	// still goes through config.MutateConfig (the flock-guarded path).
	"internal/cli/api/migrate.go": true,

	// Shipped writers (grandfathered; each is an atomic temp-file+rename or a
	// 0600 secret/state write, not an ad-hoc config clobber).
	"internal/config/file.go":              true, // writeFileAtomic -> config.yaml (the mutator today)
	"internal/config/manifest.go":          true, // install manifest
	"internal/profile/store.go":            true, // profile state (atomic)
	"internal/agentkey/key.go":             true, // Ed25519 key material (0600)
	"internal/skillgen/apply.go":           true, // rendered skill files (atomic)
	"internal/cli/localagentcmd/skills.go": true, // task skill files written to agent runtime dirs
	"internal/update/update.go":            true, // self-update binary swap (atomic)
	"internal/update/download.go":          true, // extracts a verified release binary into the update stage dir (STATE, not config.yaml; sha256/cosign-verified before write)
	"internal/install/start.go":            true, // pid file
	"internal/install/compose.go":          true, // compose/init-schemas artifacts
	"internal/install/build.go":            true, // build output tree
	"internal/cli/cmdcore/updatenudge.go":  true, // update-check timestamp (0600)
	"internal/cli/ctlcmd/install.go":       true, // install writes env/compose out
	"internal/cli/api/apis.go":             true, // apis export to user-chosen -o path
	"internal/cli/api/history.go":          true, // history export to user-chosen -o path (redacted JSON, not config)
	"internal/cli/ctlcmd/update.go":        true, // fetched installer script
	"internal/cli/ctlcmd/uninstall.go":     true, // backup/restore moves
}

// Test1E_ConfigMutatorLock asserts every os.WriteFile / os.Rename in production
// code lives in an allow-listed writer file. This is the AST form of the rule
// "all config writes go through the flock-guarded MutateConfig".
func Test1E_ConfigMutatorLock(t *testing.T) {
	pkgs := loadCLI(t)

	forEachFile(pkgs, func(pkgPath string) bool {
		return underPrefixes(pkgPath, "internal", "client")
	}, func(p *packages.Package, file *ast.File, path string) {
		relFile := rel(p.PkgPath) + "/" + baseName(path)
		if configWriterAllowlist[relFile] {
			return
		}
		hits := make([]int, 0, 2)
		hits = append(hits, selectorCalls(p.TypesInfo, p.Fset, file, "os", "WriteFile")...)
		hits = append(hits, selectorCalls(p.TypesInfo, p.Fset, file, "os", "Rename")...)
		for _, line := range hits {
			t.Errorf("%s:%d: os.WriteFile/os.Rename outside the writer allowlist — route config writes through config.MutateConfig (impl/1.3), or add this file to configWriterAllowlist with a justification if it is a legitimate state/secret writer",
				relFile, line)
		}
	})
}
