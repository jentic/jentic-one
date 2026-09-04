package api

// mcp_execute.go holds the PR 1-C execute surface: the `execute` tool (any
// method, destructiveHint), its `execute_read` GET/HEAD-only variant
// (readOnlyHint), and the `get_execution_result` poll tool for held (202)
// executes (§3.4). The handlers drive the agentops core (resolve → build →
// send → classify) exactly like `jentic execute`, with three deliberate
// differences:
//
//   - The body is ALWAYS an explicit tool argument (or absent). There is no
//     stdin fallback on this path — under stdio MCP, stdin is the JSON-RPC
//     wire — and the handlers are structurally unable to reach executeE's
//     stdin sniffing: they never consult os.Stdin, and the agentops core they
//     call has no stdin access by design (§3.7).
//   - The broker leg rides the SEC-20 CA-pinned client from clictx
//     (clictx.BrokerHTTPClient) with the session's attribution hook composed
//     over it — wrap, never displace (§3.7.2). The CLI's flag-driven broker
//     override surface does not exist here: the environment's broker_url is
//     the only broker source (SEC-21 pinning is structural).
//   - Relayed bodies are capped at maxResultBytes (§3.7 context protection —
//     the transport's 64 MiB bound is not context protection) with the
//     truncation envelope {truncated: true, total_bytes, execution_id}, and
//     relayed response headers ride under their own aggregate budget
//     (headerBytesBudget) with a headers_truncated marker — headers must not
//     smuggle around the body cap.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	sdkclient "github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/config"
)

// defaultMaxResultBytes is the §3.7 context-protection cap on a relayed
// response body in a tool result (overridable with --max-result-bytes). MCP
// has no chunking — a tool result lands in the model's context whole — so
// this is deliberately far below the 64 MiB transport bound
// (client.MaxBodyBytes): 128 KiB is roughly a 30k-token page, large enough
// for any sane API page and small enough that one oversized upstream response
// cannot evict the session's working context.
const defaultMaxResultBytes int64 = 128 << 10

// executeParams is the execute tools' argument surface: the shared
// operation-identity aliases, the §3.2 inputs alias table (inputs/params/
// parameters), an explicit headers object, the raw JSON body, and the CLI's
// --revision/--idempotency-key knobs. There are deliberately NO broker-target
// parameters: the broker is pinned to the environment's broker_url.
var executeParams = []paramSpec{
	operationIDSpec,
	inputsSpec,
	{name: "headers", kind: paramObject},
	{name: "body", aliases: []string{"data"}, kind: paramJSON},
	{name: "revision", kind: paramString},
	{name: "idempotency_key", kind: paramString},
}

// jobIDSpec is get_execution_result's single parameter. `execution_id` is NOT
// an alias: the broker's Jentic-Execution-Id names the execution record, not
// the job — folding them would silently poll the wrong resource.
var jobIDSpec = paramSpec{name: "job_id", aliases: []string{"id", "job"}, kind: paramString}

// getExecutionResultParams declares the poll tool's argument table.
var getExecutionResultParams = []paramSpec{jobIDSpec}

