package api

// mcp_access.go holds the 2-E1 access-loop tools (lane A's tail): the catalog
// pair (search_catalog / import_api) and request_access. They ride the exact
// client seams the cobra commands use — the catalogClient wrapper for
// GET /catalog and POST /catalog/{api_id:path}:import (rawPathEditor and all),
// and the generated control client's FileAccessRequest/GetAccessRequest for
// POST /access-requests — so the envelopes stay byte-consistent with
// `jentic catalog … --json` / `jentic access … --json`, with schema_version
// and the sibling instance stamp joined like every other tool result.
//
// request_access NEVER approves: it files intent and polls the decision; the
// approve_url in every result is for the HUMAN operator's dashboard (master
// §3.2 — "files the plan, returns approve_url; never self-approves").

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// defaultImportWaitBudget bounds how long import_api tracks the import job
// before handing the still-running job back to the model. It sits well inside
// the 30s mcpCallTimeout so the terminal legs (result fetch + promotes) keep
// headroom; a slower import converges on the next import_api call (the
// idempotentHint contract — re-import of the same api_id converges).
const defaultImportWaitBudget = 20 * time.Second

// defaultAccessPollBudget is request_access's short post-file poll: long
// enough to catch a server-side auto-decision (a bare toolkit:bind for an
// unserved API auto-denies), far too short to sit out a human approval —
// that wait belongs to the model's own request_id polling, never inside one
// tool call (client-side tool timeouts, §3.4).
const defaultAccessPollBudget = 8 * time.Second

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

