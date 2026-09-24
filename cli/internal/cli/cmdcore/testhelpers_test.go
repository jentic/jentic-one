package cmdcore

import (
	"bytes"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
)

// testApp builds a throwaway App with a temp-dir Paths and buffered streams,
// matching the pre-split white-box helper.
func testApp(t *testing.T) *App {
	t.Helper()
	return &App{
		Paths: config.Paths{Root: t.TempDir()},
		Out:   new(bytes.Buffer),
		Err:   new(bytes.Buffer),
	}
}
