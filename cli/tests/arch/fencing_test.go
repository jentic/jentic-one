package arch

import (
	"go/types"
	"testing"
)

// Test1C_SecurityFencing is the guardrail that every host-mutating command
// carries the `fenced` annotation (impl/0.0 §1C, canonical set in impl/3.2 §2a),
// so an autonomous agent cannot hijack the operator's contexts, identities, or
// local-agent lifecycle.
//
// Activation is keyed on the fencing machinery itself, not on a package's mere
// existence: pkg/clitree already ships today as the command-tree composition
// layer, but the `fenced` annotation, the canonical mustBeFenced set, and the
// enforcing PersistentPreRunE all arrive in Phase 2. We detect that machinery by
// the presence of an exported clitree.MustBeFenced symbol. Until it exists there
// is nothing to assert; once it exists this test MUST be upgraded (and fails
// loudly if it wasn't) to walk the constructed tree and assert:
//
//	root := clitree.API()(&core.AppContainer{})
//	– every path in clitree.MustBeFenced has Annotations["fenced"]=="true"
//	– every other mutating command is in the documented exemption set
//	  (migrate, identity register — impl/3.2 §2a)
//	– cobra.EnableTraverseRunHooks == true (else a child PersistentPreRunE
//	  silently disables the fence for its subtree)
func Test1C_SecurityFencing(t *testing.T) {
	pkgs := loadCLI(t)

	var clitreePkg *types.Package
	for _, p := range pkgs {
		if rel(p.PkgPath) == "pkg/clitree" && p.Types != nil {
			clitreePkg = p.Types
		}
	}
	if clitreePkg == nil {
		t.Skip("dormant: pkg/clitree not loaded")
	}
	if clitreePkg.Scope().Lookup("MustBeFenced") == nil {
		t.Skip("dormant: fencing machinery (clitree.MustBeFenced, Phase 2) not present — the `fenced` annotation and its PersistentPreRunE do not exist to assert against")
	}

	// The machinery exists; this stub must have been replaced with the real
	// tree-walk assertion described in the doc comment.
	t.Fatal("clitree.MustBeFenced exists but Test1C_SecurityFencing was not upgraded to assert the fenced contract against the constructed command tree — see the doc comment (impl/0.0 §1C, impl/3.2 §2a)")
}