// requestAccessParams mirrors `jentic access request`'s flag surface (the
// compose() plan builder is reused verbatim) plus the request_id poll arm.
// rules_json is paramJSON, not paramStringList: a JSON rules array carries
// commas, and the string-list coercion would comma-split a bare string value.
var requestAccessParams = []paramSpec{
	{name: "request_id", aliases: []string{"id"}, kind: paramString},
	{name: "provision", aliases: []string{"provisions"}, kind: paramStringList},
	{name: "toolkits", aliases: []string{"toolkit"}, kind: paramStringList},
	{name: "toolkit_ids", aliases: []string{"toolkit_id"}, kind: paramStringList},
	{name: "scopes", aliases: []string{"scope"}, kind: paramStringList},
	{name: "auth", aliases: []string{"auths"}, kind: paramStringList},
	{name: "rules_json", aliases: []string{"rules"}, kind: paramJSON},
	{name: "reason", kind: paramString},
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
				// get_started mapping would dead-end an agent that can fix
				// this itself via request_access (mirrors importAPIError).
				return s.softErrorNext(cctx, &ux.CodedError{
					Code: ux.CodeBrokerDenied,
					Msg:  fmt.Sprintf("reading the catalog requires the capabilities:read scope: %v", err),
					Actionable: `Request the scope with request_access, e.g. {"scopes": ["capabilities:read"], ` +
						`"reason": "search the catalog for the API needed for this task"}, wait for your operator's approval, then retry search_catalog.`,
				}, "request_access"), nil
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
// recovery is requesting the scope (skill wording), not get_started.
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
			return s.softErrorNext(ctx, &ux.CodedError{
				Code: ux.CodeBrokerDenied,
				Msg:  fmt.Sprintf("importing a cataloged API requires the catalog:import scope: %v", err),
				Actionable: `Request the scope with request_access, e.g. {"scopes": ["catalog:import"], ` +
					`"reason": "import the API needed for this task"}, wait for your operator's approval, then retry import_api.`,
			}, "request_access")
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

// ── request_access ────────────────────────────────────────────────────────────

// pendingAccessInstruction is the human-in-the-loop wording every pending
// request_access result carries: the tool files and polls, a HUMAN approves.
const pendingAccessInstruction = "Relay approve_url to your human operator — granting is always a human " +
	"action in the dashboard; this tool never approves. Poll the decision by calling request_access " +
	`with {"request_id": "<id>"}; never re-file the same request while one is pending.`

func (s *mcpServer) handleRequestAccess(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, requestAccessParams)
	if err != nil {
		return nil, invalidParams(err)
	}

	st := clictx.ActiveContext(cctx)
	if st == nil {
		return s.softError(cctx, noContextErr()), nil
	}
	client, err := s.app.controlClient(cctx)
	if err != nil {
		s.logger.Warn("request_access failed", "error", redactedErr(err))
		return s.softError(cctx, err), nil
	}

	// The poll arm: a request_id fetches the decision state and nothing else.
	// Filing parameters riding along are a confused call, not noise to drop:
	// a malformed rules_json or a stray reason silently ignored would teach
	// the model its arguments were accepted.
	if requestID, _ := args["request_id"].(string); requestID != "" {
		opts, optsErr := requestAccessOptions(args)
		if optsErr != nil {
			return nil, invalidParams(optsErr)
		}
		if opts.targetCount() > 0 || opts.reason != "" || len(opts.auths) > 0 || len(opts.rulesJSONs) > 0 {
			return nil, invalidParams(errors.New(`pass EITHER "request_id" (to poll an existing request) ` +
				`OR filing parameters ("provision"/"toolkits"/"toolkit_ids"/"scopes" with "auth"/"rules_json"/"reason") ` +
				`to file a new one, not both`))
		}
		reqState, getErr := s.app.getAccessRequest(cctx, client, requestID)
		if getErr != nil {
			var he *HTTPError
			if errors.As(getErr, &he) && he.StatusCode == http.StatusNotFound {
				// The identity resolved — the id is wrong; the recovery is
				// re-reading the earlier request_access result (self-pointer).
				return s.softErrorNext(cctx, &ux.CodedError{
					Code: ux.CodeResolveFailed,
					Msg:  fmt.Sprintf("access request %q not found", requestID),
					Actionable: "Re-check the request id — it is the `id` in the request_access result that " +
						"filed it — and call request_access again with the exact value.",
				}, "request_access"), nil
			}
			s.logger.Warn("request_access poll failed", "request_id", requestID, "error", redactedErr(getErr))
			return s.transportSoftError(cctx, getErr, nil), nil
		}
		return s.accessRequestResult(cctx, st, reqState, nil, true), nil
	}

	// The filing arm: compose the same item list `jentic access request`
	// builds (provisioning plans first, then binds, then scope grants), file
	// it, and short-poll for a server-side auto-decision.
	opts, err := requestAccessOptions(args)
	if err != nil {
		return nil, invalidParams(err)
	}
	items, err := opts.compose()
	if err != nil {
		if errors.Is(err, errAccessTargetRequired) {
			return nil, invalidParams(errors.New(`request_access requires a target: "provision" (vendor/name plans), ` +
				`"toolkits" (vendor/name binds), "toolkit_ids" (tk_… binds), or "scopes" — or "request_id" to poll ` +
				`an existing request`))
		}
		// compose()'s messages name the CLI spellings (--provision, --auth,
		// --rules-json); they match this tool's parameter names closely
		// enough to correct the call.
		return nil, invalidParams(err)
	}

	fileResp, err := client.FileAccessRequestWithResponse(cctx, control.AccessRequestFileRequest{
		Reason: strEmptyToNil(opts.reason),
		Items:  items,
	})
	if err != nil {
		s.logger.Warn("request_access failed", "error", redactedErr(err))
		return s.transportSoftError(cctx, err, nil), nil
	}
	var reqState *control.AccessRequestResponse
	var extra map[string]any
	switch {
	case fileResp.JSON202 != nil:
		reqState = fileResp.JSON202
	case fileResp.ApplicationproblemJSON409 != nil:
		dup := fileResp.ApplicationproblemJSON409
		if opts.targetCount() > 1 {
			// Filing is all-or-nothing (same reasoning as accessRequestE): a
			// 409 on a composite means NOTHING was filed — attaching would
			// silently swap the composite for the older, smaller request.
			return s.softErrorNext(cctx, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg: fmt.Sprintf("nothing was filed: one of the requested targets already has a pending request (%s)",
					dup.ExistingRequestId),
				Actionable: fmt.Sprintf("Poll the pending request with request_access {\"request_id\": %q} to see what "+
					"it covers, then either drop that target from this composite and re-file, or ask your operator "+
					"to decide the pending request first.", dup.ExistingRequestId),
				Details: map[string]any{"existing_request_id": dup.ExistingRequestId},
			}, "request_access"), nil
		}
		// Single target: attach to the existing pending request, like the CLI.
		attached, getErr := s.app.getAccessRequest(cctx, client, dup.ExistingRequestId)
		if getErr != nil {
			s.logger.Warn("request_access attach failed", "request_id", dup.ExistingRequestId, "error", redactedErr(getErr))
			return s.transportSoftError(cctx, getErr, nil), nil
		}
		reqState = attached
		extra = map[string]any{"attached_to_existing": true}
	default:
		if fileResp.StatusCode() == http.StatusForbidden {
			// The control plane refused the FILING itself. Not the generic
			// NOT_AUTHENTICATED + get_started mapping (the identity is fine)
			// — and not a request_access pointer either: an agent that may
			// not file requests cannot request the right to file them.
			return s.softErrorNext(cctx, &ux.CodedError{
				Code: ux.CodeBrokerDenied,
				Msg:  fmt.Sprintf("the control plane refused to accept this access request (HTTP 403): %v", apiErrorFor(fileResp, nil)),
				Actionable: "This agent is not permitted to file access requests; relay this error to your " +
					"human operator — they can grant what you need directly in the dashboard.",
			}, "whoami"), nil
		}
		if aerr := apiErrorFor(fileResp, nil); aerr != nil {
			s.logger.Warn("request_access failed", "error", redactedErr(aerr))
			return s.softError(cctx, aerr), nil
		}
		return s.softError(cctx, &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  fmt.Sprintf("unexpected backend response (status %d)", fileResp.StatusCode()),
		}), nil
	}

	if !requestIsTerminal(reqState) {
		reqState = s.awaitAutoDecision(cctx, client, reqState)
	}
	s.logger.Info("request_access", "request_id", reqState.Id, "status", reqState.Status, "items", len(reqState.Items))
	return s.accessRequestResult(cctx, st, reqState, extra, false), nil
}

