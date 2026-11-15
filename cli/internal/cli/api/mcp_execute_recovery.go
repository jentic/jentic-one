package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strconv"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// executeResolveError maps a resolve failure per the §3.7 table: an unknown
// operation is RESOLVE_FAILED with "re-search" guidance pointed at search_apis
// (deliberate — the identity already resolved; the id is wrong, so get_started
// would be a dead end). Non-resolve codes (an auth failure surfacing inside
// the inspect call) keep their own default pointers.
func (s *mcpServer) executeResolveError(ctx context.Context, target string, err error) *mcp.CallToolResult {
	s.logger.Warn("execute resolve failed", "target", target, "error", redactedErr(err))
	coded := mcpCoded(err)
	if coded.Code != ux.CodeResolveFailed {
		return s.softError(ctx, err)
	}
	return s.softErrorNext(ctx, &ux.CodedError{
		Code: ux.CodeResolveFailed,
		Msg:  coded.Msg,
		Actionable: "Call search_apis with a natural-language description of what you want to do, then " +
			"inspect_operation on a hit to confirm the contract before executing it.",
	}, "search_apis")
}

// executeTransportError maps a transport failure per the §3.7 table: a soft
// TRANSPORT_ERROR with the get_started pointer — on a local install a dead
// broker usually means the instance is down, which get_started diagnoses with
// the exact operator instruction. The instance stamp on the result degrades
// naturally when the control plane is down too.
//
// The retryable hint is earned, not blanket: `retryable: true` only when the
// caller proved the retry safe (GET/HEAD, or an Idempotency-Key the broker
// de-duplicates on) or the failure is provably pre-send (dial/TLS/connection
// refused — the upstream never saw the request). A mid-flight failure on an
// unkeyed mutating call (deadline exceeded, connection reset) may have
// ALREADY been delivered: hinting a retry there instructs the model to
// double-execute, so it gets `retryable: false` plus recovery guidance
// (verify first, idempotency_key, get_execution_result for held jobs).
// get_execution_result shares this helper for its own transport failures
// so they surface with hints rather than as a bare INTERNAL_ERROR.
func (s *mcpServer) executeTransportError(ctx context.Context, err error, retrySafe bool) *mcp.CallToolResult {
	s.logger.Warn("transport failure", "error", redactedErr(err), "retry_safe", retrySafe)
	coded := asCoded(err)
	if coded.Code != ux.CodeTransportError {
		return s.softError(ctx, err)
	}
	retryable := retrySafe || agentops.TransportFailurePreSend(coded)
	if !retryable {
		coded = &ux.CodedError{
			Code: coded.Code,
			Msg:  coded.Msg + " — the failure happened mid-flight, so the request may already have reached the upstream",
			Actionable: "Do not re-send this call blindly: it may have executed. Verify the effect with a read " +
				"(execute_read) first, or re-send with an idempotency_key so the broker de-duplicates the retry. " +
				"If the execute returned a held (202) call, poll it with get_execution_result using its " +
				"held_execution_id — never re-send.",
			Details: coded.Details,
			Cause:   coded.Cause,
		}
	}
	return s.softErrorExtra(ctx, coded, "get_started", map[string]any{"retryable": retryable})
}

// executeBrokerTransportError maps a broker-origin 5xx/429 (agentops.
// BrokerTransportError) onto a soft TRANSPORT_ERROR. retryable is the hint the
// classify core earned from the problem type, and retry_after_s relays the
// broker's Retry-After when it sent one, so the model can wait the right amount
// before re-sending. next_tool is search_apis for an unsupported scheme (the
// agent needs a different operation) and get_started otherwise, the same
// pointer the other transport failures use.
func (s *mcpServer) executeBrokerTransportError(ctx context.Context, coded *ux.CodedError) *mcp.CallToolResult {
	status, _ := coded.Details["http_status"].(int)
	pt, _ := coded.Details["problem_type"].(string)
	retryable, _ := coded.Details["retryable"].(bool)
	retryAfter, hasRetryAfter := coded.Details["retry_after_s"].(float64)
	s.logger.Warn("broker transport failure", "status", status, "problem_type", pt, "retryable", retryable)

	rec := agentops.BrokerTransportRecoveryFor(status, pt)
	nextTool := "get_started"
	if rec == agentops.RecoverScheme {
		nextTool = "search_apis"
	}
	coded.Actionable = brokerTransportToolHint(rec, retryable, retryAfter, hasRetryAfter)
	extra := map[string]any{"retryable": retryable}
	if hasRetryAfter {
		extra["retry_after_s"] = retryAfter
	}
	return s.softErrorExtra(ctx, coded, nextTool, extra)
}