// resolveMCPBrokerTarget resolves the broker scheme/host for the MCP execute
// path. Precedence is a strict subset of executeE's: the environment's
// broker_url wins, else the built-in loopback default — there are no flag
// overrides on this surface, so the SEC-21 machine-mode pin holds
// structurally. The two executeE fail-closed guards port unchanged:
//   - a SET but malformed broker_url errors rather than silently dialing the
//     loopback default (M2);
//   - a remote control plane with NO broker configured refuses rather than
//     leaking the bearer at the caller's own loopback (remote-cli-usage F1).
func resolveMCPBrokerTarget(st *clictx.ActiveState) (scheme, host string, err error) {
	if st.BrokerURL != "" {
		u, perr := url.Parse(st.BrokerURL)
		if perr != nil || u.Host == "" || u.Scheme == "" {
			return "", "", &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg: fmt.Sprintf("environment %q has a malformed broker_url (%q): it must be an absolute URL with a scheme and host",
					st.EnvironmentName, st.BrokerURL),
				Actionable: fmt.Sprintf("Ask your operator to fix it with `jentic env add %s --broker-url https://<broker-host>:<port> --force`.",
					st.EnvironmentName),
			}
		}
		return u.Scheme, u.Host, nil
	}
	if baseURLIsRemote(st.BaseURL) {
		return "", "", &ux.CodedError{
			Code: ux.CodeResolveFailed,
			Msg: fmt.Sprintf("environment %q has a remote control plane (%s) but no broker is configured; "+
				"execute would target the local default %s://%s",
				st.EnvironmentName, st.BaseURL, config.DefaultBrokerScheme, config.DefaultBrokerHost),
			Actionable: fmt.Sprintf("Ask your operator to set the environment's broker_url "+
				"(`jentic env add %s --url %s --broker-url https://<broker-host>:<port> --force`) — it is never derived from the control plane.",
				st.EnvironmentName, st.BaseURL),
		}
	}
	return config.DefaultBrokerScheme, config.DefaultBrokerHost, nil
}

func (s *mcpServer) handleExecute(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	return s.executeTool(ctx, req, false)
}

func (s *mcpServer) handleExecuteRead(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	return s.executeTool(ctx, req, true)
}

// executeTool is the shared execute/execute_read handler. readOnlyVariant
// enforces the GET/HEAD-only contract of execute_read AFTER resolution (the
// method is a property of the resolved operation, not of the arguments).
func (s *mcpServer) executeTool(ctx context.Context, req *mcp.CallToolRequest, readOnlyVariant bool) (*mcp.CallToolResult, error) {
	toolName := "execute"
	if readOnlyVariant {
		toolName = "execute_read"
	}
	s.noteClient(req.ClientInfo())
	// The execute family gets the wider deadline: the broker leg relays
	// arbitrary upstream latency, and the 30s control-plane deadline would
	// silently cap the 60s execute ceiling (agentops.DoWith's client timeout).
	cctx, cancel := s.executeCallContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, executeParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	target, _ := args["operation_id"].(string)
	if target == "" {
		return nil, invalidParams(errors.New(toolName + ` requires "operation_id" (aliases: "id", "uuid"): ` +
			`a registry operation id from a search_apis hit, or a METHOD:url pair like "GET:https://api.example.com/v1/things"`))
	}
	body, _ := args["body"].(json.RawMessage)
	if readOnlyVariant && len(body) > 0 {
		return nil, invalidParams(errors.New("execute_read never sends a request body; use the execute tool for body-carrying calls"))
	}
	revision, _ := args["revision"].(string)

	st := clictx.ActiveContext(cctx)
	if st == nil {
		return s.softError(cctx, noContextErr()), nil
	}
	// Auth failures (not registered / pending / revoked) map to their coded
	// soft errors with the default get_started pointer (§3.7 table).
	_, token, err := s.app.contextSession(cctx, st)
	if err != nil {
		s.logger.Warn(toolName+" auth failed", "error", err)
		return s.softError(cctx, err), nil
	}
	brokerScheme, brokerHost, err := resolveMCPBrokerTarget(st)
	if err != nil {
		// A broker-target gap is a setup problem: default pointer (get_started).
		return s.softError(cctx, err), nil
	}

	op, err := s.app.resolveOperation(cctx, target, revision)
	if err != nil {
		return s.executeResolveError(cctx, target, err), nil
	}
	if readOnlyVariant && op.Method != http.MethodGet && op.Method != http.MethodHead {
		return nil, invalidParams(fmt.Errorf(
			"operation %q resolves to %s — execute_read only performs GET/HEAD; call the execute tool instead", target, op.Method))
	}

	pathParams, queryParams := splitInputs(args, op)
	headers, err := headerKVs(args)
	if err != nil {
		return nil, invalidParams(err)
	}
	var bodyReader *bytes.Reader
	execReq := agentops.ExecuteRequest{
		Method:         op.Method,
		URL:            op.URL,
		Path:           op.Path,
		PathParams:     pathParams,
		QueryParams:    queryParams,
		Headers:        headers,
		BrokerScheme:   brokerScheme,
		BrokerHost:     brokerHost,
		Token:          token,
		SessionID:      sdkclient.SanitizeSessionID(st.SessionID),
		IdempotencyKey: stringArg(args, "idempotency_key"),
	}
	if len(body) > 0 {
		bodyReader = bytes.NewReader(body)
		execReq.Body = bodyReader
	}

	breq, err := agentops.BuildRequest(cctx, execReq)
	if err != nil {
		// SEC-1 (bearer over plaintext to a non-loopback broker) is the only
		// coded failure here — a setup problem, default pointer.
		return s.softError(cctx, err), nil
	}

	// §3.7.2: the broker leg rides clictx's SEC-20 CA-pinned client with the
	// session's attribution hook composed over it; a broken ca_cert_path
	// fails closed, exactly like every control-plane call.
	hc, err := clictx.BrokerHTTPClient(cctx)
	if err != nil {
		return s.softError(cctx, err), nil
	}
	res, err := agentops.DoWith(hc, breq)
	if err != nil {
		// Retrying is provably safe when the call cannot double-execute: a
		// GET/HEAD, or a mutating call the broker de-duplicates on its
		// Idempotency-Key. Anything else defers to the pre-send/mid-flight
		// classification inside executeTransportError.
		retrySafe := execReq.IdempotencyKey != "" ||
			op.Method == http.MethodGet || op.Method == http.MethodHead
		return s.executeTransportError(cctx, err, retrySafe), nil
	}
	s.logger.Info(toolName, "target", target, "method", op.Method, "status", res.Status, "execution_id", res.ExecutionID)

	// §3.7 table row 2: a broker denial is a soft error carrying the verbatim
	// agent_directive, non-retryable until access changes.
	if denial := agentops.Classify(res); denial != nil {
		return s.executeDenialError(cctx, denial), nil
	}

	// Everything the broker relayed — 2xx, upstream 4xx/5xx, and the ask-tier
	// 202-held envelope — is a normal result (§3.7 table row 1; §3.4: the held
	// envelope passes through with its directive intact, the model polls with
	// get_execution_result and never re-sends).
	return s.result(cctx, s.executeResultPayload(res)), nil
}

