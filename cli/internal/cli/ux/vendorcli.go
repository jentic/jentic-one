package ux

import (
	"io"
)

// VendorCLIDirectivePrefix starts the one stderr line a vendor CLI wrapper
// (`jentic cf …`) adds to the vendor CLI's own output. The rest of the line is
// one JSON object, so an agent can find it among the vendor CLI's stderr.
const VendorCLIDirectivePrefix = "jentic: "

// VendorCLIDirective is the payload of a vendor CLI wrapper's directive line:
// the recovery step (the same {strategy, parameters, human_readable_instruction}
// shape as the broker's agent_directive) and the trace id the command's calls
// are recorded under, when one was minted.
//
// The wrapper's exit code is always the vendor CLI's own (or 1 when the CLI
// never ran), so Jentic outcomes travel only in this line.
type VendorCLIDirective struct {
	Directive Directive `json:"directive"`
	TraceID   string    `json:"trace_id,omitempty"`
}

// RenderVendorCLIDirective writes d to w (the caller's stderr) as a single
// `jentic: {…}` line, through the same redaction funnel as every other output
// path. The line is plain in every mode: it is meant to be found and parsed
// among the vendor CLI's own output, not styled.
func RenderVendorCLIDirective(w io.Writer, d VendorCLIDirective) {
	if d.Directive.Parameters == nil {
		d.Directive.Parameters = map[string]any{}
	}
	line := append([]byte(VendorCLIDirectivePrefix), safeMarshal(d)...)
	_, _ = w.Write(append(line, '\n'))
}