// brokerTransportToolHint is the MCP-lane recovery prose for a broker
// transport failure, phrased for this lane's tools (the classify core phrases
// it for the CLI).
func brokerTransportToolHint(rec agentops.BrokerTransportRecovery, retryable bool, retryAfter float64, hasRetryAfter bool) string {
	wait := "Wait briefly"
	if hasRetryAfter {
		wait = fmt.Sprintf("Wait retry_after_s (%ss)", strconv.FormatFloat(retryAfter, 'f', -1, 64))
	}
	switch {
	case rec == agentops.RecoverWait:
		return wait + " and re-send the same call: the broker refused it before it reached the upstream API, " +
			"so a retry cannot double-execute it. If it keeps failing, report it to your user: the broker is degraded."
	case rec == agentops.RecoverTimeout && retryable:
		return wait + " and re-send the same call (it is a read or carries an idempotency_key, so a retry " +
			"cannot double-execute it)."
	case rec == agentops.RecoverScheme:
		return "The broker has no runner for this upstream's URL scheme, so re-sending cannot succeed. Call " +
			"search_apis for an operation served over http(s)."
	default:
		return "Do not re-send this call blindly: the broker failed it in a way that may have reached the " +
			"upstream API. Verify the effect with a read (execute_read) first, or re-send with an " +
			"idempotency_key so the broker de-duplicates the retry. If it keeps failing, report it to your user."
	}
}

// classifyTransportErr maps a generated-client transport failure (the request
// never completed: no *HTTPError, no meaningful taxonomy code) onto the §3.7
// transport row, so callers degrade like execute does — TRANSPORT_ERROR with
// the retryable hint and the get_started pointer — instead of INTERNAL_ERROR
// ("stop, CLI bug" semantics) with no recovery. Completed calls (*HTTPError)
// and already-classifiable failures (auth, pending, no-config) pass through
// untouched.
func classifyTransportErr(err error) error {
	var he *HTTPError
	if err == nil || errors.As(err, &he) {
		return err
	}
	if coded := asCoded(err); coded.Code != ux.CodeInternalError {
		return err
	}
	return &ux.CodedError{
		Code:  ux.CodeTransportError,
		Msg:   fmt.Sprintf("transport error: %v", err),
		Cause: err,
	}
}

// executeDenialError maps a broker denial per the §3.7 table: a soft
// BROKER_DENIED with the agent_directive relayed as the RAW JSON sub-object
// the broker sent (the broker's recovery instructions must reach the model
// intact — a struct projection would silently drop unknown future fields),
// retryable: false — re-sending the same call cannot succeed until access
// changes. next_tool forks on the problem+json type (theme-7 Phase 1b):
// provisioning-shaped denials (no_credential_binding /
// credential_not_provisioned) point at request_connection — the agent can
// start the credential-provisioning leg itself — while everything else
// (action_denied, credential_identity_mismatch, credential_binding_suspended,
// unknown types) keeps whoami
// ("check your bindings, never execute to probe access", §3.2). Binding an
// existing credential and scope grants stay operator actions; the hints keep
// saying so.
func (s *mcpServer) executeDenialError(ctx context.Context, denial *agentops.Denial) *mcp.CallToolResult {
	coded := denial.Err()
	extra := map[string]any{"retryable": false}
	if denial.Directive != nil {
		extra["agent_directive"] = denial.DirectiveRaw
		coded.Actionable = denial.Directive.Instruction
	}
	if coded.Actionable == "" {
		// UX7's synthesized recovery, tool-flavored: no denial is a dead end.
		coded.Actionable = synthesizedDenialHint(denial.Status, denial.ProblemType)
	}
	nextTool := denialNextTool(denial.ProblemType, denial.Directive)
	if nextTool == "request_connection" {
		if args := requestConnectionArguments(denial.Directive); len(args) > 0 {
			extra["next_tool_arguments"] = args
		}
	}
	return s.softErrorExtra(ctx, coded, nextTool, extra)
}

