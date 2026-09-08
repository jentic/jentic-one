package clitree_test

import (
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/pkg/clitree"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// Test that a downstream binary can compose the built-in tree via the exported
// builder + core.NewRootCmd, and that ExtraCommands are appended (not shadowed).
func TestDownstreamCompositionAppendsExtraCommands(t *testing.T) {
	for name, build := range map[string]core.TreeBuilder{
		"api": clitree.API(),
		"ctl": clitree.Ctl(),
	} {
		t.Run(name, func(t *testing.T) {
			extra := func(*core.AppContainer) *cobra.Command {
				return &cobra.Command{Use: "ent-only-xyz"}
			}
			deps := &core.AppContainer{ExtraCommands: []core.CommandFactory{extra}}
			root := core.NewRootCmd(deps, build)

			// The built-in tree must be present (has subcommands) ...
			if !root.HasSubCommands() {
				t.Fatalf("%s: built-in tree has no subcommands", name)
			}
			// ... and our injected command must be appended.
			var found bool
			for _, c := range root.Commands() {
				if c.Use == "ent-only-xyz" {
					found = true
					break
				}
			}
			if !found {
				t.Fatalf("%s: injected ExtraCommand was not appended to the root", name)
			}
		})
	}
}