// awaitAutoDecision short-polls a just-filed request so a server-side
// auto-decision (e.g. the auto-deny of a bare toolkit:bind for an unserved
// API) reaches the model in the SAME tool result. Bounded by
// accessPollBudget — a human approval takes minutes and belongs to the
// model's own request_id polling, never inside one call.
func (s *mcpServer) awaitAutoDecision(ctx context.Context, client *control.ClientWithResponses, req *control.AccessRequestResponse) *control.AccessRequestResponse {
	budget := s.accessPollBudget
	if budget <= 0 {
		budget = defaultAccessPollBudget
	}
	deadline := time.Now().Add(budget)
	pollInitial, pollMax, pollStep := s.app.PollCadence()
	delay := pollInitial
	for {
		if time.Now().After(deadline) {
			return req
		}
		select {
		case <-ctx.Done():
			return req
		case <-time.After(delay):
		}
		if delay < pollMax {
			delay += pollStep
		}
		polled, err := s.app.getAccessRequest(ctx, client, req.Id)
		if err != nil {
			s.logger.Warn("request_access status poll failed", "request_id", req.Id, "error", redactedErr(err))
			return req
		}
		req = polled
		if requestIsTerminal(req) {
			return req
		}
	}
}

// accessRequestResult renders one access request as the tool result: the
// verbatim AccessRequestResponse `jentic access … --json` prints (approve_url
// absolutized onto the environment's base URL) with schema_version and the
// instance stamp joined. Terminal non-approved states map to the same coded
// taxonomy the CLI's exit codes come from (terminalAccessError), phrased for
// a model that polls tools instead of running shell commands. viaPoll marks
// the request_id arm, whose token re-mint is verify-gated (see
// refreshTokenIfScopeGranted).
func (s *mcpServer) accessRequestResult(ctx context.Context, st *clictx.ActiveState, req *control.AccessRequestResponse, extra map[string]any, viaPoll bool) *mcp.CallToolResult {
	absolutizeApproveURL(st.BaseURL, req)
	payload, err := structToMap(req)
	if err != nil {
		return s.softError(ctx, &ux.CodedError{Code: ux.CodeInternalError, Msg: "encode access request: " + err.Error()})
	}
	payload["schema_version"] = mcpSchemaVersion
	for k, v := range extra {
		if _, taken := payload[k]; !taken {
			payload[k] = v
		}
	}

	switch req.Status {
	case statusDenied:
		return s.softErrorExtra(ctx, &ux.CodedError{
			Code: ux.CodeBrokerDenied,
			Msg:  fmt.Sprintf("access request %s was denied", req.Id),
			Actionable: "Read the items' decision_reason in this result to learn why before giving up. A bare " +
				"toolkit bind for an API nothing serves auto-denies — file a provisioning plan " +
				`({"provision": ["vendor/name"], …}) instead. Only re-file if something material changed.`,
		}, "whoami", map[string]any{"request": payload})
	case statusExpired, statusWithdrawn:
		return s.softErrorExtra(ctx, &ux.CodedError{
			Code:       ux.CodeBrokerDenied,
			Msg:        fmt.Sprintf("request %s is %s, not approved; nothing was granted", req.Id, req.Status),
			Actionable: "File a fresh request_access naming what you still need, with a clear reason.",
		}, "request_access", map[string]any{"request": payload})
	case statusPartiallyApproved:
		// A granted scope only takes effect once re-minted into the token —
		// do it now, like the CLI, so the model needn't know about refresh.
		s.refreshTokenIfScopeGranted(ctx, st, req, viaPoll)
		return s.softErrorExtra(ctx, &ux.CodedError{
			Code: ux.CodePartialApproval,
			Msg:  "partially approved — not all requested items were granted",
			Actionable: "Check items[].status in this result: proceed only with what was approved, and do not " +
				"assume the rest is available.",
		}, "whoami", map[string]any{"request": payload})
	case statusApproved:
		s.refreshTokenIfScopeGranted(ctx, st, req, viaPoll)
		return s.result(ctx, payload)
	default: // pending
		payload["instruction"] = pendingAccessInstruction
		return s.result(ctx, payload)
	}
}

