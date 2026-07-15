package core

import (
	"io"
	"testing"

	"github.com/spf13/cobra"
)

// buildStub returns a minimal built-in-style tree builder for tests.
func buildStub(_ *AppContainer) *cobra.Command {
	root := &cobra.Command{Use: "jentic"}
	root.AddCommand(&cobra.Command{Use: "register"})
	return root
}

func TestNewRootCmdBuildsTree(t *testing.T) {
	deps := &AppContainer{Out: io.Discard, Err: io.Discard}
	root := NewRootCmd(deps, buildStub)

	if root.Use != "jentic" {
		t.Fatalf("root.Use = %q, want %q", root.Use, "jentic")
	}
	if _, _, err := root.Find([]string{"register"}); err != nil {
		t.Fatalf("expected built-in 'register' command to be present: %v", err)
	}
}

func TestNewRootCmdAppendsExtraCommands(t *testing.T) {
	deps := &AppContainer{
		Out: io.Discard,
		Err: io.Discard,
		ExtraCommands: []CommandFactory{
			func(_ *AppContainer) *cobra.Command { return &cobra.Command{Use: "proxy"} },
			func(_ *AppContainer) *cobra.Command { return &cobra.Command{Use: "trust"} },
		},
	}
	root := NewRootCmd(deps, buildStub)

	for _, name := range []string{"register", "proxy", "trust"} {
		if _, _, err := root.Find([]string{name}); err != nil {
			t.Errorf("expected command %q to be present: %v", name, err)
		}
	}
}
