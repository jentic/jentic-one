package cmdcore

import (
	"bytes"
	"context"
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
)

// TestJSONOrPrettyModeRungs pins the two mode rungs (AGT-2 + UX-5). Tests run
// with a non-TTY stdout, so the TTY-heuristic default is JSON — which makes the
// explicit-human rung observable: it must flip the answer to pretty.
func TestJSONOrPrettyModeRungs(t *testing.T) {
	cmdWith := func(state *clictx.ActiveState) *cobra.Command {
		cmd := &cobra.Command{Use: "x"}
		ctx := context.Background()
		if state != nil {
			ctx = clictx.WithActiveState(ctx, state)
		}
		cmd.SetContext(ctx)
		return cmd
	}

	// --json always wins.
	if !JSONOrPretty(cmdWith(&clictx.ActiveState{Mode: clictx.ModeHuman, ModeExplicit: true}), true) {
		t.Error("--json must force JSON even in explicit human mode")
	}
	// Machine modes force JSON regardless of TTY (AGT-2).
	if !JSONOrPretty(cmdWith(&clictx.ActiveState{Mode: clictx.ModeAgent, ModeExplicit: true}), false) {
		t.Error("agent mode must force JSON")
	}
	// Explicit human pins pretty even piped (UX-5).
	if JSONOrPretty(cmdWith(&clictx.ActiveState{Mode: clictx.ModeHuman, ModeExplicit: true}), false) {
		t.Error("explicit human mode must pin pretty output even when stdout is piped")
	}
	// Default human (nothing set): non-TTY heuristic → JSON under `go test`.
	if !JSONOrPretty(cmdWith(&clictx.ActiveState{Mode: clictx.ModeHuman}), false) {
		t.Error("default human on a piped stdout must stay agent-friendly JSON")
	}
	// No resolved state at all: same heuristic.
	if !JSONOrPretty(cmdWith(nil), false) {
		t.Error("stateless piped stdout must default to JSON")
	}
}

// TestWriteJSONRedactsSensitiveValues is the SEC-1 regression: the legacy JSON
// render paths (cmdcore.WriteJSON and api's writeJSON delegate) sit outside the
// ux.safeMarshal funnel, so the byte-level backstop MUST run inside WriteJSON
// itself — a bearer token or api key echoed by a broker/third-party response
// must never reach stdout verbatim.
func TestWriteJSONRedactsSensitiveValues(t *testing.T) {
	var buf bytes.Buffer
	payload := map[string]any{
		"name":       "prov-1",
		"api_key":    "jak_supersecret123",
		"auth":       "Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig",
		"next_token": "cursor-ok", // allowlisted: must survive
	}
	if err := WriteJSON(&buf, payload); err != nil {
		t.Fatalf("WriteJSON: %v", err)
	}
	out := buf.String()
	if strings.Contains(out, "jak_supersecret123") {
		t.Errorf("api_key value leaked: %s", out)
	}
	if strings.Contains(out, "eyJhbGciOiJSUzI1NiJ9") {
		t.Errorf("bearer token leaked: %s", out)
	}
	if !strings.Contains(out, "cursor-ok") {
		t.Errorf("allowlisted next_token was clobbered: %s", out)
	}
	if !strings.Contains(out, "[REDACTED]") {
		t.Errorf("no redaction marker present: %s", out)
	}
}

// TestWriteJSONKeepsShape: the backstop must not reshape the document — same
// keys, same order, same indentation — only sensitive values change.
func TestWriteJSONKeepsShape(t *testing.T) {
	var buf bytes.Buffer
	type row struct {
		B string `json:"b"`
		A string `json:"a"`
	}
	if err := WriteJSON(&buf, row{B: "2", A: "1"}); err != nil {
		t.Fatalf("WriteJSON: %v", err)
	}
	want := "{\n  \"b\": \"2\",\n  \"a\": \"1\"\n}\n"
	if buf.String() != want {
		t.Errorf("shape changed:\ngot  %q\nwant %q", buf.String(), want)
	}
}