// refreshTokenIfScopeGranted is refreshIfScopeGranted for the MCP surface:
// when a decided request granted a scope, re-mint the agent token so the next
// tool call already carries it (the catalog:import loop depends on this).
// Best-effort and silent to the wire — a mint failure only logs; bindings
// take effect live server-side and need no re-mint.
//
// viaPoll gates the request_id arm: a model may poll an already-decided
// request any number of times, and an unconditional re-mint per poll is pure
// churn (InvalidateTokens + an assertion exchange each time). On that arm the
// mint runs only when GET /me shows the granted scope actually missing from
// the presented token (staleScopes); the filing arm — which observes the
// decision exactly once — keeps the unconditional CLI behavior.
func (s *mcpServer) refreshTokenIfScopeGranted(ctx context.Context, st *clictx.ActiveState, req *control.AccessRequestResponse, viaPoll bool) {
	if !requestGrantedScope(req) {
		return
	}
	creds, err := credsFromState(ctx, st)
	if err != nil {
		s.logger.Warn("skipping token re-mint; mint transport unavailable", "error", redactedErr(err))
		return
	}
	if creds.InjectedBearerToken != "" {
		return // static credential: nothing mintable to refresh
	}
	if key, err := auth.ReadAPIKey(creds.IdentityRef()); err == nil && key != "" {
		return
	}
	if viaPoll {
		me, err := s.app.getMe(ctx)
		if err != nil {
			s.logger.Warn("skipping token re-mint; staleness check failed", "error", redactedErr(err))
			return
		}
		if !grantedScopeStale(req, me) {
			return // token already carries every granted scope — nothing to do
		}
	}
	if _, err := auth.RefreshBearerToken(creds); err != nil {
		s.logger.Warn("granted scope not yet on the token; re-mint failed", "error", redactedErr(err))
	}
}