// provisioningProblemTypes are the problem+json types whose recovery
// request_connection can start (theme-7 Phase 1b): a missing credential
// binding where nothing is provisioned (no_credential_binding) and a
// resolved-but-unprovisioned
// credential (credential_not_provisioned, 424). Everything else — notably
// action_denied (a permission rule forbids the op; connecting a fresh
// credential must NOT be taught as a way around it),
// credential_identity_mismatch and credential_undecryptable (operator fixes
// the credential), credential_binding_suspended (only the operator resumes
// the binding; a new connect is refused), and any unknown type — keeps whoami.
var provisioningProblemTypes = map[string]bool{
	"no_credential_binding":      true,
	"credential_not_provisioned": true,
	// Retired with the toolkit path in 0.41 (theme-5 Phase 6b), but a 0.40.x
	// server on its legacy flag-off toolkit path still emits it — this CLI
	// may talk to one mid-upgrade, so keep treating it as provisioning-shaped.
	"no_toolkit_binding": true,
}

// denialNextTool picks the recovery pointer for a broker denial, keyed on the
// problem+json type — never the bare HTTP status: 403 also covers
// action_denied and credential_identity_mismatch, whose directives say the
// opposite of "connect a credential", so a status-keyed fork would teach the
// model to file connect sessions to route around permission rules. whoami is
// the safe default for anything unrecognized.
//
// Even a provisioning-shaped denial points at request_connection only when the
// broker's directive names a connect target (parameters.connect or
// parameters.suggested_command) — the broker sets them exactly when a
// vendor-registry entry or a shared OAuth app covers the API. Otherwise (or
// with no directive to name the vendor) request_connection is guaranteed to
// fail as an unknown vendor, so the pointer stays on whoami and the directive's
// operator hand-off.
//
// A rules denial is change-shaped instead: its directive carries the edit to
// ask for (parameters.edit), so the pointer names the tool that asks rather
// than sending the agent to re-read an identity it already knows.
func denialNextTool(problemType string, directive *ux.Directive) string {
	if provisioningProblemTypes[problemType] && directive != nil {
		if connectVendorKey(directive) != "" {
			return "request_connection"
		}
		if cmd, _ := directive.Parameters["suggested_command"].(string); cmd != "" {
			return "request_connection"
		}
	}
	if directive != nil {
		if edit, ok := directive.Parameters["edit"].(map[string]any); ok && edit != nil {
			return "request_access_change"
		}
	}
	return "whoami"
}

// connectVendorKey is the connect key a denial directive names in its
// structured parameters.connect.vendor_key, or "" (Python twin:
// _connect_vendor_key). A directive from a server that predates the field
// carries only parameters.suggested_command.
func connectVendorKey(directive *ux.Directive) string {
	if directive == nil {
		return ""
	}
	connect, _ := directive.Parameters["connect"].(map[string]any)
	key, _ := connect["vendor_key"].(string)
	return key
}

// connectAPI is the registry API a denial directive names as its connect
// target in parameters.connect.api ({vendor, name, version}), or nil — set
// when the deployment takes connect requests for any registry API and no
// vendor key covers the denied one (Python twin: _connect_api).
func connectAPI(directive *ux.Directive) map[string]any {
	if directive == nil {
		return nil
	}
	connect, _ := directive.Parameters["connect"].(map[string]any)
	api, _ := connect["api"].(map[string]any)
	out := map[string]any{}
	for _, k := range []string{"vendor", "name", "version"} {
		v, _ := api[k].(string)
		if v == "" {
			return nil
		}
		out[k] = v
	}
	return out
}

// connectRegistrationID is the shared OAuth app a denial directive pins in
// parameters.connect.registration_id, or "" (Python twin:
// _connect_registration_id).
func connectRegistrationID(directive *ux.Directive) string {
	if directive == nil {
		return ""
	}
	connect, _ := directive.Parameters["connect"].(map[string]any)
	id, _ := connect["registration_id"].(string)
	return id
}

