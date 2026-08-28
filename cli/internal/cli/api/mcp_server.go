package api

// mcp_server.go assembles the MCP server: tool registration (with the
// --read-only / --exclude-tools filters), the shared result/soft-error shape
// (every tool result is a JSON object with a top-level sibling `instance`
// stamp — never a wrapper, never _meta, §3.7.4), and the attribution
// RoundTripper (User-Agent + X-Jentic-Session-Id fallback, §3.6) that rides
// the clictx transport hook.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"sync/atomic"
	"time"

	"github.com/google/uuid"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// mcpCallTimeout bounds one tool call's control-plane work. The process is
// long-running (exempt from the interceptor's 60s deadline), so each call
// carries its own deadline instead — an unresponsive control plane must fail
// the CALL, not wedge the session.
const mcpCallTimeout = 30 * time.Second

// mcpServer is one `jentic mcp` process: a single stdio session plus the
// process-scoped attribution state (session UUID, last-seen clientInfo) and
// the TTL instance-stamp cache.
type mcpServer struct {
	app      *app
	version  string
	logger   *slog.Logger
	readOnly bool
	excluded map[string]bool
	// maxResultBytes is the §3.7 context-protection cap applied to relayed
	// upstream bodies (the execute family) — see mcp_execute.go.
	maxResultBytes int64

	// sessionID is the per-process UUID fallback for X-Jentic-Session-Id. The
	// RoundTripper stamps it ONLY when the header is absent, so an env-set
	// $JENTIC_SESSION_ID (attached earlier by the SDK's session editor) always
	// wins — "orchestrator wins" (master §3.6 item 3).
	sessionID string
	// clientInfo is the most recent client identity seen: from a request's
	// _meta `io.modelcontextprotocol/clientInfo` (spec 2026-07-28 — clientInfo
	// optionally rides every request; there is no initialize handshake) or
	// from a legacy client's initialize params. Atomic: tool handlers run
	// concurrently with the transport hook reading it.
	clientInfo atomic.Pointer[mcp.Implementation]

	instances *instanceCache
	server    *mcp.Server
}

func newMCPServer(a *app, version string, opts *mcpOptions, logger *slog.Logger) *mcpServer {
	if logger == nil {
		logger = discardLogger()
	}
	excluded := make(map[string]bool, len(opts.excludeTools))
	for _, name := range opts.excludeTools {
		if name = strings.TrimSpace(name); name != "" {
			excluded[name] = true
		}
	}
	s := &mcpServer{
		app:            a,
		version:        version,
		logger:         logger,
		readOnly:       opts.readOnly,
		excluded:       excluded,
		maxResultBytes: opts.maxResultBytes,
		sessionID:      uuid.NewString(),
		instances:      newInstanceCache(),
	}
	if s.maxResultBytes <= 0 {
		s.maxResultBytes = defaultMaxResultBytes
	}
	s.server = mcp.NewServer(
		&mcp.Implementation{Name: "jentic-mcp", Title: "Jentic One", Version: version},
		&mcp.ServerOptions{
			Instructions: "Jentic One tool server. On a new machine, or after any tool returns an " +
				"auth or connectivity error, call get_started first — it diagnoses this " +
				"machine's setup state and returns the exact operator instruction to fix it. " +
				"Call whoami to see the agent identity, status, scopes, and toolkit bindings. " +
				"Every tool result carries a top-level `instance` key identifying the Jentic " +
				"One instance it came from; instance.backend is \"unreachable\" when the " +
				"control plane could not be reached.",
			Logger: logger,
			// Legacy clients (< 2026-07-28) still send initialize; capture
			// their clientInfo for the User-Agent upgrade the same way a
			// modern client's per-request _meta is captured in the handlers.
			InitializedHandler: func(_ context.Context, req *mcp.InitializedRequest) {
				s.noteClient(req.ClientInfo())
			},
		},
	)
	s.registerTools()
	return s
}

// run serves one stdio session to completion: client disconnect (EOF) ends it
// cleanly; a cancelled ctx (SIGINT/SIGTERM) surfaces as context.Canceled.
func (s *mcpServer) run(ctx context.Context) error {
	return s.server.Run(ctx, &mcp.StdioTransport{})
}