// grantedScopeStale reports whether any scope this request granted is missing
// from the token the agent presents (per staleScopes' semantics: a server
// that reports no token scopes at all makes staleness unknowable, which reads
// as not-stale — skip the mint rather than churn).
func grantedScopeStale(req *control.AccessRequestResponse, me *control.MeAgent) bool {
	stale := staleScopes(me.Scopes, me.TokenScopes)
	if len(stale) == 0 {
		return false
	}
	staleSet := make(map[string]struct{}, len(stale))
	for _, sc := range stale {
		staleSet[sc] = struct{}{}
	}
	for _, it := range req.Items {
		if it.ResourceType != "scope" || it.Action != "grant" || it.Status != "approved" || it.ResourceId == nil {
			continue
		}
		if _, missing := staleSet[*it.ResourceId]; missing {
			return true
		}
	}
	return false
}

// requestAccessOptions folds the normalized arguments onto the CLI's
// accessRequestOptions so compose() — the one plan builder — is reused
// verbatim (provisioning chains, keyed --auth/--rules-json resolution,
// duplicate/conflict validation).
func requestAccessOptions(args map[string]any) (*accessRequestOptions, error) {
	opts := &accessRequestOptions{}
	if v, ok := args["provision"].([]string); ok {
		opts.provisions = v
	}
	if v, ok := args["toolkits"].([]string); ok {
		opts.toolkits = v
	}
	if v, ok := args["toolkit_ids"].([]string); ok {
		opts.toolkitIDs = v
	}
	if v, ok := args["scopes"].([]string); ok {
		opts.scopes = v
	}
	if v, ok := args["auth"].([]string); ok {
		opts.auths = v
	}
	if v, ok := args["reason"].(string); ok {
		opts.reason = v
	}
	rules, err := rulesJSONValues(args["rules_json"])
	if err != nil {
		return nil, err
	}
	opts.rulesJSONs = rules
	return opts, nil
}

// rulesJSONValues normalizes the rules_json argument onto compose()'s
// repeatable string values. Models send it three ways: the natural JSON array
// of rule objects (kept whole as ONE stringified value — commas inside rules
// must never split), a single string (bare or keyed), or a list of keyed
// strings for multi-provision requests.
func rulesJSONValues(v any) ([]string, error) {
	raw, ok := v.(json.RawMessage)
	if !ok || len(raw) == 0 {
		return nil, nil
	}
	var decoded any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		return nil, fmt.Errorf(`parameter "rules_json": %w`, err)
	}
	switch t := decoded.(type) {
	case string:
		return []string{t}, nil
	case []any:
		values := make([]string, 0, len(t))
		for _, e := range t {
			s, isStr := e.(string)
			if !isStr {
				// An array carrying rule OBJECTS is the rules document
				// itself: one bare value, verbatim.
				return []string{string(raw)}, nil
			}
			values = append(values, s)
		}
		return values, nil
	case map[string]any:
		// A single rule object: wrap it into the array compose expects.
		return []string{"[" + string(raw) + "]"}, nil
	default:
		return nil, fmt.Errorf(`parameter "rules_json": expected a JSON array of rules, a string, or a list of keyed strings, got %T`, decoded)
	}
}

// structToMap re-projects a typed response onto a map so schema_version and
// the instance stamp can join it as top-level siblings (the whoami pattern).
func structToMap(v any) (map[string]any, error) {
	raw, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	return m, nil
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

var requestAccessSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"request_id": map[string]any{
			"type": "string",
			"description": "Poll an existing request instead of filing a new one: the id from an earlier " +
				"request_access result. Mutually exclusive with the target parameters.",
		},
		"provision": map[string]any{
			"type":  "array",
			"items": map[string]any{"type": "string"},
			"description": "APIs to file full provisioning plans for, as vendor/name[/version] references " +
				"(e.g. \"stripe.com/api\"). Use when NOTHING you're bound to serves the API yet: the plan " +
				"describes the whole path to first execution (create toolkit, provision + bind a credential " +
				"with your proposed rules, bind you), which a human fulfils and approves.",
		},
		"toolkits": map[string]any{
			"type":  "array",
			"items": map[string]any{"type": "string"},
			"description": "Toolkits to be bound to, as vendor/name[/version] API references. The LAST MILE only: " +
				"use when a toolkit already serves the API and you just aren't bound to it — for an unserved " +
				"API this auto-denies; use provision instead.",
		},
		"toolkit_ids": map[string]any{
			"type":        "array",
			"items":       map[string]any{"type": "string"},
			"description": "Toolkits to be bound to, by id (tk_…).",
		},
		"scopes": map[string]any{
			"type":        "array",
			"items":       map[string]any{"type": "string"},
			"description": "Token scopes to request, e.g. \"catalog:import\".",
		},
		"auth": map[string]any{
			"type":  "array",
			"items": map[string]any{"type": "string"},
			"description": "Credential auth type for a provision plan, read from the API's security schemes: " +
				"bearer, api_key, basic, oauth2, or none (default bearer). With several provisions, key each " +
				"value by API: \"vendor/name=bearer\".",
		},
		"rules_json": map[string]any{
			"description": "Proposed permission rules for a provision plan, as a JSON array of rules, e.g. " +
				`[{"effect":"allow","methods":["GET"],"path":".*"}]. A human reviews and edits them before ` +
				"approving. With several provisions, pass a list of keyed strings: \"vendor/name=<json>\".",
		},
		"reason": map[string]any{
			"type": "string",
			"description": "Why you need this — ALWAYS include it: the human who approves reads it, and a clear " +
				"one-liner is what gets you approved faster.",
		},
	},
}