// requestConnectionArguments are the request_connection arguments a denial
// directive fills: the vendor key — with oauth_app_registration_id when the
// directive pins the one shared app covering the API — or else the api
// identity. The directive's suggested_rules stay in agent_directive (Python
// twin: _request_connection_arguments).
func requestConnectionArguments(directive *ux.Directive) map[string]any {
	if key := connectVendorKey(directive); key != "" {
		args := map[string]any{"vendor": key}
		if id := connectRegistrationID(directive); id != "" {
			args["oauth_app_registration_id"] = id
		}
		return args
	}
	if api := connectAPI(directive); api != nil {
		return map[string]any{"api": api}
	}
	return nil
}

// synthesizedDenialHint is the MCP counterpart of the CLI's status-keyed
// denial recovery (ux.RenderSynthesizedDenialRecovery): same semantics,
// phrased for a model that can call tools but must relay operator commands.
// The connect-flavored wording is gated on the provisioning-shaped problem
// types, mirroring denialNextTool — an action_denied 403 must never be
// answered with "start a connect session".
func synthesizedDenialHint(status int, problemType string) string {
	if provisioningProblemTypes[problemType] {
		return "No credential binding covers this API for this agent. Call whoami to see your bindings. " +
			"If nothing serves the API, call request_connection with the vendor's registry key to start " +
			"connecting a credential yourself (your operator approves the approval_url); if a credential " +
			"already serves it, ask your operator to bind you to it (dashboard) — binding is always a " +
			"human action."
	}
	switch status {
	case http.StatusForbidden:
		return "The broker denied this call. Call whoami to see your bindings and permissions; if a " +
			"permission rule forbids this operation, ask your operator to adjust it — do not try to " +
			"route around a rule by connecting a new credential."
	case http.StatusConflict:
		return "Multiple bound credentials cover this API. Resend the same call with the " +
			"Jentic-Credential-Id header naming one of them (Jentic-Credential-Name also works " +
			"when names are unique); whoami lists your bindings."
	case http.StatusFailedDependency:
		return "A stored credential dependency is unusable for this call. Ask your operator to " +
			"re-provision the credential in the dashboard, then retry."
	case http.StatusUnauthorized:
		return "The stored upstream credential needs reconnecting. Ask your operator to re-provision it " +
			"in the dashboard, then retry."
	default:
		return "The broker denied this call before it reached the upstream API. Call whoami to check what you can run."
	}
}

// brokerErrorToolHint is the MCP-lane recovery prose for a broker resolve
// failure, keyed on the next_tool brokerErrorNextTool chose so the two never
// disagree. It names tools this surface serves, never CLI commands.
func brokerErrorToolHint(nextTool string) string {
	switch nextTool {
	case "search_apis":
		return "The broker has no such operation registered. Call search_apis to find the right " +
			"operation, confirm it with inspect_operation, then execute that one — do not retry this call."
	case "whoami":
		return "A Jentic-Credential-Id or Jentic-Credential-Name header named a credential the broker could " +
			"not resolve for this operation. Pick a valid id from details.candidates when present (else call " +
			"whoami to see your credential bindings) and re-issue the call naming one bound to this operation — " +
			"do not retry the same header."
	default: // inspect_operation
		return "The broker rejected the request before it reached the upstream API. Call inspect_operation " +
			"to re-read the operation's contract — the method, revision pin, payload size, idempotency key, or a " +
			"required header is wrong — and fix the request rather than retrying it."
	}
}

// brokerErrorNextTool points a broker resolve failure at the tool that can fix
// it, keyed on agentops.BrokerErrorRecoveryFor (the same routing the CLI prose
// uses): an unregistered operation needs search_apis, an unresolvable
// credential header gets whoami (the same safe default the denial path uses),
// and any other broker 4xx — a contract or payload error — points at
// inspect_operation so the agent re-reads the contract instead of looping on an
// identity check that cannot fix it. Never get_started: the identity already
// resolved.
func brokerErrorNextTool(coded *ux.CodedError) string {
	status, _ := coded.Details["http_status"].(int)
	pt, _ := coded.Details["problem_type"].(string)
	switch agentops.BrokerErrorRecoveryFor(status, pt) {
	case agentops.RecoverOperation:
		return "search_apis"
	case agentops.RecoverCredential:
		return "whoami"
	default:
		return "inspect_operation"
	}
}