// splitInputs folds the normalized `inputs` object onto agentops' path/query
// split: a key matching a {placeholder} in the resolved target is a path
// parameter, everything else is a query parameter. Keys are applied in sorted
// order so the built URL is deterministic. Non-string scalars format like the
// normalizer's string coercion; structured values marshal compact.
func splitInputs(args map[string]any, op *agentops.Operation) (pathParams, queryParams []agentops.KV) {
	inputs, _ := args["inputs"].(map[string]any)
	if len(inputs) == 0 {
		return nil, nil
	}
	target := op.URL
	if target == "" {
		target = op.Path
	}
	keys := make([]string, 0, len(inputs))
	for k := range inputs {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		kv := agentops.KV{Key: k, Value: inputValueString(inputs[k])}
		if strings.Contains(target, "{"+k+"}") {
			pathParams = append(pathParams, kv)
		} else {
			queryParams = append(queryParams, kv)
		}
	}
	return pathParams, queryParams
}

// inputValueString renders one inputs/headers value as the wire string:
// strings verbatim, scalars via their JSON form, structured values compact.
func inputValueString(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	raw, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprintf("%v", v)
	}
	return string(raw)
}

// headerKVs converts the normalized `headers` object to KV pairs. A structured
// header value is malformed input (headers are strings on the wire).
func headerKVs(args map[string]any) ([]agentops.KV, error) {
	obj, _ := args["headers"].(map[string]any)
	if len(obj) == 0 {
		return nil, nil
	}
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make([]agentops.KV, 0, len(keys))
	for _, k := range keys {
		s, err := coerceString(obj[k])
		if err != nil {
			return nil, fmt.Errorf("header %q: %w", k, err)
		}
		out = append(out, agentops.KV{Key: k, Value: s})
	}
	return out, nil
}

