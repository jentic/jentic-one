package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/theme"
)

// printSynthesizedDenialRecovery prints a best-effort recovery hint for a broker
// denial that carried no agent_directive, keyed off the HTTP status the broker
// already returned (UX7). It mirrors printAgentDirective's shape (instruction +
// `run:` command) so a directive-less denial reads the same as a directed one.
// Unknown statuses fall back to the generic setup check.
func (a *app) printSynthesizedDenialRecovery(status int) {
	fmt.Fprintln(a.Err, theme.Warn.Render("Denied — recovery required:"))
	switch status {
	case http.StatusForbidden: // 403: have an identity, no access to this toolkit yet.
		fmt.Fprintln(a.Err, "  This agent isn't bound to the toolkit you called. Check what you can run, then request access.")
		fmt.Fprintln(a.Err, "  run: "+theme.Accent.Render("jentic access whoami"))
		fmt.Fprintln(a.Err, "  run: "+theme.Accent.Render("jentic access request --toolkit <vendor/name> --wait"))
	case http.StatusFailedDependency: // 424: no credential provisioned for the call.
		fmt.Fprintln(a.Err, "  No credential is provisioned for this call. Provision one, then retry.")
		fmt.Fprintln(a.Err, "  run: "+theme.Accent.Render("jentic access request --toolkit <vendor/name> --provision --wait"))
	case http.StatusUnauthorized: // 401: credential expired / needs reconnecting.
		fmt.Fprintln(a.Err, "  Your credential needs reconnecting. Re-run access to refresh it.")
		fmt.Fprintln(a.Err, "  run: "+theme.Accent.Render("jentic access request --toolkit <vendor/name> --provision --wait"))
	default:
		fmt.Fprintln(a.Err, "  The broker denied this call. Check what you can run and your setup.")
		fmt.Fprintln(a.Err, "  run: "+theme.Accent.Render("jentic access whoami"))
	}
	// Point at the read-only self-check as the catch-all when the specific hint
	// above doesn't unblock (UX9).
	fmt.Fprintln(a.Err, "  stuck? "+theme.Accent.Render("jentic doctor"))
}

// agentDirective mirrors the broker's problem+json agent_directive extension
// member (broker/core/exceptions.py AgentDirective). The strategy vocabulary
// below mirrors broker AgentStrategy; until the contract is a shared OpenAPI
// schema, the Python test test_directive_factories_emit_known_strategies and
// this list must be kept in lock-step (review P1-1).
type agentDirective struct {
	Strategy    string         `json:"strategy"`
	Parameters  map[string]any `json:"parameters"`
	Instruction string         `json:"human_readable_instruction"`
}

// Recovery strategies the broker may emit in an agent_directive (mirrors
// broker AgentStrategy): wait, retry, modify_headers, prompt_human,
// switch_toolkit, fatal. Only the ones this CLI branches on are named here.
const (
	directiveWait  = "wait"
	directiveRetry = "retry"
)

// parseAgentDirective extracts an agent_directive from a denial response body.
// It only treats recoverable broker-denial responses as directives so a normal
// 4xx (including an upstream pass-through with an incidental
// "agent_directive"-shaped body) can't trip the exit code.
func parseAgentDirective(resp *http.Response, body []byte) (agentDirective, bool) {
	if !isBrokerDenial(resp) {
		return agentDirective{}, false
	}
	var envelope struct {
		Directive *agentDirective `json:"agent_directive"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil || envelope.Directive == nil {
		return agentDirective{}, false
	}
	return *envelope.Directive, true
}

// printAgentDirective renders a recovery directive to stderr, lifting the
// suggested_command / provisioning_url out of parameters so the agent (or its
// operator) sees the exact next action without parsing JSON.
func (a *app) printAgentDirective(d agentDirective) {
	fmt.Fprintln(a.Err, theme.Warn.Render("Denied — recovery required:"))
	if d.Instruction != "" {
		fmt.Fprintln(a.Err, "  "+d.Instruction)
	}
	if cmd, ok := d.Parameters["suggested_command"].(string); ok && cmd != "" {
		fmt.Fprintln(a.Err, "  run: "+theme.Accent.Render(cmd))
	}
	if u, ok := d.Parameters["provisioning_url"].(string); ok && u != "" {
		fmt.Fprintln(a.Err, "  open: "+theme.Accent.Render(u))
	}
	if cands, ok := d.Parameters["candidates"].([]any); ok && len(cands) > 0 {
		parts := make([]string, 0, len(cands))
		for _, c := range cands {
			if s, isStr := c.(string); isStr {
				parts = append(parts, s)
			}
		}
		if len(parts) > 0 {
			fmt.Fprintln(a.Err, "  candidates: "+theme.Accent.Render(strings.Join(parts, ", ")))
		}
	}
	// A wait/retry directive carries a backoff hint the agent should honor before
	// retrying; surface it so the recovery loop doesn't hot-spin.
	if d.Strategy == directiveWait || d.Strategy == directiveRetry {
		if secs, ok := d.Parameters["retry_after_seconds"]; ok {
			fmt.Fprintf(a.Err, "  retry after: %v\n", secs)
		}
	}
	// Catch-all self-check for when the directive's step doesn't unblock (UX9).
	fmt.Fprintln(a.Err, "  stuck? "+theme.Accent.Render("jentic doctor"))
}
