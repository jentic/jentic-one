package api

// mcp_catalog.go holds the catalog pair of MCP tools (search_catalog /
// import_api). They ride the exact client seams the cobra commands use — the
// catalogClient wrapper for GET /catalog and
// POST /catalog/{api_id:path}:import (rawPathEditor and all) — so the
// envelopes stay byte-consistent with `jentic catalog … --json`, with
// schema_version and the sibling instance stamp joined like every other tool
// result.

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// defaultImportWaitBudget bounds how long import_api tracks the import job
// before handing the still-running job back to the model. It sits well inside
// the 30s mcpCallTimeout so the terminal legs (result fetch + promotes) keep
// headroom; a slower import converges on the next import_api call (the
// idempotentHint contract — re-import of the same api_id converges).
const defaultImportWaitBudget = 20 * time.Second

// searchCatalogParams mirrors the `jentic catalog search` surface: keyword
// query plus the pagination knobs.
var searchCatalogParams = []paramSpec{
	{name: "query", aliases: []string{"q"}, kind: paramString},
	{name: "limit", kind: paramInt},
	// Same next_cursor fold as search_apis: a model copying the response key
	// back as the argument name must not silently re-serve page 1.
	{name: "cursor", aliases: []string{"next_cursor"}, kind: paramString},
}

// importAPIParams: the catalog entry identity. `id` is a safe alias here —
// this tool has no operation_id for it to collide with.
var importAPIParams = []paramSpec{
	{name: "api_id", aliases: []string{"id", "api"}, kind: paramString},
}

// transportSoftError is the access-loop twin of executeTransportError
// (mcp_execute.go): a transport failure must reach the model as a retryable
// TRANSPORT_ERROR with the get_started pointer — never as INTERNAL_ERROR
// ("stop, CLI bug") with no recovery. Unlike execute, retryable is safe to
// hint unconditionally here: every access-loop call is a read or a
// server-converging write (a re-import of the same api_id converges, a
// duplicate filing answers 409 — nothing double-executes). Non-transport
// failures (completed *HTTPError, auth, no-config) fall through to the
// shared code-keyed mapping, keeping any caller-supplied extras.
func (s *mcpServer) transportSoftError(ctx context.Context, err error, extra map[string]any) *mcp.CallToolResult {
	err = classifyTransportErr(err)
	if asCoded(err).Code != ux.CodeTransportError {
		return s.softErrorExtra(ctx, err, "", extra)
	}
	merged := map[string]any{"retryable": true}
	for k, v := range extra {
		merged[k] = v
	}
	return s.softErrorExtra(ctx, err, "get_started", merged)
}

// ── search_catalog ────────────────────────────────────────────────────────────