// mcpToolSpec pairs a tool declaration with its handler for the registration
// filters. readOnly mirrors the tool's ReadOnlyHint annotation: --read-only
// serves only these (1-C's execute tool registers with readOnly=false).
type mcpToolSpec struct {
	tool     *mcp.Tool
	handler  mcp.ToolHandler
	readOnly bool
}

// noArgsSchema is the input schema for the parameterless tools. Kept
// permissive (no additionalProperties:false): a client sending a stray key
// must not lose the diagnosis get_started exists to deliver.
var noArgsSchema = map[string]any{"type": "object", "properties": map[string]any{}}

// The discovery tools' input schemas. Like noArgsSchema they stay permissive —
// no additionalProperties:false, no alias properties — because the SDK's raw
// AddTool leaves validation to the handler, where the shared normalizer
// (mcp_params.go) resolves aliases and coerces shapes the schema alone would
// have rejected. The declared types are the canonical shapes a well-behaved
// model should send.
var searchAPIsSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"query": map[string]any{
			"type":        "string",
			"description": "Natural-language description of the operation you need, e.g. \"create github issue\".",
		},
		"apis": map[string]any{
			"type":        "array",
			"items":       map[string]any{"type": "string"},
			"description": "Restrict to these APIs, as vendor/name/version slugs from earlier hits (a single string is also accepted).",
		},
		"limit": map[string]any{
			"type":        "integer",
			"description": "Max results per page (1-100, server default 10).",
		},
		"cursor": map[string]any{
			"type":        "string",
			"description": "next_cursor from the previous page, to fetch the next one.",
		},
	},
	"required": []string{"query"},
}

// inspectOperationSchema declares no required list: operation_id may arrive
// under its aliases (id, uuid), and a client-side schema validator would
// reject those spellings before the call ever reached the normalizer. (The
// server itself never validates — raw AddTool leaves that to the handler, as
// noted above — so any `required` here, including search_apis' required
// query, is advisory for clients; presence is enforced in the handler with
// a JSON-RPC invalid-params error naming all accepted spellings.)
var inspectOperationSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"operation_id": map[string]any{
			"type": "string",
			"description": "The operation to inspect (required; \"id\" and \"uuid\" are accepted aliases): a registry " +
				"operation id from a search_apis hit, or a METHOD:url pair like \"GET:https://api.example.com/v1/things\".",
		},
		"revision": map[string]any{
			"type":        "string",
			"description": "Pin a specific revision ID for reproducibility (defaults to the current revision).",
		},
	},
}

