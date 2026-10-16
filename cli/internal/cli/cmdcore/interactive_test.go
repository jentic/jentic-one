package cmdcore

import (
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
)

// WantsInteractive must treat a TERM=dumb TTY as non-interactive so register /
// setup take their flag-driven path instead of opening an uncancellable huh
// accessible-mode form (jentic-one#841).
func TestWantsInteractive_DumbTerminal(t *testing.T) {
	orig := prompt.StdinIsTerminal
	prompt.StdinIsTerminal = func() bool { return true }
	t.Cleanup(func() { prompt.StdinIsTerminal = orig })
	cmd := &cobra.Command{Use: "register"}

	t.Setenv("TERM", "xterm-256color")
	if !WantsInteractive(cmd, false, registerFieldFlags...) {
		t.Fatal("a real TTY with a normal TERM should be interactive")
	}
	t.Setenv("TERM", "dumb")
	if WantsInteractive(cmd, false, registerFieldFlags...) {
		t.Fatal("TERM=dumb must not be interactive")
	}
}