func (s *mcpServer) handleSearchCatalog(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, searchCatalogParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	limit, _ := args["limit"].(int)
	if limit != 0 && (limit < 1 || limit > 200) {
		// The docstring promises 1-200 (the server's own page bound); an
		// out-of-range value is a correctable protocol error, not a soft
		// INTERNAL_ERROR round-tripped from the backend's 422.
		return nil, invalidParams(fmt.Errorf("limit must be between 1 and 200, got %d", limit))
	}

	client, err := s.app.catalogSession(cctx)
	if err != nil {
		s.logger.Warn("search_catalog failed", "error", redactedErr(err))
		return s.softError(cctx, err), nil
	}
	query, _ := args["query"].(string)
	cursor, _ := args["cursor"].(string)
	page, err := client.List(cctx, catalogListParams{Q: query, Cursor: cursor, Limit: limit})
	if err != nil {
		s.logger.Warn("search_catalog failed", "error", redactedErr(err))
		var he *HTTPError
		if errors.As(err, &he) {
			switch he.StatusCode {
			case http.StatusNotFound, http.StatusNotImplemented:
				// The same missing-route mapping catalogListErr makes: no
				// catalog on this deployment is a deployment fact, not a
				// malformed call.
				return s.softError(cctx, &ux.CodedError{
					Code: ux.CodeInternalError,
					Msg:  fmt.Sprintf("catalog not available on this server (HTTP %d)", he.StatusCode),
				}), nil
			case http.StatusForbidden:
				// A 403 on THIS route is the missing capabilities:read scope,
				// not a revoked identity — the generic NOT_AUTHENTICATED +
				// get_started mapping would dead-end the agent; the scope is
				// granted by a human operator (mirrors importAPIError).
				return s.softError(cctx, &ux.CodedError{
					Code: ux.CodeBrokerDenied,
					Msg:  fmt.Sprintf("reading the catalog requires the capabilities:read scope: %v", err),
					Actionable: "Ask your human operator to grant this agent the capabilities:read scope " +
						"in the dashboard, then retry search_catalog once they confirm.",
				}), nil
			}
		}
		return s.transportSoftError(cctx, err, nil), nil
	}

	// Envelope: the same entries + counters `jentic catalog search --json`
	// emits, with the shared pagination invariant applied — has_more is
	// DERIVED from cursor presence and an empty next_cursor is omitted, so
	// the page-1-loop trap is unrepresentable here too.
	nextCursor := ""
	if page.HasMore {
		nextCursor = page.NextCursor
	}
	payload := map[string]any{
		"schema_version":       mcpSchemaVersion,
		"data":                 page.Data,
		"catalog_total":        page.CatalogTotal,
		"registered_count":     page.RegisteredCount,
		"outdated_count":       page.OutdatedCount,
		"manifest_age_seconds": page.ManifestAgeSeconds,
		"has_more":             nextCursor != "",
	}
	if nextCursor != "" {
		payload["next_cursor"] = nextCursor
	}
	return s.result(cctx, payload), nil
}

// ── import_api ────────────────────────────────────────────────────────────────

func (s *mcpServer) handleImportAPI(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, importAPIParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	apiID, _ := args["api_id"].(string)
	if apiID == "" {
		return nil, invalidParams(errors.New(`import_api requires "api_id" (aliases: "id", "api"): ` +
			`a catalog entry id from a search_catalog hit, e.g. "googleapis.com/sheets"`))
	}
	if err := validateAPIID(apiID); err != nil {
		return nil, invalidParams(err)
	}

	client, err := s.app.catalogSession(cctx)
	if err != nil {
		s.logger.Warn("import_api failed", "error", redactedErr(err))
		return s.softError(cctx, err), nil
	}
	jobID, err := client.Import(cctx, apiID)
	if err != nil {
		return s.importAPIError(cctx, apiID, err), nil
	}

	job, done, pollErr := s.trackImportJob(cctx, client, jobID)
	if pollErr != nil {
		// A failing GET /jobs/{id} is NOT "still running": reporting it as a
		// clean non-terminal result would send the model into a re-import
		// loop against a backend that de-duplicates nothing. Surface the
		// failure with the job_id so the model can keep watching THIS job.
		s.logger.Warn("import_api job poll failed", "api_id", apiID, "job_id", jobID, "error", redactedErr(pollErr))
		return s.transportSoftError(cctx, pollErr, map[string]any{"job_id": jobID}), nil
	}
	if !done {
		// Budget lapsed with the job still running: a normal result — the
		// model converges by re-calling import_api (idempotent) or watches
		// the job with get_execution_result. Never block out the call.
		status := "queued"
		if job != nil {
			status = job.Status
		}
		s.logger.Info("import_api still running past the wait budget", "api_id", apiID, "job_id", jobID, "status", status)
		return s.result(cctx, map[string]any{
			"schema_version": mcpSchemaVersion,
			"job_id":         jobID,
			"status":         status,
		}), nil
	}
	if job.Status != catJobCompleted {
		return s.softErrorExtra(cctx, &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  fmt.Sprintf("import of %s %s: %s", apiID, job.Status, valueOr(job.Error, "no detail")),
			Actionable: "Re-check the api_id against a search_catalog hit and retry import_api; " +
				"if the import keeps failing, relay this error to your operator.",
		}, "search_catalog", map[string]any{"job_id": jobID, "job_status": job.Status}), nil
	}

	result, err := client.JobResult(cctx, jobID)
	if err != nil {
		s.logger.Warn("import_api result fetch failed", "job_id", jobID, "error", redactedErr(err))
		return s.transportSoftError(cctx, err, map[string]any{"job_id": jobID}), nil
	}
	// Auto-promote the imported revisions to live, exactly like the CLI's
	// default (`jentic catalog import` without --no-promote): unpromoted
	// drafts are invisible to search/execute, which would strand the loop.
	promoted := s.app.promoteRevisions(cctx, client, result)
	s.logger.Info("import_api", "api_id", apiID, "job_id", jobID, "revisions", len(result.Revisions))
	return s.result(cctx, map[string]any{
		"schema_version": mcpSchemaVersion,
		"job_id":         jobID,
		"status":         job.Status,
		"revisions":      result.Revisions,
		"promoted":       promoted,
	}), nil
}