// toolSpecs declares the served tool surface: the 1-A pre-auth pair
// (get_started, whoami), the 1-B discovery pair (search_apis,
// inspect_operation), and the 1-C execute surface (execute, execute_read,
// get_execution_result — mcp_execute.go). Docstrings encode the flow
// (get_started first, whoami before discovery, search → inspect → execute)
// per §3.2, with concrete argument examples a model can copy.
func (s *mcpServer) toolSpecs() []mcpToolSpec {
	readOnly := &mcp.ToolAnnotations{ReadOnlyHint: true}
	execSpecs := s.executeToolSpecs()
	specs := make([]mcpToolSpec, 0, 4+len(execSpecs))
	specs = append(specs, []mcpToolSpec{
		{
			tool: &mcp.Tool{
				Name:  "get_started",
				Title: "Diagnose Jentic setup",
				Description: "Diagnose this machine's Jentic setup state, pre-auth. Reports one of: " +
					"no_config (no configuration or active context), not_registered, " +
					"pending_approval (registered, awaiting a human operator), " +
					"instance_unreachable (identity fine, control plane down), or ready — " +
					"each with the exact operator instruction to relay. Call this first on a " +
					"new machine and whenever another tool returns an auth or connectivity " +
					"error. Registration and approval block on a human and cannot be " +
					"completed autonomously; this server never starts or stops the instance.",
				InputSchema: noArgsSchema,
				Annotations: readOnly,
			},
			handler:  s.handleGetStarted,
			readOnly: true,
		},
		{
			tool: &mcp.Tool{
				Name:  "whoami",
				Title: "Show agent identity",
				Description: "Show the calling agent's identity as the Jentic control plane sees it: " +
					"id, status, scopes, and toolkit bindings with the APIs each one serves. " +
					"Call after get_started reports ready, and before requesting access or " +
					"executing operations — never execute an operation just to probe whether " +
					"you have access. On an auth error, call get_started for the fix.",
				InputSchema: noArgsSchema,
				Annotations: readOnly,
			},
			handler:  s.handleWhoami,
			readOnly: true,
		},
		{
			tool: &mcp.Tool{
				Name:  "search_apis",
				Title: "Search API operations",
				Description: "Search the connected Jentic One registry for API operations by " +
					"natural-language query. This is the first step of the discovery flow " +
					"(whoami → search_apis → inspect_operation → execute): call it whenever " +
					"you need an operation you don't already have the id of. " +
					`Example: {"query": "create github issue", "limit": 5}. Returns one page ` +
					"as {data, has_more, next_cursor}; each hit carries the operation_id to " +
					"pass to inspect_operation. When has_more is true, pass next_cursor back " +
					"as cursor for the next page. Optionally restrict to specific APIs with " +
					`apis (vendor/name/version slugs from earlier hits), e.g. ` +
					`{"query": "list pets", "apis": ["acme/pets/v1"]}. An empty data array ` +
					"means nothing matching is imported into this instance's registry yet.",
				InputSchema: searchAPIsSchema,
				Annotations: readOnly,
			},
			handler:  s.handleSearchAPIs,
			readOnly: true,
		},
		{
			tool: &mcp.Tool{
				Name:  "inspect_operation",
				Title: "Inspect an operation's contract",
				Description: "Fetch one operation's full contract before executing it: HTTP method, " +
					"URL, parameters, request/response schemas, and security requirements. " +
					"This is the read-contract step of the flow (whoami → search_apis → " +
					"inspect_operation → execute) — always inspect before you execute. " +
					`Example: {"operation_id": "op_abc123"} with an id from a search_apis ` +
					`hit, or a METHOD:url pair like {"operation_id": ` +
					`"GET:https://rest.coincap.io/v3/markets"}. Optionally pin a revision ` +
					"for reproducibility. If the operation is not found, call search_apis " +
					"to rediscover the right id.",
				InputSchema: inspectOperationSchema,
				Annotations: readOnly,
			},
			handler:  s.handleInspectOperation,
			readOnly: true,
		},
	}...)
	return append(specs, execSpecs...)
}

// registerTools applies the serving filters. --exclude-tools drops by name;
// --read-only drops everything not annotated read-only — from 1-C on that is
// exactly the `execute` tool (execute_read and get_execution_result stay).
func (s *mcpServer) registerTools() {
	for _, spec := range s.toolSpecs() {
		if s.excluded[spec.tool.Name] {
			s.logger.Info("tool excluded by --exclude-tools", "tool", spec.tool.Name)
			continue
		}
		if s.readOnly && !spec.readOnly {
			s.logger.Info("tool withheld by --read-only", "tool", spec.tool.Name)
			continue
		}
		s.server.AddTool(spec.tool, spec.handler)
	}
}

// noteClient records the client identity riding a request's _meta (or, for
// legacy clients, the initialize params — req.ClientInfo() resolves both), so
// the next backend call's User-Agent carries it.
func (s *mcpServer) noteClient(ci *mcp.Implementation) {
	if ci == nil || ci.Name == "" {
		return
	}
	s.clientInfo.Store(ci)
}

// callContext derives the per-call context every handler uses: the session
// context's values (ActiveState, transport hook) with this call's deadline.
func (s *mcpServer) callContext(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, mcpCallTimeout)
}

