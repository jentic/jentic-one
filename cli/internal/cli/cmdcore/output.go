package cmdcore

import (
	"encoding/json"
	"io"
	"os"

	"github.com/charmbracelet/x/term"
	"github.com/spf13/cobra"
)

// isTerminal reports whether stdout is connected to a terminal.
func isTerminal(_ *cobra.Command) bool {
	return term.IsTerminal(os.Stdout.Fd())
}

// JSONOrPretty returns true when the caller should emit JSON output: either
// because --json was explicitly set, or because stdout is not a TTY (agent
// friendly by default).
func JSONOrPretty(cmd *cobra.Command, jsonFlag bool) bool {
	return jsonFlag || !isTerminal(cmd)
}

// WriteJSON encodes v as indented JSON to w.
func WriteJSON(w io.Writer, v any) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}
