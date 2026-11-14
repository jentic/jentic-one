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
			`a METHOD:url pair like "GET:https://api.example.com/v1/things" (a search_apis hit's target)`))
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
		s.logger.Warn(toolName+" auth failed", "error", redactedErr(err))
		return s.softError(cctx, err), nil
	}
	agent := st.IdentityName + "@" + st.EnvironmentName
	if req.Params.RequestState != "" {
		// A multi-round-trip retry of a held execute: never re-send it.
		return s.resumeHeld(cctx, req, agent)
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
	// Retrying is provably safe when the call cannot double-execute: a GET/HEAD,
	// or a mutating call the broker de-duplicates on its Idempotency-Key. Both
	// failure paths read the key off the built request, so one passed via
	// `headers` counts the same as `idempotency_key`.
	retrySafe := agentops.RetrySafe(op.Method, breq.Header.Get("Idempotency-Key"))
	res, err := agentops.DoWith(hc, breq)
	if err != nil {
		// Anything not provably retry-safe defers to the pre-send/mid-flight
		// classification inside executeTransportError.
		return s.executeTransportError(cctx, err, retrySafe), nil
	}
	s.logger.Info(toolName, "target", target, "method", op.Method, "status", res.Status, "execution_id", res.ExecutionID)

	// §3.7 table row 2: a broker denial is a soft error carrying the verbatim
	// agent_directive, non-retryable until access changes.
	if denial := agentops.Classify(res); denial != nil {
		return s.executeDenialError(cctx, denial), nil
	}

	// A broker-origin error that is not a denial (unknown credential id/name,
	// unregistered operation) never reached the upstream, so it is an error
	// result, not a normal one (#1429). Re-sending the same call cannot succeed.
	if coded := agentops.BrokerError(res); coded != nil {
		nextTool := brokerErrorNextTool(coded)
		// The classify core phrases the recovery for the CLI (jentic search /
		// creds list); re-flavor it for this lane's tools so the agent is pointed
		// at a tool it can actually call, matching nextTool. Relayed candidates
		// stay in details["candidates"].
		coded.Actionable = brokerErrorToolHint(nextTool)
		return s.softErrorExtra(cctx, coded, nextTool, map[string]any{"retryable": false}), nil
	}

	// A broker-origin 5xx/429 (circuit open, runner degraded, rate limited,
	// scheme unsupported) is a soft TRANSPORT_ERROR, not a normal result
	// (#1531). The retryable hint is earned from the problem type, and the
	// broker's Retry-After rides along when it sent one.
	if coded := agentops.BrokerTransportError(res, retrySafe); coded != nil {
		return s.executeBrokerTransportError(cctx, coded), nil
	}

	// Everything the broker relayed — 2xx, upstream 4xx/5xx, and the ask-tier
	// 202-held envelope — is a normal result (§3.7 table row 1; §3.4: the held
	// envelope passes through with its directive intact, the model polls with
	// get_execution_result and never re-sends).
	payload := s.executeResultPayload(res)
	if envelope := heldEnvelope(res, payload["body"]); envelope != nil {
		return s.answerHeld(cctx, req, agent, envelope, payload, op.Method, heldPath(op))
	}
	return s.result(cctx, payload), nil
}

// heldPath is the upstream path a held call's consent prompt names.
func heldPath(op *agentops.Operation) string {
	if u, err := url.Parse(op.URL); err == nil && u.Path != "" {
		return u.Path
	}
	if op.Path != "" {
		return op.Path
	}
	return "/"
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
