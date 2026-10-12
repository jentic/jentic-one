package api

// connect_target.go holds what `jentic connect` and the stdio
// request_connection tool share for connect sessions that target a registry
// API (`--api` / the tool's `api` argument) rather than a vendor key: target
// parsing, the flow-aware relay guidance, the coded mapping of the API-target
// error codes, and the terminal /status outcomes. A human enters (or pastes) the
// credential for those sessions, so they can stay open for hours: the agent
// relays the approval_url, ends its turn and retries the blocked call later —
// it never blocks on a long wait.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// connectAuthTypeMax mirrors the route's auth_type bound
// (IntegrationsConnectRequest.auth_type — max_length=255).
const connectAuthTypeMax = 255

// flowAwaitingApp is the resolved_flow of an OAuth API with no OAuth app to
// connect through yet: an approver supplies one (or binds an existing
// credential) before the sign-in can start.
const flowAwaitingApp = "awaiting_app"

// isHumanEntryFlow reports whether a resolved_flow waits on a human typing a
// credential (manual_api_key / manual_bearer / manual_basic) or resolving an
// OAuth app (awaiting_app). Those sessions live for hours, so the relay advice
// is "end your turn and retry later", never "wait".
func isHumanEntryFlow(flow string) bool {
	return flow == flowAwaitingApp || strings.HasPrefix(flow, "manual_")
}

// parseConnectAPI parses a `vendor/name/version` API identity into the
// route's api target.
func parseConnectAPI(ref string) (*control.ApiTargetRequest, error) {
	vendor, name, version, err := parseAPIRef(strings.TrimSpace(ref))
	if err != nil {
		return nil, err
	}
	return &control.ApiTargetRequest{Vendor: vendor, Name: name, Version: version}, nil
}

// decodeConnectAPIArg reads the request_connection `api` argument: either a
// {vendor, name, version} object or a "vendor/name/version" string.
func decodeConnectAPIArg(raw json.RawMessage) (*control.ApiTargetRequest, error) {
	var slug string
	if json.Unmarshal(raw, &slug) == nil {
		return parseConnectAPI(slug)
	}
	var target control.ApiTargetRequest
	if err := json.Unmarshal(raw, &target); err != nil ||
		target.Vendor == "" || target.Name == "" || target.Version == "" {
		return nil, errors.New(`api must be {"vendor", "name", "version"} or a "vendor/name/version" string`)
	}
	return &target, nil
}

// connectTargetLabel renders a connect target for messages: the vendor key,
// or the API identity.
func connectTargetLabel(vendor string, api *control.ApiTargetRequest) string {
	if api != nil {
		return api.Vendor + "/" + api.Name + "/" + api.Version
	}
	return vendor
}

// humanEntryNextStep is the relay guidance for a session a human finishes by
// entering a credential or picking an OAuth app.
func humanEntryNextStep(retry string) string {
	return "Relay the approval_url to your human user and end your turn: a human enters the " +
		"credential (or picks the OAuth app) in the browser, which can take hours, so do not " +
		"wait or poll. Later, check your bindings with " + retry + " and retry the call that " +
		"was blocked. Asking again for the same API returns this same request."
}

// connectProblemCode is the problem type's last path segment (the route's
// type slug, e.g. "unknown_api"), or "".
func connectProblemCode(he *HTTPError) string {
	t, _ := he.Fields()["type"].(string)
	if i := strings.LastIndex(t, "/"); i >= 0 {
		t = t[i+1:]
	}
	return t
}

// connectStringList reads a string-list extension field off a problem body.
func connectStringList(he *HTTPError, key string) []string {
	raw, _ := he.Fields()[key].([]any)
	out := make([]string, 0, len(raw))
	for _, v := range raw {
		if s, ok := v.(string); ok && s != "" {
			out = append(out, s)
		}
	}
	return out
}

// connectLane spells a lane's connect verb and its retry forms, so the CLI and
// the stdio MCP mount render the same advice in their own vocabulary (the
// Python mount renders the same text for the MCP lane).
type connectLane struct {
	// mcp selects the tool vocabulary.
	mcp bool
}

func (l connectLane) retryAuthType(target string) string {
	if l.mcp {
		return "call request_connection again with auth_type set to one of them"
	}
	return fmt.Sprintf("re-run `jentic connect --api %s --auth-type <one of them>`", target)
}

func (l connectLane) findAPI() string {
	if l.mcp {
		return "Check the identity against the api.vendor/name/version that inspect_operation reports; " +
			"if the API is not imported yet, find it with search_catalog and import it with import_api, " +
			"then call request_connection again."
	}
	return "Check the identity with `jentic apis show <vendor/name/version>` (or the api fields " +
		"`jentic inspect` reports); if the API is not imported yet, import it with " +
		"`jentic catalog import <api_id>`, then retry `jentic connect --api`."
}