func stringArg(args map[string]any, name string) string {
	s, _ := args[name].(string)
	return s
}

// executeResolveError maps a resolve failure per the §3.7 table: an unknown
// operation is RESOLVE_FAILED with "re-search" guidance pointed at search_apis
// (deliberate — the identity already resolved; the id is wrong, so get_started
// would be a dead end). Non-resolve codes (an auth failure surfacing inside
// the inspect call) keep their own default pointers.
func (s *mcpServer) executeResolveError(ctx context.Context, target string, err error) *mcp.CallToolResult {
	s.logger.Warn("execute resolve failed", "target", target, "error", err)
	coded := asCoded(err)
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
	s.logger.Warn("transport failure", "error", err, "retry_safe", retrySafe)
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
				"If the execute returned a held (202) job, poll it with get_execution_result — never re-send.",
			Details: coded.Details,
			Cause:   coded.Cause,
		}
	}
	return s.softErrorExtra(ctx, coded, "get_started", map[string]any{"retryable": retryable})
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
// changes. next_tool is whoami (deliberate): the §3.2 flow guidance is "check
// your bindings, never execute to probe access", and no access-request tool
// exists on this surface yet (it queues behind this PR).
func (s *mcpServer) executeDenialError(ctx context.Context, denial *agentops.Denial) *mcp.CallToolResult {
	coded := denial.Err()
	extra := map[string]any{"retryable": false}
	if denial.Directive != nil {
		extra["agent_directive"] = denial.DirectiveRaw
		coded.Actionable = denial.Directive.Instruction
	}
	if coded.Actionable == "" {
		// UX7's synthesized recovery, tool-flavored: no denial is a dead end.
		coded.Actionable = synthesizedDenialHint(denial.Status)
	}
	return s.softErrorExtra(ctx, coded, "whoami", extra)
}

// synthesizedDenialHint is the MCP counterpart of the CLI's status-keyed
// denial recovery (ux.RenderSynthesizedDenialRecovery): same semantics,
// phrased for a model that can call tools but must relay operator commands.
func synthesizedDenialHint(status int) string {
	switch status {
	case http.StatusForbidden:
		return "This agent isn't bound to a toolkit serving this API. Call whoami to see your bindings, " +
			"then ask your operator to grant access (`jentic access request --toolkit <vendor/name> --wait`)."
	case http.StatusFailedDependency:
		return "No credential is provisioned for this call. Ask your operator to provision one " +
			"(`jentic access request --toolkit <vendor/name> --provision --wait`), then retry."
	case http.StatusUnauthorized:
		return "The stored upstream credential needs reconnecting. Ask your operator to re-provision it " +
			"(`jentic access request --toolkit <vendor/name> --provision --wait`), then retry."
	default:
		return "The broker denied this call before it reached the upstream API. Call whoami to check what you can run."
	}
}

// executeResultPayload projects an ExecuteResult onto the tool payload: the
// exact CLI envelope keys ({schema_version, status, headers, body,
// execution_id} — shared goldens compare this sub-object with the sibling
// `instance` stamp stripped), with the §3.7 size cap applied to the body AND
// an aggregate budget applied to the headers (Go's HTTP/1 transport accepts
// up to 10 MiB of response headers — without their own budget they would ride
// straight around the body cap). A capped body is replaced by its leading
// maxResultBytes bytes — sanitized to valid UTF-8, which also strips invalid
// sequences a binary body carries anywhere — as a string plus the truncation
// envelope {truncated: true, total_bytes, execution_id} so the model can
// fetch or act deliberately instead of flooding its context. Normal-sized
// responses are relayed byte-identically to the CLI envelope.
func (s *mcpServer) executeResultPayload(res *agentops.ExecuteResult) map[string]any {
	env := res.Envelope()
	headers, headersTruncated := capHeaders(env.Headers, s.headerBytesBudget())
	payload := map[string]any{
		"schema_version": env.SchemaVersion,
		"status":         env.Status,
		"headers":        headers,
		"body":           env.Body,
	}
	if headersTruncated {
		payload["headers_truncated"] = true
		s.logger.Info("execute response headers truncated", "cap", s.headerBytesBudget())
	}
	if env.ExecutionID != "" {
		payload["execution_id"] = env.ExecutionID
	}
	if int64(len(res.Body)) > s.maxResultBytes {
		payload["body"] = strings.ToValidUTF8(string(res.Body[:s.maxResultBytes]), "")
		payload["truncated"] = true
		payload["total_bytes"] = len(res.Body)
		s.logger.Info("execute result truncated", "total_bytes", len(res.Body), "cap", s.maxResultBytes)
	}
	return payload
}

