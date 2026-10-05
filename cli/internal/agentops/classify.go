package agentops

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// IsBrokerDenial reports whether a result is one the broker itself emitted to
// deny a call the agent can recover from: missing credential binding → 403
// (wire type no_credential_binding), ambiguous credential binding → 409, credential needs
// reconnect → 401, no credential provisioned → 424. Each carries an
// agent_directive (see broker/web/errors.STATUS_BY_ERROR).
//
// Status alone is NOT sufficient: the broker is a transparent forward proxy, so
// an *upstream* API can return these same 4xx codes on a call the broker
// successfully proxied (the upstream auth failed, the resource is forbidden,
// etc.). Treating those as broker denials would exit 2 and print a misleading
// "recovery required" for a call that actually ran. The broker disambiguates
// with the Jentic-Error-Origin response header (broker/core/headers): it stamps
// "broker" on its own errors and "upstream" on mirrored pass-through 4xx/5xx
// (broker/services/execution/pipeline.enrich_error_origin). So a denial-class
// status is a broker denial only when the origin is not "upstream" (a missing
// header is treated as broker, since the loopback broker always sets it on its
// own errors and only a non-conformant proxy would omit it).
func IsBrokerDenial(r *ExecuteResult) bool {
	if r == nil {
		return false
	}
	if errorOrigin(r) == errorOriginUpstream {
		return false
	}
	switch r.Status {
	case http.StatusUnauthorized,
		http.StatusForbidden,
		http.StatusConflict,
		http.StatusFailedDependency:
		return true
	default:
		return false
	}
}

// ParseAgentDirective extracts an agent_directive from a denial result's body.
// It only treats recoverable broker-denial responses as directives so a normal
// 4xx (including an upstream pass-through with an incidental
// "agent_directive"-shaped body) can't trip the caller's exit code.
func ParseAgentDirective(r *ExecuteResult) (ux.Directive, bool) {
	directive, _, ok := parseAgentDirectiveRaw(r)
	return directive, ok
}

// parseAgentDirectiveRaw extracts both the typed projection (what the CLI's
// renderer branches on) and the verbatim agent_directive JSON sub-object (what
// the MCP payload relays — unknown future broker fields must survive the
// round-trip, which a struct projection would silently drop).
func parseAgentDirectiveRaw(r *ExecuteResult) (ux.Directive, json.RawMessage, bool) {
	if !IsBrokerDenial(r) {
		return ux.Directive{}, nil, false
	}
	var envelope struct {
		Directive json.RawMessage `json:"agent_directive"`
	}
	if err := json.Unmarshal(r.Body, &envelope); err != nil ||
		len(envelope.Directive) == 0 || string(envelope.Directive) == "null" {
		return ux.Directive{}, nil, false
	}
	var directive ux.Directive
	if json.Unmarshal(envelope.Directive, &directive) != nil {
		return ux.Directive{}, nil, false
	}
	return directive, envelope.Directive, true
}

// parseProblemType extracts the problem+json "type" member from a denial
// body ("no_credential_binding", "action_denied", …). Empty when the body is
// not parseable — callers must treat that as unknown and pick the safe
// recovery, never assume a shape.
func parseProblemType(r *ExecuteResult) string {
	var envelope struct {
		Type string `json:"type"`
	}
	if err := json.Unmarshal(r.Body, &envelope); err != nil {
		return ""
	}
	return envelope.Type
}

// Classify is the unfused classification step (response → denial-or-not) the
// old executeOutput performed inline with printing: nil for every non-denial
// result (2xx, upstream pass-through 4xx/5xx), else the Denial carrying the
// denying status and the parsed directive (nil Directive when the body carried
// none — the caller synthesizes a status-keyed recovery hint). The exit code
// keys off the *status*, not the presence of an agent_directive: some denials
// (e.g. action_denied from a permission rule) carry no directive, and gating
// on a parsed directive would let those silently exit 0.
func Classify(r *ExecuteResult) *Denial {
	if !IsBrokerDenial(r) {
		return nil
	}
	d := &Denial{Status: r.Status, ProblemType: parseProblemType(r)}
	if directive, raw, ok := parseAgentDirectiveRaw(r); ok {
		d.Directive = &directive
		d.DirectiveRaw = raw
	}
	return d
}

// BrokerError classifies a broker-origin 4xx that is not a recoverable denial:
// the broker could not resolve the call (an unknown or unbound credential id or
// name, an unregistered operation) and never reached the upstream, so it must
// not read as an upstream response that exits 0 (#1429). It returns
// RESOLVE_FAILED (exit 2) carrying the problem detail, or nil.
//
// Unlike IsBrokerDenial it requires Jentic-Error-Origin to say "broker"
// explicitly: 400/404/422 are exactly what an upstream answers on a call that
// ran, so a missing header keeps the pass-through reading rather than turning a
// real upstream answer into a failure. Broker 5xx is out of scope here; whether
// those should be retryable is a separate decision.
func BrokerError(r *ExecuteResult) *ux.CodedError {
	if r == nil || r.Status < 400 || r.Status >= 500 || IsBrokerDenial(r) ||
		errorOrigin(r) != errorOriginBroker {
		return nil
	}
	var problem struct {
		Type   string `json:"type"`
		Title  string `json:"title"`
		Detail string `json:"detail"`
	}
	_ = json.Unmarshal(r.Body, &problem) // best effort: FastAPI's array-form detail just leaves Detail empty
	reason := problem.Detail
	if reason == "" {
		reason = problem.Title
	}
	if reason == "" {
		reason = http.StatusText(r.Status)
	}
	details := map[string]any{"http_status": r.Status, "origin": errorOriginBroker}
	if problem.Type != "" {
		details["problem_type"] = problem.Type
	}
	return &ux.CodedError{
		Code: ux.CodeResolveFailed,
		Msg: fmt.Sprintf("the broker could not resolve this call, so it never reached the upstream API (HTTP %d): %s",
			r.Status, reason),
		Actionable: "Fix the request instead of retrying it: check the operation is registered (`jentic apis list`) and that " +
			"any Jentic-Credential-Id or Jentic-Credential-Name header names a credential bound to it (`jentic creds list`).",
		Details: details,
	}
}

// errorOriginUpstream is the Jentic-Error-Origin value the broker stamps on a
// mirrored upstream response (broker ErrorOrigin.UPSTREAM). The matching header
// name mirrors broker/core/headers.JenticHeader.ERROR_ORIGIN.
const errorOriginUpstream = "upstream"

// errorOriginBroker is the value the broker stamps on the problem responses it
// emits itself (broker ErrorOrigin.BROKER, broker/web/errors.py).
const errorOriginBroker = "broker"

func errorOrigin(r *ExecuteResult) string {
	if r == nil {
		return ""
	}
	return strings.ToLower(strings.TrimSpace(r.Headers.Get("Jentic-Error-Origin")))
}