// apiTargetCoded maps the connect route's API-target and session-cap error
// codes to coded errors with the lane's recovery, plus the MCP next_tool. It
// returns nil for any other failure (the caller's generic mapping applies).
// target labels the ask (vendor key or API identity).
func apiTargetCoded(he *HTTPError, target string, err error, lane connectLane) (*ux.CodedError, string) {
	code := connectProblemCode(he)
	switch code {
	case "manual_flows_disabled":
		return &ux.CodedError{
			Code: ux.CodeBrokerDenied,
			Msg:  fmt.Sprintf("this deployment does not take connect requests for registry APIs (%s): %v", target, err),
			Actionable: "Report the gap to your human user once: ask them to connect a credential for " +
				target + " in the dashboard and bind this agent — name the auth type the API's spec " +
				"declares, the permission rules you need, and why. Then end your turn and retry later.",
		}, ""
	case "unknown_api":
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("no live API %s in this deployment's registry: %v", target, err),
			Actionable: lane.findAPI(),
		}, "search_catalog"
	case "auth_type_required", "auth_type_not_declared":
		options := connectStringList(he, "options")
		coded := &ux.CodedError{
			Code: ux.CodeResolveFailed,
			Msg:  fmt.Sprintf("cannot start a connect session for %s: %v", target, err),
		}
		if code == "auth_type_required" {
			coded.Actionable = "The API's spec declares several auth schemes"
		} else {
			coded.Actionable = "That auth type is not one the API's spec declares"
		}
		if len(options) > 0 {
			coded.Actionable += fmt.Sprintf(" (%s): pick the one the task needs and %s.",
				strings.Join(options, ", "), lane.retryAuthType(target))
			coded.Details = map[string]any{"options": options}
		} else {
			coded.Actionable += ": read them from the API's spec and " + lane.retryAuthType(target) + "."
		}
		return coded, "request_connection"
	case "no_declared_scheme":
		return &ux.CodedError{
			Code: ux.CodeBrokerDenied,
			Msg:  fmt.Sprintf("API %s declares no auth scheme a connect session can collect: %v", target, err),
			Actionable: "You cannot start this connection. Tell your human user: an operator can create " +
				"the credential for " + target + " directly in the dashboard (or the API's spec must " +
				"declare its auth). Do not retry the connect.",
		}, ""
	case "host_variable_not_pinned":
		coded := &ux.CodedError{
			Code: ux.CodeBrokerDenied,
			Msg:  fmt.Sprintf("API %s takes its server host from an unrestricted variable: %v", target, err),
			Actionable: "A credential for it cannot be pinned to known hosts, so you cannot start this " +
				"connection. Tell your human user: an operator can create the credential directly in the " +
				"dashboard (or the API's spec must limit its server variables to an enum). Do not retry the connect.",
		}
		if vars := connectStringList(he, "variables"); len(vars) > 0 {
			coded.Details = map[string]any{"variables": vars}
		}
		return coded, ""
	case "reserved_auth_field":
		return &ux.CodedError{
			Code: ux.CodeBrokerDenied,
			Msg:  fmt.Sprintf("API %s injects its key into a header the platform reserves: %v", target, err),
			Actionable: "You cannot start this connection. Tell your human user: an operator can create " +
				"the credential directly in the dashboard. Do not retry the connect.",
		}, ""
	case "security_schemes_lookup_unavailable":
		return &ux.CodedError{
			Code: ux.CodeBrokerDenied,
			Msg:  fmt.Sprintf("this instance cannot read the registry to connect %s: %v", target, err),
			Actionable: "Report it to your human user: the connect service cannot reach the registry on " +
				"this deployment. They can connect the credential in the dashboard instead.",
		}, ""
	case "too_many_open_sessions":
		return &ux.CodedError{
			Code: ux.CodeBrokerDenied,
			Msg:  fmt.Sprintf("too many open connect requests are already waiting for a human: %v", err),
			Actionable: "Do not open more. Relay the approval URLs you already have to your human user, " +
				"end your turn, and retry the blocked calls once they act.",
		}, ""
	case "recently_rejected":
		coded := &ux.CodedError{
			Code: ux.CodeBrokerDenied,
			Msg:  fmt.Sprintf("a human recently rejected this agent's request to connect %s: %v", target, err),
			Actionable: "Do not ask again. Tell your human user the request was rejected and what you " +
				"needed it for; continue without this API unless they say otherwise.",
		}
		return coded, ""
	}
	return nil, ""
}

// connectOutcomeCoded maps a terminal /status that is not "connected" to a
// coded error: rejected (do not ask again), cancelled, an API spec or OAuth app
// change that ended the session (start a new one), or any other failure.
// rerun is the lane's command to start a fresh session.
func connectOutcomeCoded(sessionID, status, errorCode, rerun string) *ux.CodedError {
	switch {
	case status == "expired":
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("connect session %s expired before it was approved", sessionID),
			Actionable: "Run `" + rerun + "` again and relay the fresh approval_url to your human user.",
		}
	case errorCode == "rejected":
		return &ux.CodedError{
			Code: ux.CodeBrokerDenied,
			Msg:  fmt.Sprintf("connect session %s was rejected by a human", sessionID),
			Actionable: "Do not ask again. Tell your human user the request was rejected and what you " +
				"needed it for; continue without this API unless they say otherwise.",
			Details: map[string]any{"error_code": errorCode},
		}
	case errorCode == "cancelled":
		return &ux.CodedError{
			Code: ux.CodeResolveFailed,
			Msg:  fmt.Sprintf("connect session %s was cancelled before it connected", sessionID),
			Actionable: "Ask your human user whether they still want this connection; if so, run `" +
				rerun + "` again and relay the fresh approval_url.",
			Details: map[string]any{"error_code": errorCode},
		}
	case errorCode == "scheme_changed" || errorCode == "servers_changed" || errorCode == "oauth_app_changed":
		return &ux.CodedError{
			Code: ux.CodeResolveFailed,
			Msg: fmt.Sprintf("connect session %s ended: the API's auth or hosts changed while it was open (%s)",
				sessionID, errorCode),
			Actionable: "Run `" + rerun + "` again to ask against the current API, and relay the fresh approval_url.",
			Details:    map[string]any{"error_code": errorCode},
		}
	}
	return &ux.CodedError{
		Code: ux.CodeBrokerDenied,
		Msg:  fmt.Sprintf("connect session %s failed (%s)", sessionID, valueOr(errorCode, "no error code")),
		Actionable: "Relay the failure to your human user — they can see the session " +
			"in the dashboard; retry `" + rerun + "` once the cause is fixed.",
		Details: map[string]any{"error_code": errorCode},
	}
}