// accessToolSpecs declares the 2-E1 tool surface. Annotations per master
// §3.2: search_catalog is read-only; import_api carries idempotentHint and NO
// readOnlyHint (re-import of the same api_id converges); request_access
// carries neither readOnlyHint nor destructiveHint (additive: it files
// intent, destroys nothing, and duplicate filings are deduped server-side —
// but each filing pages a human, so it is not annotated idempotent either).
// --read-only therefore serves search_catalog and withholds the other two.
func (s *mcpServer) accessToolSpecs() []mcpToolSpec {
	return []mcpToolSpec{
		{
			tool: &mcp.Tool{
				Name:  "search_catalog",
				Title: "Search the public API catalog",
				Description: "Search the public API catalog for importable APIs by keyword. The registry that " +
					"search_apis reads only sees IMPORTED APIs — when search_apis returns an empty data " +
					"array, the API probably isn't imported yet: find it here, import it with import_api, " +
					"then search_apis again (reading the registry and importing need no access request). " +
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
					"(agents hold it by default); on a denial, request it via request_access — do not guess " +
					"other scopes. Importing makes an API discoverable but does NOT grant access to call " +
					"it: check whoami for a toolkit binding serving it — never execute just to probe — and " +
					"use request_access if nothing serves it.",
				InputSchema: importAPISchema,
				Annotations: &mcp.ToolAnnotations{IdempotentHint: true},
			},
			handler: s.handleImportAPI,
		},
		{
			tool: &mcp.Tool{
				Name:  "request_access",
				Title: "Request access from a human operator",
				Description: "File one access request for the access you are missing, or poll a filed one by id. " +
					"Decide from whoami FIRST — never execute an operation just to probe whether you have " +
					"access. If a whoami binding already serves the API, you have access; if NOTHING serves " +
					`it, file a provisioning plan: {"provision": ["vendor/name"], "auth": ["bearer"], ` +
					`"rules_json": [{"effect":"allow","methods":["GET"],"path":".*"}], "reason": "…"} — it ` +
					"describes the whole path to first execution (create toolkit, provision + bind a " +
					"credential with your proposed rules, bind you), which a human fulfils in the dashboard " +
					"(they enter the secret; it never rides in your request). A bare toolkit bind " +
					`({"toolkits": ["vendor/name"]}) is only the last mile when a toolkit already serves the ` +
					"API; for an unserved API it auto-denies — use provision. ALWAYS include reason. File " +
					"once, richly: combine every target this task needs into ONE composite request instead " +
					"of filing piecemeal. The result carries approve_url: relay it to your human operator — " +
					"granting is always a human action and this tool NEVER approves. Poll the decision with " +
					`{"request_id": "<id>"}; never re-file while a request is pending. Once approved, ` +
					"bindings are live immediately — retry the execute that was denied.",
				InputSchema: requestAccessSchema,
				Annotations: &mcp.ToolAnnotations{},
			},
			handler: s.handleRequestAccess,
		},
	}
}
