package arch

import (
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/pkg/clitree"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// Test1C_SecurityFencing is the guardrail that every command in the canonical
// fence set (clitree.MustBeFenced, impl/3.2 §2a) carries the `fenced` annotation,
// so an autonomous agent cannot hijack the operator's local-agent lifecycle,
// contexts, or identities. It also asserts cobra.EnableTraverseRunHooks is set,
// because without it a subcommand's own PersistentPreRunE would silently disable
// the fencing hook for its whole subtree.
//
// This test was DORMANT until the fencing machinery (clitree.MustBeFenced) shipped
// in Phase 2; it now walks the constructed command tree and asserts the contract.
// As Phase 3 adds context/env/identity commands, they extend clitree.MustBeFenced
// and this test covers them automatically.
func Test1C_SecurityFencing(t *testing.T) {
	if len(clitree.MustBeFenced) == 0 {
		t.Fatal("clitree.MustBeFenced is empty; the canonical fence set must list every host-mutating command")
	}

	// Build the real jentic tree the same way the binary does.
	root := clitree.API()(&core.AppContainer{})

	for _, path := range clitree.MustBeFenced {
		fields := strings.Fields(path) // "context use" -> ["context","use"]
		cmd, _, err := root.Find(fields)
		if err != nil {
			t.Errorf("fenced command %q not found in the jentic tree: %v", path, err)
			continue
		}
		// root.Find returns the nearest match; confirm we resolved the exact leaf,
		// not a parent (e.g. a typo'd path that resolves to root).
		if cmd.Name() != fields[len(fields)-1] {
			t.Errorf("fenced path %q resolved to %q, not the intended leaf", path, cmd.CommandPath())
			continue
		}
		if cmd.Annotations["fenced"] != "true" {
			t.Errorf("command %q is NOT fenced; agent mode could run it. Wrap its constructor in fenced(...)", path)
		}
	}

	// The fencing hook lives on the parent root; a child PersistentPreRunE would
	// bypass it unless traversal is enabled (impl/3.2 §2 "COBRA HAZARD").
	if !cobra.EnableTraverseRunHooks {
		t.Error("cobra.EnableTraverseRunHooks is false; a subcommand's own PersistentPreRunE would silently disable fencing for its subtree")
	}
}
