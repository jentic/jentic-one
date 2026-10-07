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
// the broker could not resolve the call (an unknown credential id or name, an
// unregistered operation, a malformed request) and never reached the upstream,
// so it must not read as an upstream response that exits 0 (#1429). It returns
// RESOLVE_FAILED (exit 2) carrying the problem reason, or nil.
//
// Unlike IsBrokerDenial it requires Jentic-Error-Origin to say "broker"
// explicitly: 400/404/422 are exactly what an upstream answers on a call that
// ran, so a missing header keeps the pass-through reading rather than turning a
// real upstream answer into a failure. Because of that origin requirement the
// denial-status exclusion is equivalent to skipping 401/403/409/424 — those stay
// on the Classify (denial) path.
//
// Two broker-origin statuses are deliberately left as pass-through, because
// RESOLVE_FAILED means "change the ask, don't retry" and both are retryable as
// sent: 429 (rate_limit_exceeded, with Retry-After) and every 5xx. Their coded
// mapping is tracked separately (#1531).
func BrokerError(r *ExecuteResult) *ux.CodedError {
	if r == nil || r.Status < 400 || r.Status >= 500 || r.Status == http.StatusTooManyRequests ||
		IsBrokerDenial(r) || errorOrigin(r) != errorOriginBroker {
		return nil
	}
	// The broker renders every problem through problem_body: the message rides
	// `title` (there is no `detail` member), and a request-validation 422 carries
	// its field errors in `errors` ([{loc, msg}]). `detail` is still read first so
	// a conformant RFC 9457 body that does set it is honoured.
	var problem struct {
		Type       string          `json:"type"`
		Title      string          `json:"title"`
		Detail     json.RawMessage `json:"detail"`
		Errors     json.RawMessage `json:"errors"`
		Candidates json.RawMessage `json:"candidates"`
	}
	_ = json.Unmarshal(r.Body, &problem) // best effort: a non-JSON body falls back to the status text
	reason := problemReason(problem.Detail, problem.Title, problem.Errors, r.Status)
	details := map[string]any{"http_status": r.Status, "origin": errorOriginBroker}
	if problem.Type != "" {
		details["problem_type"] = problem.Type
	}
	// The broker embeds the caller's own covering credentials on an unknown
	// Jentic-Credential-Id/Name (orchestrator._resolve_mapped). Relaying them
	// spares the agent a follow-up whoami/`jentic creds list` just to learn which
	// ids exist, so attach them verbatim when present.
	candidates := decodeCandidates(problem.Candidates)
	if candidates != nil {
		details["candidates"] = candidates
	}
	return &ux.CodedError{
		Code: ux.CodeResolveFailed,
		Msg: fmt.Sprintf("the broker could not resolve this call, so it never reached the upstream API (HTTP %d): %s",
			r.Status, reason),
		Actionable: brokerErrorActionable(BrokerErrorRecoveryFor(r.Status, problem.Type), candidates),
		Details:    details,
	}
}

// BrokerErrorRecovery names what a broker resolve failure needs fixed. The CLI
// actionable prose and the MCP next_tool both key on it, so the two lanes never
// disagree about where to send the agent.
type BrokerErrorRecovery string

const (
	// RecoverOperation means no registered operation serves the call — rediscover it.
	RecoverOperation BrokerErrorRecovery = "operation"
	// RecoverCredential means a Jentic-Credential-Id/Name header named a credential
	// the broker could not resolve — pick a valid one.
	RecoverCredential BrokerErrorRecovery = "credential"
	// RecoverContract means any other broker 4xx (method, revision pin, payload size,
	// idempotency key, egress-blocked URL, request validation) — fix the request
	// against the operation's contract.
	RecoverContract BrokerErrorRecovery = "contract"
)

// brokerCredentialProblemTypes are the wire types the broker emits when a
// Jentic-Credential-Id/Name header names a credential it cannot resolve for the
// caller (broker/services/credentials/orchestrator.py). Matched exactly: a
// substring test would silently re-route any future type that merely mentions
// "credential".
var brokerCredentialProblemTypes = map[string]bool{
	"credential_id_not_found":   true,
	"credential_name_not_found": true,
}

// BrokerErrorRecoveryFor routes a broker resolve failure by status and exact
// problem type: a 404 or operation_not_found is a discovery problem, a known
// credential-resolution type is a header problem, and everything else is a
// contract problem (never a bare identity check, which cannot fix a malformed
// request).
func BrokerErrorRecoveryFor(status int, problemType string) BrokerErrorRecovery {
	switch {
	case status == http.StatusNotFound || problemType == "operation_not_found":
		return RecoverOperation
	case brokerCredentialProblemTypes[problemType]:
		return RecoverCredential
	default:
		return RecoverContract
	}
}