// headerBytesBudget is the slice of the §3.7 cap reserved for relayed
// response headers: 8 KiB — generous for any sane API's headers — clamped to
// the whole result cap when the operator shrank --max-result-bytes below it.
func (s *mcpServer) headerBytesBudget() int64 {
	const headerBudget int64 = 8 << 10
	if s.maxResultBytes < headerBudget {
		return s.maxResultBytes
	}
	return headerBudget
}

// capHeaders bounds the aggregate serialized size of the relayed response
// headers to budget bytes. Headers are admitted whole in sorted key order; a
// header that would overflow the remaining budget is dropped whole (a
// half-relayed value is worse than an absent one) while smaller later headers
// still fit — one hostile fat header must not evict Content-Type. When
// everything fits (every normal response), the input map is returned
// UNTOUCHED so the shared CLI-envelope bytes cannot change.
func capHeaders(headers map[string]string, budget int64) (map[string]string, bool) {
	var total int64
	for k, v := range headers {
		total += headerCost(k, v)
	}
	if total <= budget {
		return headers, false
	}
	keys := make([]string, 0, len(headers))
	for k := range headers {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	capped := make(map[string]string, len(keys))
	var used int64
	for _, k := range keys {
		cost := headerCost(k, headers[k])
		if used+cost > budget {
			continue
		}
		used += cost
		capped[k] = headers[k]
	}
	return capped, true
}

// headerCost estimates one header's serialized JSON size (pre-escaping —
// close enough for a budget that sits far below the body cap).
func headerCost(k, v string) int64 {
	const perEntryOverhead = 6 // two quote pairs, colon, comma
	return int64(len(k) + len(v) + perEntryOverhead)
}

// handleGetExecutionResult polls one job through the LIVE control-plane
// routes: GET /jobs/{id} for the status, plus GET /jobs/{id}/result once the
// job completed (agent tokens hold jobs:read by default — §3.4/§3.2). This is
// the recovery path for a held (202) execute: poll until terminal, never
// re-send the execute.
func (s *mcpServer) handleGetExecutionResult(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	args, err := normalizeToolArgs(req.Params.Arguments, getExecutionResultParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	jobID, _ := args["job_id"].(string)
	if jobID == "" {
		return nil, invalidParams(errors.New(`get_execution_result requires "job_id" (aliases: "id", "job"): ` +
			`the job id from a held (202) execute response`))
	}

	client, err := s.app.controlClient(cctx)
	if err != nil {
		s.logger.Warn("get_execution_result failed", "error", err)
		return s.softError(cctx, err), nil
	}
	resp, jobErr := client.GetJobWithResponse(cctx, jobID)
	if err := apiErrorFor(resp, jobErr); err != nil {
		var he *HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
			// The identity resolved fine — the job id is wrong, so the
			// get_started default would be a dead end. The recovery is
			// re-reading the held execute envelope and calling this tool
			// again (self-pointer, deliberate).
			return s.softErrorNext(cctx, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("job %q not found", jobID),
				Actionable: "Re-check the job id — it is carried by the held (202) execute response — and call " +
					"get_execution_result again with the exact value.",
			}, "get_execution_result"), nil
		}
		s.logger.Warn("get_execution_result failed", "job_id", jobID, "error", err)
		// §3.7 transport row: a transport failure on the RECOVERY path ("the
		// control plane is briefly down, poll again") must come back as a
		// retryable TRANSPORT_ERROR with the get_started pointer, never as
		// INTERNAL_ERROR. The poll is a GET — re-polling is always safe.
		return s.executeTransportError(cctx, classifyTransportErr(err), true), nil
	}
	if resp.JSON200 == nil {
		return s.softError(cctx, &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  fmt.Sprintf("unexpected backend response (status %d)", resp.StatusCode()),
		}), nil
	}

	job := resp.JSON200
	payload := map[string]any{
		"schema_version": mcpSchemaVersion,
		"job_id":         job.JobId,
		"kind":           job.Kind,
		"status":         job.Status,
	}
	if job.Error != nil && *job.Error != "" {
		payload["error"] = *job.Error
	}
	if job.ExecutionId != nil && *job.ExecutionId != "" {
		payload["execution_id"] = *job.ExecutionId
	}
	if job.Status == catJobCompleted {
		s.attachJobResult(cctx, client, jobID, payload)
	}
	return s.result(cctx, payload), nil
}

