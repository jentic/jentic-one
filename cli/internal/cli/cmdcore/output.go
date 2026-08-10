package cmdcore

import (
	"bytes"
	"encoding/json"
	"io"
	"os"

	"github.com/charmbracelet/x/term"
	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
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

// WriteJSON encodes v as indented JSON to w, scrubbed by the byte-level
// redaction backstop (SEC-1). These legacy render paths write server-echoed
// payloads (execute envelopes, provider records, access responses) straight to
// stdout, OUTSIDE the ux.safeMarshal funnel — without this pass a secret
// embedded in an upstream response would reach a machine parser verbatim. The
// backstop deliberately does not re-shape the document (no key re-ordering),
// so golden output stays byte-stable except for redacted values. The strangler-
// fig cutover to Audience.Render (which applies the full three-layer funnel)
// retires these call sites over time.
func WriteJSON(w io.Writer, v any) error {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		return err
	}
	_, err := w.Write(ux.RedactBytes(buf.Bytes()))
	return err
}