// trackImportJob polls the import job until it is terminal, the wait budget
// (importWaitBudget, test-injectable) lapses, or a poll fails. The three
// outcomes are DISTINCT: (job, true, nil) is terminal, (last, false, nil) is
// a genuine budget/cancellation lapse with the job still running, and a
// non-nil error means the job's state is UNKNOWN — the caller must surface
// that as a failure, never as a clean "still running" result. Poll cadence is
// the App's shared schedule so tests shrink it without mutating globals.
func (s *mcpServer) trackImportJob(ctx context.Context, client *catalogClient, jobID string) (*catalogJob, bool, error) {
	budget := s.importWaitBudget
	if budget <= 0 {
		budget = defaultImportWaitBudget
	}
	deadline := time.Now().Add(budget)
	_, pollMax, pollStep := s.app.PollCadence()
	delay := pollStep // the first poll is immediate; back off from the step
	var last *catalogJob
	for {
		job, err := client.Job(ctx, jobID)
		if err != nil {
			return last, false, err
		}
		last = job
		switch job.Status {
		case catJobCompleted, catJobFailed, catJobCancelled, catJobDeadLetter:
			return job, true, nil
		}
		if time.Now().After(deadline) {
			return last, false, nil
		}
		select {
		case <-ctx.Done():
			return last, false, nil
		case <-time.After(delay):
		}
		if delay < pollMax {
			delay += pollStep
		}
	}
}

// importAPIError maps the import-specific failures: a 404 is an unknown
// catalog entry (rediscover via search_catalog — the identity is fine); a
// control-plane 403 on THIS route is a missing catalog:import scope, and the
// recovery is asking the operator to grant the scope (skill wording), not
// get_started.
func (s *mcpServer) importAPIError(ctx context.Context, apiID string, err error) *mcp.CallToolResult {
	s.logger.Warn("import_api failed", "api_id", apiID, "error", redactedErr(err))
	var he *HTTPError
	if errors.As(err, &he) {
		switch he.StatusCode {
		case http.StatusNotFound:
			return s.softErrorNext(ctx, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("catalog entry %q not found", apiID),
				Actionable: "Call search_catalog with a keyword for the API you need and use the " +
					"api_id from one of its hits.",
			}, "search_catalog")
		case http.StatusForbidden:
			return s.softError(ctx, &ux.CodedError{
				Code: ux.CodeBrokerDenied,
				Msg:  fmt.Sprintf("importing a cataloged API requires the catalog:import scope: %v", err),
				Actionable: "Ask your human operator to grant this agent the catalog:import scope " +
					"in the dashboard, then retry import_api once they confirm.",
			})
		}
	}
	return s.transportSoftError(ctx, err, nil)
}

// validateAPIID syntactically guards the {api_id:path} route: the api_id is
// spliced into the request path VERBATIM (catalog_client.go's rawPathEditor —
// deliberately unescaped so umbrella ids keep their literal slash), which
// means a traversal-shaped model-supplied value ("../access-requests", a
// leading "/", empty or dot segments) would otherwise rewrite the route.
// Reject those before the wire as a correctable protocol error.
func validateAPIID(apiID string) error {
	if strings.HasPrefix(apiID, "/") {
		return fmt.Errorf("invalid api_id %q: a leading %q is not allowed — pass the api_id from a search_catalog hit verbatim", apiID, "/")
	}
	for _, seg := range strings.Split(apiID, "/") {
		if seg == "" || seg == "." || seg == ".." {
			return fmt.Errorf("invalid api_id %q: empty, %q, or %q path segments are not allowed — pass the api_id from a search_catalog hit verbatim", apiID, ".", "..")
		}
	}
	return nil
}