// attachJobResult adds the completed job's result document to the payload
// (GET /jobs/{id}/result), size-capped like every relayed body (a capped
// result is its leading maxResultBytes bytes, sanitized to valid UTF-8). A
// result fetch failure degrades to a `result_error` note rather than failing
// the poll — the status the model asked for is already in hand.
func (s *mcpServer) attachJobResult(ctx context.Context, client jobResultClient, jobID string, payload map[string]any) {
	resp, err := client.GetJobResultWithResponse(ctx, jobID)
	if err := apiErrorFor(resp, err); err != nil {
		s.logger.Warn("get_execution_result: result fetch failed", "job_id", jobID, "error", err)
		payload["result_error"] = fmt.Sprintf("the job completed but its result could not be fetched: %v", err)
		return
	}
	if int64(len(resp.Body)) > s.maxResultBytes {
		payload["result"] = strings.ToValidUTF8(string(resp.Body[:s.maxResultBytes]), "")
		payload["truncated"] = true
		payload["total_bytes"] = len(resp.Body)
		return
	}
	var result any
	if len(resp.Body) > 0 && json.Unmarshal(resp.Body, &result) == nil {
		payload["result"] = result
	} else if len(resp.Body) > 0 {
		payload["result"] = string(resp.Body)
	}
}

// jobResultClient is the one generated-client method attachJobResult needs —
// a seam so tests can drive the result fetch without the whole SDK surface.
type jobResultClient interface {
	GetJobResultWithResponse(ctx context.Context, jobID string, reqEditors ...control.RequestEditorFn) (*control.GetJobResultHTTPResp, error)
}

// The execute tools' input schema. Permissive like every 1-A/1-B schema (no
// additionalProperties:false, no alias properties — the normalizer resolves
// them handler-side); the declared shapes are the canonical spellings.
func executeInputSchema(withBody bool) map[string]any {
	props := map[string]any{
		"operation_id": map[string]any{
			"type": "string",
			"description": "The operation to execute (required; \"id\" and \"uuid\" are accepted aliases): a registry " +
				"operation id from a search_apis hit, or a METHOD:url pair like \"GET:https://api.example.com/v1/things\".",
		},
		"inputs": map[string]any{
			"type": "object",
			"description": "Operation parameters as one flat object (\"params\"/\"parameters\" are accepted aliases): " +
				"keys matching a {placeholder} in the operation's path fill it; every other key is sent as a query parameter.",
		},
		"headers": map[string]any{
			"type":        "object",
			"description": "Extra request headers as a string-valued object.",
		},
		"revision": map[string]any{
			"type":        "string",
			"description": "Pin a specific revision ID for reproducibility (defaults to the current revision).",
		},
	}
	if withBody {
		props["body"] = map[string]any{
			"description": "The request body as a JSON value (object, array, or string; \"data\" is an accepted alias). " +
				"Omit it for bodyless calls — the body is ALWAYS this argument, never read from anywhere else. " +
				"A string whose content parses as JSON is deliberately treated as a stringified body and sent " +
				"as that JSON value, not as a quoted string.",
		}
		props["idempotency_key"] = map[string]any{
			"type": "string",
			"description": "Optional Idempotency-Key so a retried POST/PUT is de-duplicated by the broker instead of " +
				"running twice.",
		}
	}
	return map[string]any{"type": "object", "properties": props}
}

var getExecutionResultSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"job_id": map[string]any{
			"type": "string",
			"description": "The job to poll (required; \"id\" and \"job\" are accepted aliases), from a held (202) " +
				"execute response.",
		},
	},
}

// executeToolSpecs declares the PR 1-C tool surface. Annotations per master
// §3.2: execute is the ONLY tool carrying destructiveHint (openWorldHint on
// both execute variants — they reach arbitrary upstream APIs); execute_read
// and get_execution_result are read-only. --read-only therefore withholds
// exactly `execute`.
func (s *mcpServer) executeToolSpecs() []mcpToolSpec {
	openWorld := true
	destructive := true
	return []mcpToolSpec{
		{
			tool: &mcp.Tool{
				Name:  "execute",
				Title: "Execute an API operation",
				Description: "Execute an operation through the Jentic broker: the broker authenticates this agent " +
					"and injects the stored upstream credential server-side — credentials never pass through " +
					"this session. This is the final step of the flow (whoami → search_apis → " +
					"inspect_operation → execute): always inspect the contract first, and never execute just " +
					"to probe whether you have access (call whoami). " +
					`Example: {"operation_id": "op_abc123", "inputs": {"petId": "42", "limit": 10}, ` +
					`"body": {"name": "Bob"}}. ` +
					"Returns {status, headers, body, execution_id}: any HTTP status, including upstream " +
					"4xx/5xx, is the upstream's answer — a denial by the broker itself comes back as an " +
					"error result with recovery directions instead. A 202 response with a directive means " +
					"the call is HELD for human approval: poll it with get_execution_result using the job id " +
					"it carries — never re-send the execute. Large bodies are truncated " +
					"({truncated: true, total_bytes}); narrow the call (query parameters, pagination) to see " +
					"the rest. Prefer execute_read for pure reads — it is approved more readily.",
				InputSchema: executeInputSchema(true),
				Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: &openWorld},
			},
			handler: s.handleExecute,
		},
		{
			tool: &mcp.Tool{
				Name:  "execute_read",
				Title: "Execute a read-only API operation",
				Description: "Execute a GET or HEAD operation through the Jentic broker — the read-only variant of " +
					"execute (same envelope, same flow, no request body). Use it for every pure read: clients " +
					"approve read-only tools more readily. If the operation resolves to any other HTTP " +
					"method, the call is rejected — use execute. " +
					`Example: {"operation_id": "GET:https://rest.coincap.io/v3/markets", "inputs": {"limit": 5}}.`,
				InputSchema: executeInputSchema(false),
				Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true, OpenWorldHint: &openWorld},
			},
			handler: s.handleExecuteRead,
		},
		{
			tool: &mcp.Tool{
				Name:  "get_execution_result",
				Title: "Poll a held or asynchronous execution",
				Description: "Poll a job by id: returns {job_id, kind, status, ...} and, once status is " +
					"\"completed\", the result document. Use it when execute returns a 202 HELD response " +
					"(human approval required): poll with the job id it carries until the status is terminal " +
					"(completed, failed, cancelled, dead_letter) — NEVER re-send the execute while a job is " +
					"pending; approval happens out-of-band and re-sending duplicates the call. " +
					`Example: {"job_id": "job_abc123"}. While pending/running, wait briefly and poll again.`,
				InputSchema: getExecutionResultSchema,
				Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true},
			},
			handler: s.handleGetExecutionResult,
		},
	}
}