// problemReason renders the human-readable reason from a broker problem body:
// `detail` (a string verbatim, or a validation array flattened), else `title`,
// else the HTTP status text — with the flattened `errors` array appended when
// present, so a validation 422 names WHICH field was wrong instead of only the
// generic "Request validation failed" title.
func problemReason(detail json.RawMessage, title string, errs json.RawMessage, status int) string {
	reason := flattenDetail(detail)
	if reason == "" {
		reason = title
	}
	if reason == "" {
		reason = http.StatusText(status)
	}
	if fieldErrs := flattenValidationErrors(errs); fieldErrs != "" {
		reason += " (" + fieldErrs + ")"
	}
	return reason
}

// flattenDetail renders a problem+json `detail` member to a string: a JSON
// string verbatim, or a validation array ([{loc, msg}, …]) via
// flattenValidationErrors. Any other shape (or an unparseable one) yields "".
func flattenDetail(detail json.RawMessage) string {
	if len(detail) == 0 {
		return ""
	}
	var asString string
	if json.Unmarshal(detail, &asString) == nil {
		return asString
	}
	return flattenValidationErrors(detail)
}

// flattenValidationErrors renders a validation error array ([{loc, msg}, …] —
// the broker's 422 `errors` member, sanitized of submitted input) as its
// semicolon-joined messages, each prefixed with its dotted location when one is
// present. Any other shape (or an unparseable one) yields "".
func flattenValidationErrors(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var items []struct {
		Loc []any  `json:"loc"`
		Msg string `json:"msg"`
	}
	if json.Unmarshal(raw, &items) != nil {
		return ""
	}
	msgs := make([]string, 0, len(items))
	for _, item := range items {
		if item.Msg == "" {
			continue
		}
		if loc := joinLoc(item.Loc); loc != "" {
			msgs = append(msgs, loc+": "+item.Msg)
		} else {
			msgs = append(msgs, item.Msg)
		}
	}
	return strings.Join(msgs, "; ")
}

// joinLoc renders a validation error `loc` ([... "header", "x-id"]) as a dotted
// path, formatting non-string segments (array indices) with %v.
func joinLoc(loc []any) string {
	parts := make([]string, 0, len(loc))
	for _, seg := range loc {
		if s, ok := seg.(string); ok {
			parts = append(parts, s)
		} else {
			parts = append(parts, fmt.Sprintf("%v", seg))
		}
	}
	return strings.Join(parts, ".")
}

// decodeCandidates relays the broker's candidate-credential list verbatim when
// it is a JSON array (orchestrator attaches it as a top-level `candidates`
// member on an unknown credential id or name). Any other shape yields nil so the
// detail carries no half-parsed field.
func decodeCandidates(raw json.RawMessage) []any {
	if len(raw) == 0 {
		return nil
	}
	var candidates []any
	if json.Unmarshal(raw, &candidates) != nil || len(candidates) == 0 {
		return nil
	}
	return candidates
}

// candidateLabels renders relayed candidate records ({id, name, last4, …}) as
// "id (name)" labels so the recovery line names a valid Jentic-Credential-Id
// directly. Records without an id are skipped; never the secret — the broker
// sends only id, name, a last4 display hint and created_at.
func candidateLabels(candidates []any) []string {
	labels := make([]string, 0, len(candidates))
	for _, c := range candidates {
		switch v := c.(type) {
		case string:
			if v != "" {
				labels = append(labels, v)
			}
		case map[string]any:
			id, _ := v["id"].(string)
			if id == "" {
				continue
			}
			if name, _ := v["name"].(string); name != "" {
				labels = append(labels, fmt.Sprintf("%s (%s)", id, name))
			} else {
				labels = append(labels, id)
			}
		}
	}
	return labels
}

// brokerErrorActionable is the CLI recovery prose for each BrokerErrorRecovery,
// so the advice is never noise for the other cases. A credential failure names
// the relayed candidates inline: the root error reporter renders Actionable as
// the single "next step" on stderr (and as actionable_step in the agent
// envelope), so the fix appears exactly once per stream.
func brokerErrorActionable(rec BrokerErrorRecovery, candidates []any) string {
	switch rec {
	case RecoverOperation:
		return "The broker has no such operation registered. Find the right one with `jentic search <query>`, " +
			"then re-issue the call against a registered operation — do not retry this one."
	case RecoverCredential:
		hint := "A Jentic-Credential-Id or Jentic-Credential-Name header named a credential the broker could " +
			"not resolve for this operation. Re-issue the call naming one bound to this operation — do not " +
			"retry the same header."
		if labels := candidateLabels(candidates); len(labels) > 0 {
			return hint + " Candidates: " + strings.Join(labels, ", ") + "."
		}
		return hint + " List your bound credentials with `jentic creds list`."
	default:
		return "The broker rejected the request before it reached the upstream API. Re-check the " +
			"operation's contract (`jentic apis inspect`) — the method, revision pin, payload size, " +
			"idempotency key, or a required header is wrong — and fix the request rather than retrying it."
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
