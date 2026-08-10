package cmdcore

import (
	"bytes"
	"encoding/json"
	"io"
	"os"

	"github.com/charmbracelet/x/term"
	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// isTerminal reports whether stdout is connected to a terminal.
func isTerminal(_ *cobra.Command) bool {
	return term.IsTerminal(os.Stdout.Fd())
}

// StdoutIsTerminal is the exported TTY probe for command trees outside cmdcore
// (e.g. jenticctl's doctor) that need the same pretty-vs-machine default
// without referencing os.Stdout directly (the 1F boundary confines that to the
// render layer and this file).
func StdoutIsTerminal() bool {
	return term.IsTerminal(os.Stdout.Fd())
}

// JSONOrPretty returns true when the caller should emit JSON output:
//   - --json was explicitly set, or
//   - the resolved mode is a fenced machine mode (agent/service-account), or
//   - mode is EXPLICITLY human → pretty, even piped (UX-5), or
//   - otherwise: stdout is not a TTY (agent friendly by default).
//
// The machine-mode rung (AGT-2) exists because agent harnesses often run the
// CLI on a PTY: JENTIC_MODE=agent must force machine output even on a terminal,
// exactly as it forces fencing and no-color. Any non-human mode counts — an
// unknown mode fails closed to AgentUX at audience construction, so it fails
// closed to JSON here too.
//
// The explicit-human rung (UX-5) is the inverse: --mode human (or
// JENTIC_MODE=human / a persisted human context) says "render for a person",
// so piping to `less`/`tee` keeps the pretty report — previously there was no
// way to force it in a pipe. Only DEFAULT human (nothing set anywhere) falls
// through to the TTY heuristic.
func JSONOrPretty(cmd *cobra.Command, jsonFlag bool) bool {
	if jsonFlag {
		return true
	}
	if state := clictx.FromContext(cmd.Context()); state != nil {
		if state.Mode != clictx.ModeHuman {
			return true
		}
		if state.ModeExplicit {
			return false
		}
	}
	return !isTerminal(cmd)
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