// valueOr returns v, or fallback when v is empty (local twin of
// cmdcore.ValueOr to keep this file free of the cmdcore import).
func valueOr(v, fallback string) string {
	if v == "" {
		return fallback
	}
	return v
}

// ── registration ─────────────────────────────────────────────────────────────

// The access-loop tools' input schemas. Permissive like every other schema
// (no additionalProperties:false, no alias properties — the normalizer
// resolves them handler-side); the declared shapes are the canonical
// spellings.
var searchCatalogSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"query": map[string]any{
			"type":        "string",
			"description": "Keyword to search the catalog for, e.g. \"spreadsheets\". Omit to list the catalog.",
		},
		"limit": map[string]any{
			"type":        "integer",
			"description": "Max entries per page (1-200, server default 50).",
		},
		"cursor": map[string]any{
			"type":        "string",
			"description": "next_cursor from the previous page, to fetch the next one.",
		},
	},
}

var importAPISchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"api_id": map[string]any{
			"type": "string",
			"description": "The catalog entry to import (required; \"id\" and \"api\" are accepted aliases): " +
				"an api_id from a search_catalog hit, e.g. \"googleapis.com/sheets\".",
		},
	},
	"required": []string{"api_id"},
}

// catalogToolSpecs declares the catalog tool surface. Annotations per master
// §3.2: search_catalog is read-only; import_api carries idempotentHint and NO
// readOnlyHint (re-import of the same api_id converges). --read-only
// therefore serves search_catalog and withholds import_api.
func (s *mcpServer) catalogToolSpecs() []mcpToolSpec {
	return []mcpToolSpec{
		{
			tool: &mcp.Tool{
				Name:  "search_catalog",
				Title: "Search the public API catalog",
				Description: "Search the public API catalog for importable APIs by keyword. The registry that " +
					"search_apis reads only sees IMPORTED APIs — when search_apis returns an empty data " +
					"array, the API probably isn't imported yet: find it here, import it with import_api, " +
					"then search_apis again (reading the registry and importing need no extra grant). " +
					`Example: {"query": "spreadsheets", "limit": 10}. Returns one page as ` +
					"{data, catalog_total, registered_count, has_more, next_cursor}; each entry carries " +
					"the api_id to pass to import_api and `registered` (true = already imported — skip " +
					"the import). When has_more is true, pass next_cursor back as cursor.",
				InputSchema: searchCatalogSchema,
				Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true},
			},
			handler: s.handleSearchCatalog,
		},
		{
			tool: &mcp.Tool{
				Name:  "import_api",
				Title: "Import a catalog API into the registry",
				Description: "Import a catalog API into this instance's registry so its operations become " +
					"searchable and executable. " +
					`Example: {"api_id": "googleapis.com/sheets"} with an api_id from a search_catalog hit. ` +
					"The import runs as a job: this call tracks it briefly and, on completion, promotes the " +
					"imported revisions live, returning {job_id, status, revisions, promoted}. If it returns " +
					"a non-terminal status, call import_api again with the same api_id — re-importing " +
					"converges (idempotent) and finishes the promotion. Requires the catalog:import scope " +
					"(agents hold it by default); on a denial, ask your operator to grant it — do not guess " +
					"other scopes. Importing makes an API discoverable but does NOT grant access to call " +
					"it: check whoami for a credential binding serving it — never execute just to probe — and " +
					"ask your operator to connect a credential and bind you if nothing serves it.",
				InputSchema: importAPISchema,
				Annotations: &mcp.ToolAnnotations{IdempotentHint: true},
			},
			handler: s.handleImportAPI,
		},
	}
}