// result builds the one tool-result shape every tool returns: the payload
// object with the top-level sibling `instance` stamp added (a strict superset
// of the CLI envelope — never a wrapper object, never _meta: GUI clients
// don't reliably surface _meta to the model, and the stamp exists for the
// model to read, §3.7.4), redacted through the same funnel as CLI output.
func (s *mcpServer) result(ctx context.Context, payload map[string]any) *mcp.CallToolResult {
	payload["instance"] = s.instances.stamp(ctx)
	data, err := json.Marshal(payload)
	if err != nil {
		// A map[string]any of JSON-decoded values can't fail to marshal;
		// guard anyway so a future payload bug degrades, not panics. The
		// fallback is itself marshalled (a map[string]string cannot fail) so
		// an error text carrying quotes/backslashes can't break the JSON this
		// path exists to preserve.
		data, _ = json.Marshal(map[string]string{
			"schema_version": mcpSchemaVersion,
			"error_code":     ux.CodeInternalError,
			"error":          "encoding tool result: " + err.Error(),
		})
	}
	return &mcp.CallToolResult{
		Content: []mcp.Content{&mcp.TextContent{Text: string(ux.RedactBytes(data))}},
	}
}

// mcpSchemaVersion pins the machine-contract shape of the MCP tool payloads,
// mirroring the CLI envelopes' schema_version (13 §2/§6).
const mcpSchemaVersion = "1"

// redactedErr funnels an error's message through the ux redaction backstop
// before it is written to the MCP log file. The log is an output surface like
// stderr — and HTTPError.Error() embeds the raw upstream response body — so
// every failure-logging site must apply the same guarantee ReportError's
// stderr path does, not write err verbatim.
func redactedErr(err error) string {
	return string(ux.RedactBytes([]byte(err.Error())))
}

// softError converts a failure into an isError tool result carrying the SAME
// coded taxonomy as the CLI's agent envelope ({schema_version, error_code,
// error, actionable_step}) — plus the instance stamp (degraded when the
// failure IS unreachability). Protocol errors are reserved for malformed
// requests; a diagnosable state (not registered, pending, instance down) must
// reach the model as data it can act on. Auth/config failures append the
// get_started pointer so the model's next step is always discoverable.
func (s *mcpServer) softError(ctx context.Context, err error) *mcp.CallToolResult {
	return s.softErrorNext(ctx, err, "")
}

// softErrorNext is softError with an explicit next_tool override for handlers
// that know a better recovery than the code-keyed default — e.g. an inspect
// 404 is RESOLVE_FAILED, but the fix is search_apis (find the right id), not
// get_started (the identity already resolved). An empty nextTool keeps the
// default mapping.
func (s *mcpServer) softErrorNext(ctx context.Context, err error, nextTool string) *mcp.CallToolResult {
	return s.softErrorExtra(ctx, err, nextTool, nil)
}

// softErrorExtra is softErrorNext plus caller-supplied payload keys — the
// §3.7 error-mapping table's per-class enrichments (a broker denial's verbatim
// agent_directive passthrough, the retryable hint on transport failures).
// Extra keys never displace the coded contract keys.
func (s *mcpServer) softErrorExtra(ctx context.Context, err error, nextTool string, extra map[string]any) *mcp.CallToolResult {
	coded := mcpCoded(err)
	payload := map[string]any{
		"schema_version": mcpSchemaVersion,
		"error_code":     coded.Code,
		"error":          coded.Msg,
	}
	if coded.Actionable != "" {
		payload["actionable_step"] = coded.Actionable
	}
	if len(coded.Details) > 0 {
		payload["details"] = coded.Details
	}
	if nextTool == "" {
		switch coded.Code {
		case ux.CodeNotAuthenticated, ux.CodePendingApproval, ux.CodeResolveFailed:
			nextTool = "get_started"
		}
	}
	if nextTool != "" {
		payload["next_tool"] = nextTool
	}
	switch coded.Code {
	case ux.CodeNotAuthenticated, ux.CodePendingApproval:
		// §3.7.4 refresh-on-auth-error: an auth failure inside the stamp TTL
		// means the cached identity can no longer be presumed current (the
		// backend may have been re-pointed, the identity revoked) — force the
		// stamp joining THIS result to re-validate instead of serving the
		// cached "fresh" identity next to an auth error.
		s.instances.invalidate()
	}
	for k, v := range extra {
		if _, taken := payload[k]; !taken {
			payload[k] = v
		}
	}
	res := s.result(ctx, payload)
	res.IsError = true
	return res
}

