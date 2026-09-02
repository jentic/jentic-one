package api

// mcp_discovery.go holds the PR 1-B discovery tools. search_apis is the CLI's
// `jentic search` as a tool: the same SearchOperationsWithResponse call and
// the same envelope, passed through one page at a time (the model paginates
// with next_cursor; auto-following --all style would flood its context).
// inspect_operation is `jentic inspect` over the agentops Inspector seam with
// the format fixed to json — the raw full inspect document, redacted through
// the same funnel as CLI output. Both fold their arguments through the shared
// normalizer (mcp_params.go) before touching the wire.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"

	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// invalidParams maps a malformed-arguments failure to the JSON-RPC
// invalid-params error, per the §3.7 error-mapping table: a request the
// server cannot even interpret is a protocol error, not a soft error result
// (those are reserved for diagnosable states the model should act on).
func invalidParams(err error) error {
	return &jsonrpc.Error{Code: jsonrpc.CodeInvalidParams, Message: err.Error()}
}

// searchAPIsParams mirrors the `jentic search` surface: the query plus the
// pagination/filter knobs the CLI already has (--api/--limit/--cursor).
var searchAPIsParams = []paramSpec{
	{name: "query", kind: paramString},
	{name: "apis", aliases: []string{"api"}, kind: paramStringList},
	{name: "limit", kind: paramInt},
	// next_cursor is aliased because it's the single most predictable drift:
	// a model copying the response key straight back as the argument name.
	// Without the fold the key would be silently dropped and the tool would
	// re-serve page 1 forever.
	{name: "cursor", aliases: []string{"next_cursor"}, kind: paramString},
}

func (s *mcpServer) handleSearchAPIs(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, searchAPIsParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	query, _ := args["query"].(string)
	if query == "" {
		return nil, invalidParams(errors.New(`search_apis requires a non-empty "query" string, e.g. {"query": "create github issue"}`))
	}

	client, err := s.app.controlClient(cctx)
	if err != nil {
		s.logger.Warn("search_apis failed", "error", redactedErr(err))
		return s.softError(cctx, err), nil
	}

	body := control.SearchRequest{Query: query}
	if apis, ok := args["apis"].([]string); ok && len(apis) > 0 {
		body.Apis = &apis
	}
	if limit, ok := args["limit"].(int); ok && limit != 0 {
		// The docstring promises 1-100 (the server's own bound); enforce it
		// here so an out-of-range value is an invalid-params protocol error
		// the model can correct, not a soft INTERNAL_ERROR round-tripped from
		// the server's 422 (which mis-hints retryability).
		if limit < 1 || limit > 100 {
			return nil, invalidParams(fmt.Errorf("limit must be between 1 and 100, got %d", limit))
		}
		body.Limit = &limit
	}
	if cursor, ok := args["cursor"].(string); ok && cursor != "" {
		body.Cursor = &cursor
	}

	resp, searchErr := client.SearchOperationsWithResponse(cctx, body)
	if err := apiErrorFor(resp, searchErr); err != nil {
		// The same 501 mapping searchE makes: search disabled on this
		// deployment is a deployment fact, not a malformed call.
		var he *HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotImplemented {
			err = &ux.CodedError{Code: ux.CodeInternalError, Msg: errSearchUnsupported.Error()}
		}
		s.logger.Warn("search_apis failed", "error", redactedErr(err))
		return s.softError(cctx, err), nil
	}
	if resp.JSON200 == nil {
		return s.softError(cctx, &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  fmt.Sprintf("unexpected backend response (status %d)", resp.StatusCode()),
		}), nil
	}

	// Envelope passthrough: the exact hit projection `jentic search --json`
	// emits, under the same {data, has_more, next_cursor} keys, one page per
	// call. Non-nil so an empty result serializes as [] (never null).
	hits := make([]searchHit, 0, len(resp.JSON200.Data))
	for _, h := range resp.JSON200.Data {
		hits = append(hits, toSearchHit(h))
	}
	// Pagination mirrors ux.NewList's invariant: has_more is DERIVED from
	// cursor presence and an empty next_cursor is omitted, so the
	// inconsistent pair {has_more:true, next_cursor:""} — which the CLI list
	// envelope cannot represent, and which would trap a docstring-following
	// model in a page-1 loop (the handler drops an empty cursor argument) —
	// is unrepresentable here too, whatever the server sends.
	nextCursor := deref(resp.JSON200.NextCursor)
	payload := map[string]any{
		"schema_version": mcpSchemaVersion,
		"data":           hits,
		"has_more":       nextCursor != "",
	}
	if nextCursor != "" {
		payload["next_cursor"] = nextCursor
	}
	return s.result(cctx, payload), nil
}

// inspectOperationParams: the shared operation-identity aliases plus the CLI's
// --revision pin. Format is NOT a parameter — the tool fixes json (markdown/
// openapi are human/CLI shapes).
var inspectOperationParams = []paramSpec{
	operationIDSpec,
	{name: "revision", kind: paramString},
}

func (s *mcpServer) handleInspectOperation(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, inspectOperationParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	target, _ := args["operation_id"].(string)
	if target == "" {
		return nil, invalidParams(errors.New(`inspect_operation requires "operation_id" (aliases: "id", "uuid"): ` +
			`a registry operation id from a search_apis hit, or a METHOD:url pair like "GET:https://api.example.com/v1/things"`))
	}
	revision, _ := args["revision"].(string)

	ins, err := s.inspector(cctx)
	if err != nil {
		s.logger.Warn("inspect_operation failed", "error", redactedErr(err))
		return s.softError(cctx, err), nil
	}
	body, err := ins.Inspect(cctx, target, revision, "json")
	if err != nil {
		var he *HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
			// The same 404 → RESOLVE_FAILED mapping inspectE and the execute
			// resolve path make — with the recovery pointed at the search
			// tool, not get_started (the identity is fine; the id is not).
			return s.softErrorNext(cctx, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("operation %q not found", target),
				Actionable: "Call search_apis with a natural-language description of what you want to do, " +
					"then inspect the operation_id (or the METHOD:url) from one of its hits.",
			}, "search_apis"), nil
		}
		s.logger.Warn("inspect_operation failed", "error", redactedErr(err))
		return s.softError(cctx, err), nil
	}

	// Envelope: the raw full inspect document itself, with schema_version and
	// the instance stamp joined as top-level siblings (the whoami pattern —
	// a strict superset of what `jentic inspect --format json` prints).
	// s.result routes the marshaled payload through ux.RedactBytes, the same
	// redaction guarantee inspectE applies to the body before writing it.
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil {
		return s.softError(cctx, &ux.CodedError{Code: ux.CodeInternalError, Msg: "decode inspect response: " + err.Error()}), nil
	}
	payload["schema_version"] = mcpSchemaVersion
	return s.result(cctx, payload), nil
}

// inspector resolves the agentops Inspector seam for one call: the api layer's
// generated-SDK wrapper (apiClient) over the active context's control client —
// the same implementation `jentic inspect` and the execute resolve path use.
func (s *mcpServer) inspector(ctx context.Context) (agentops.Inspector, error) {
	return s.app.apisSession(ctx)
}
