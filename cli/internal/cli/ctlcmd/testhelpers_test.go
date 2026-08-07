package ctlcmd

import (
	"bytes"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/spf13/cobra"
)

// testApp builds a ctl-tree receiver backed by a throwaway cmdcore.App with a
// temp-dir Paths and buffered streams, matching the pre-split white-box helper.
func testApp(t *testing.T) *app {
	t.Helper()
	return &app{App: &cmdcore.App{
		Paths: config.Paths{Root: t.TempDir()},
		Out:   new(bytes.Buffer),
		Err:   new(bytes.Buffer),
	}}
}

func hasCommand(root *cobra.Command, name string) bool {
	for _, c := range root.Commands() {
		if c.Name() == name {
			return true
		}
	}
	return false
}