// mcpCoded is the MCP-side error mapping: asCoded's taxonomy plus the wire
// case only a long-lived server sees. A control-plane 401 that SURVIVED the
// retry transport's one token re-exchange means the identity was rejected
// post-mint — revoked or kill-switched (the §3.7 table's "revoked identity"
// row); a 403 is the same recovery loop (identity known, not permitted).
// asCoded has no *HTTPError branch (mint-time failures never produce one), so
// without this mapping the one taxonomy row written for the long-lived-agent
// recovery loop would fall into the INTERNAL_ERROR "report a CLI bug"
// catch-all with no next_tool pointer.
func mcpCoded(err error) *ux.CodedError {
	var httpErr *HTTPError
	if errors.As(err, &httpErr) &&
		(httpErr.StatusCode == http.StatusUnauthorized || httpErr.StatusCode == http.StatusForbidden) {
		return &ux.CodedError{
			Code: ux.CodeNotAuthenticated,
			Msg: fmt.Sprintf("the control plane rejected this agent's credentials (%v) — "+
				"the identity may have been revoked or disabled", err),
			Actionable: "call get_started to diagnose this machine's setup and relay its instruction to your operator",
		}
	}
	return asCoded(err)
}

// transportHook returns the clictx.TransportHook this session installs: it
// composes the attribution RoundTripper over whatever transport the SEC-20
// resolution produced (CA-pinned or default) — wrap, never displace.
func (s *mcpServer) transportHook() clictx.TransportHook {
	return func(base http.RoundTripper) http.RoundTripper {
		return &attributionTransport{
			base:      base,
			userAgent: s.userAgent,
			sessionID: s.sessionID,
		}
	}
}

// userAgent is `jentic-mcp/<version>`, upgraded to
// `jentic-mcp/<version> (<client>/<clientversion>)` once any request has
// carried clientInfo. The prefix stays `jentic-mcp/` so the backend's
// derive_origin is unaffected (§3.6).
func (s *mcpServer) userAgent() string {
	ua := "jentic-mcp/" + s.version
	if ci := s.clientInfo.Load(); ci != nil && ci.Name != "" {
		clientVersion := ci.Version
		if clientVersion == "" {
			clientVersion = "unknown"
		}
		ua += " (" + uaToken(ci.Name) + "/" + uaToken(clientVersion) + ")"
	}
	return ua
}

// uaToken sanitizes an untrusted clientInfo fragment for the User-Agent
// header: keep RFC 7230 token-ish bytes, drop the rest, clamp the length.
// Same fail-soft posture as client.SanitizeSessionID — attribution is
// best-effort and a hostile value must never break the request.
func uaToken(s string) string {
	const maxLen = 64
	var b strings.Builder
	for _, r := range s {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9',
			r == '.', r == '_', r == '-', r == '+':
			b.WriteRune(r)
		}
		if b.Len() >= maxLen {
			break
		}
	}
	if b.Len() == 0 {
		return "unknown"
	}
	return b.String()
}

// attributionTransport decorates every backend request with the process's
// attribution headers. It delegates to base — the SEC-20 resolved transport —
// and, being INSIDE the SDK's retry transport, re-stamps every retry attempt.
type attributionTransport struct {
	base      http.RoundTripper
	userAgent func() string
	sessionID string
}

func (t *attributionTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	// Per http.RoundTripper contract the request must not be mutated; clone
	// before stamping.
	r := req.Clone(req.Context())
	r.Header.Set("User-Agent", t.userAgent())
	// Only when absent: the SDK's session editor has already attached an
	// env-set $JENTIC_SESSION_ID by the time the request reaches the
	// transport, so the per-process UUID is strictly the fallback.
	if t.sessionID != "" && r.Header.Get("X-Jentic-Session-Id") == "" {
		r.Header.Set("X-Jentic-Session-Id", t.sessionID)
	}
	return t.base.RoundTrip(r)
}
