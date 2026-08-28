package ux

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/theme"
)

// ExecuteEnvelope is the versioned success envelope for `jentic execute` (and
// any other surface — e.g. an MCP handler — that relays a brokered upstream
// response): {schema_version, status, headers, body, execution_id?}, matching
// the agent-commands contract. It replaces the ad-hoc inline map the execute
// command used to build, so the CLI and future callers share one struct.
//
// FIELD ORDER IS LOAD-BEARING: encoding/json emits struct fields in declaration
// order, and the golden-pinned envelope was historically marshaled from a map —
// alphabetical key order. The fields are declared alphabetically by JSON key so
// the emitted document stays byte-identical to the frozen contract.
type ExecuteEnvelope struct {
	// Body is the upstream response body: parsed JSON when it decodes, else the
	// raw body as a string.
	Body any `json:"body"`
	// ExecutionID is the broker's Jentic-Execution-Id, when one was stamped.
	ExecutionID string `json:"execution_id,omitempty"`
	// Headers is the single-value projection of the upstream response headers.
	Headers map[string]string `json:"headers"`
	// SchemaVersion pins the machine-contract envelope shape (AGT-23). Use
	// NewExecuteEnvelope so it is always stamped: this envelope is written via
	// the legacy WriteJSON path (its body is arbitrary upstream data), which —
	// unlike Render — does not stamp versions itself.
	SchemaVersion string `json:"schema_version"`
	// Status is the upstream HTTP status the broker relayed.
	Status int `json:"status"`
}

// NewExecuteEnvelope builds an ExecuteEnvelope with the current machine-contract
// schema version stamped.
func NewExecuteEnvelope(status int, headers map[string]string, body any, executionID string) ExecuteEnvelope {
	return ExecuteEnvelope{
		Body:          body,
		ExecutionID:   executionID,
		Headers:       headers,
		SchemaVersion: currentSchemaVersion,
		Status:        status,
	}
}

// Directive mirrors the broker's problem+json agent_directive extension member
// (broker/core/exceptions.py AgentDirective). The strategy vocabulary below
// mirrors broker AgentStrategy; until the contract is a shared OpenAPI schema,
// the Python test test_directive_factories_emit_known_strategies and this list
// must be kept in lock-step (review P1-1).
type Directive struct {
	Strategy    string         `json:"strategy"`
	Parameters  map[string]any `json:"parameters"`
	Instruction string         `json:"human_readable_instruction"`
}

// Recovery strategies the broker may emit in a Directive (mirrors broker
// AgentStrategy): wait, retry, modify_headers, prompt_human, switch_toolkit,
// fatal. Only the ones the rendering branches on are named here.
const (
	DirectiveWait  = "wait"
	DirectiveRetry = "retry"
)

// RenderDirective renders a recovery directive to w (the caller's stderr),
// lifting the suggested_command / provisioning_url out of parameters so the
// agent (or its operator) sees the exact next action without parsing JSON.
// It lives in ux — the terminal gatekeeper — so command code never composes
// styled output itself; the palette is the one the root interceptor resolved
// into ctx.
func RenderDirective(ctx context.Context, w io.Writer, d Directive) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(w, st.Warn.Render("Denied — recovery required:"))
	if d.Instruction != "" {
		fmt.Fprintln(w, "  "+d.Instruction)
	}
	if cmd, ok := d.Parameters["suggested_command"].(string); ok && cmd != "" {
		fmt.Fprintln(w, "  run: "+st.Accent.Render(cmd))
	}
	if u, ok := d.Parameters["provisioning_url"].(string); ok && u != "" {
		fmt.Fprintln(w, "  open: "+st.Accent.Render(u))
	}
	if cands, ok := d.Parameters["candidates"].([]any); ok && len(cands) > 0 {
		parts := make([]string, 0, len(cands))
		for _, c := range cands {
			if s, isStr := c.(string); isStr {
				parts = append(parts, s)
			}
		}
		if len(parts) > 0 {
			fmt.Fprintln(w, "  candidates: "+st.Accent.Render(strings.Join(parts, ", ")))
		}
	}
	// A wait/retry directive carries a backoff hint the agent should honor before
	// retrying; surface it so the recovery loop doesn't hot-spin.
	if d.Strategy == DirectiveWait || d.Strategy == DirectiveRetry {
		if secs, ok := d.Parameters["retry_after_seconds"]; ok {
			fmt.Fprintf(w, "  retry after: %v\n", secs)
		}
	}
	// Catch-all self-check for when the directive's step doesn't unblock (UX9).
	fmt.Fprintln(w, "  stuck? "+st.Accent.Render("jentic doctor"))
}

// RenderSynthesizedDenialRecovery writes a best-effort recovery hint for a
// broker denial that carried no agent_directive, keyed off the HTTP status the
// broker already returned (UX7). It mirrors RenderDirective's shape
// (instruction + `run:` command) so a directive-less denial reads the same as a
// directed one. Unknown statuses fall back to the generic setup check.
func RenderSynthesizedDenialRecovery(ctx context.Context, w io.Writer, status int) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(w, st.Warn.Render("Denied — recovery required:"))
	switch status {
	case http.StatusForbidden: // 403: have an identity, no access to this toolkit yet
		fmt.Fprintln(w, "  This agent isn't bound to the toolkit you called. Check what you can run, then request access.")
		fmt.Fprintln(w, "  run: "+st.Accent.Render("jentic access whoami"))
		fmt.Fprintln(w, "  run: "+st.Accent.Render("jentic access request --toolkit <vendor/name> --wait"))
	case http.StatusFailedDependency: // 424: no credential provisioned for the call
		fmt.Fprintln(w, "  No credential is provisioned for this call. Provision one, then retry.")
		fmt.Fprintln(w, "  run: "+st.Accent.Render("jentic access request --toolkit <vendor/name> --provision --wait"))
	case http.StatusUnauthorized: // 401: credential expired / needs reconnecting
		fmt.Fprintln(w, "  Your credential needs reconnecting. Re-run access to refresh it.")
		fmt.Fprintln(w, "  run: "+st.Accent.Render("jentic access request --toolkit <vendor/name> --provision --wait"))
	default:
		fmt.Fprintln(w, "  The broker denied this call. Check what you can run and your setup.")
		fmt.Fprintln(w, "  run: "+st.Accent.Render("jentic access whoami"))
	}
	// Point at the read-only self-check as the catch-all when the specific hint
	// above doesn't unblock (UX9).
	fmt.Fprintln(w, "  stuck? "+st.Accent.Render("jentic doctor"))
}
