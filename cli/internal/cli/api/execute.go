package api

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/x/term"
	sdkclient "github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/internal/apiclient"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

var errInspectMissingFields = errors.New("inspect response missing method or url")

// operationInfo holds the resolved HTTP method and target for an operation.
// URL is an absolute upstream URL (from inspect or a METHOD:URL target); Path
// is a broker-relative path (from a METHOD:/path target). Exactly one of URL or
// Path is set.
type operationInfo struct {
	Method string `json:"method"`
	URL    string `json:"url"`
	Path   string `json:"-"`
}

type executeOptions struct {
	pathParams     []string
	queryParams    []string
	headers        []string
	data           string
	dataFile       string
	raw            bool
	json           bool
	brokerScheme   string
	brokerHost     string
	revision       string
	idempotencyKey string
}

func newExecuteCmd(app *app) *cobra.Command {
	opts := &executeOptions{}

	cmd := &cobra.Command{
		Use:   "execute <METHOD:url | METHOD:/path | operation_id>",
		Short: "Execute an operation through the Jentic broker",
		Long: "execute sends an HTTP request through the Jentic broker. The broker\n" +
			"authenticates the caller with their agent token and injects the stored\n" +
			"upstream credential, so the agent token is never sent to the upstream\n" +
			"API directly. The target can be specified in three ways:\n\n" +
			"  1. METHOD:url — a discovered operation's full URL, the same form\n" +
			"     `jentic search`/`jentic inspect` accept (e.g.\n" +
			"     GET:https://rest.coincap.io/v3/markets). Resolved via inspect, then\n" +
			"     routed through the broker.\n" +
			"  2. operation_id — resolve via inspect, then route through the broker.\n" +
			"  3. METHOD:/path — a broker-relative path sent to --broker-host\n" +
			"     verbatim (e.g. GET:/v1/pets); the caller supplies the broker path.\n\n" +
			"Path parameters, query parameters, headers, and a request body can be\n" +
			"supplied via flags.\n\n" +
			"When the broker denies the call (e.g. you are not bound to a toolkit\n" +
			"for the API, or no credential is provisioned), it returns an\n" +
			"agent_directive describing how to recover. execute surfaces that\n" +
			"directive on stderr and exits 2 so a script can branch on the denial.\n\n" +
			"Exit codes:\n" +
			"  0 — broker returned a non-denial HTTP response (incl. 2xx and upstream errors)\n" +
			"  1 — local/transport failure (DNS, TLS, timeout, connection refused)\n" +
			"  2 — denied by the broker (carries an agent_directive) or resolve failure\n" +
			"      (inspect error, e.g. unknown operation_id)\n\n" +
			"Broker target: resolved as built-in default (https://127.0.0.1:8100) <\n" +
			"the active environment's broker_url < --broker-scheme/--broker-host. A\n" +
			"local install serves the broker over plain HTTP, so `jentic register`\n" +
			"seeds broker_url=http://127.0.0.1:8100 for a loopback environment. A TLS\n" +
			"error like \"server gave HTTP response to HTTPS client\" means the target\n" +
			"resolved to https against a local http broker — set the environment's\n" +
			"broker_url (`jentic env add <env> --broker-url http://127.0.0.1:8100\n" +
			"--force`) or override per call with --broker-scheme http.",
		Example: "  jentic execute GET:https://rest.coincap.io/v3/markets --json\n" +
			"  jentic execute listPets --query limit=10 --json\n" +
			"  jentic execute GET:/v1/pets/{petId} --path petId=123 --raw\n" +
			"  echo '{\"name\":\"Bob\"}' | jentic execute POST:/v1/users --json\n" +
			"  # Local broker over http, one-off (usually unnecessary — register seeds broker_url):\n" +
			"  jentic execute listPets --broker-scheme http --broker-host 127.0.0.1:8100",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.executeE(cmd, opts, args[0])
		},
	}

	cmd.Flags().StringArrayVar(&opts.pathParams, "path", nil, "path parameter as key=value (repeatable)")
	cmd.Flags().StringArrayVar(&opts.queryParams, "query", nil, "query parameter as key=value (repeatable)")
	cmd.Flags().StringArrayVar(&opts.headers, "header", nil, "extra header as key=value (repeatable)")
	cmd.Flags().StringVarP(&opts.data, "data", "d", "", "request body JSON (use - for stdin)")
	cmd.Flags().StringVar(&opts.dataFile, "data-file", "", "read request body from this file")
	cmd.Flags().BoolVar(&opts.raw, "raw", false, "stream response body directly to stdout")
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON envelope output")
	cmd.Flags().StringVar(&opts.brokerScheme, "broker-scheme", config.DefaultBrokerScheme, "broker target scheme (http or https)")
	cmd.Flags().StringVar(&opts.brokerHost, "broker-host", config.DefaultBrokerHost, "broker target host as host[:port] (no scheme; use --broker-scheme)")
	cmd.Flags().StringVar(&opts.revision, "revision", "", "pin to a specific revision ID for inspect")
	cmd.Flags().StringVar(&opts.idempotencyKey, "idempotency-key", "", "caller-supplied Idempotency-Key so a retried POST/PUT is de-duplicated by the broker")
	planFlags(cmd)

	return cmd
}

func (a *app) executeE(cmd *cobra.Command, opts *executeOptions, target string) error {
	baseURL, token, err := a.agentSession(cmd.Context())
	if err != nil {
		return err
	}

	// Resolve the broker target with precedence defaults < active environment
	// broker_url < flags. A context whose environment declares broker_url
	// routes through THAT broker — without it, pointing a context at a remote
	// install would still execute against the built-in local default.
	flags := cmd.Flags()
	if st := clictx.ActiveV2(cmd.Context()); st != nil && st.BrokerURL != "" {
		if u, perr := url.Parse(st.BrokerURL); perr == nil && u.Host != "" && u.Scheme != "" {
			if !flags.Changed("broker-scheme") {
				opts.brokerScheme = u.Scheme
			}
			if !flags.Changed("broker-host") {
				opts.brokerHost = u.Host
			}
		}
	}

	// Resolve phase: determine method and path either from METHOD:/path syntax
	// or by inspecting an operation_id.
	opInfo, err := a.resolveOperation(cmd, token, baseURL, opts, target)
	if err != nil {
		return err
	}

	// Build phase: assemble the upstream URL (path params + query), then route
	// it through the broker.
	//
	// All traffic goes through the Jentic broker so the agent authenticates to
	// the broker with its own token (Authorization: Bearer) and the broker
	// injects the stored upstream credential. We never send the agent token to
	// the upstream API directly. The broker is addressed as a catch-all proxy:
	// {brokerScheme}://{brokerHost}/{upstreamURL}  (mirrors the run-proxy rewrite
	// in internal/proxy and the broker's /{upstream_url:path} route).
	//
	//   - opInfo.URL  (absolute upstream URL, from inspect / METHOD:url) is
	//     prefixed with the broker host.
	//   - opInfo.Path (broker-relative METHOD:/path) is sent to the broker host
	//     verbatim — the caller supplied the broker path themselves.
	var upstream string
	brokerRelative := opInfo.URL == ""
	if brokerRelative {
		upstream = opInfo.Path
	} else {
		upstream = opInfo.URL
	}
	for _, kv := range opts.pathParams {
		k, v, ok := strings.Cut(kv, "=")
		if !ok {
			return badFlagKV("--path", kv)
		}
		upstream = strings.ReplaceAll(upstream, "{"+k+"}", url.PathEscape(v))
	}

	// Append query params to the upstream URL (before broker-wrapping).
	if len(opts.queryParams) > 0 {
		qv := url.Values{}
		for _, kv := range opts.queryParams {
			k, v, ok := strings.Cut(kv, "=")
			if !ok {
				return badFlagKV("--query", kv)
			}
			qv.Add(k, v)
		}
		sep := "?"
		if strings.Contains(upstream, "?") {
			sep = "&"
		}
		upstream += sep + qv.Encode()
	}

	var brokerURL string
	if brokerRelative {
		brokerURL = opts.brokerScheme + "://" + opts.brokerHost + upstream
	} else {
		brokerURL = opts.brokerScheme + "://" + opts.brokerHost + "/" + upstream
	}

	// Resolve request body.
	var body io.Reader
	switch {
	case opts.data == "-" || (opts.data == "" && opts.dataFile == "" && !term.IsTerminal(os.Stdin.Fd())):
		data, readErr := io.ReadAll(os.Stdin)
		if readErr != nil {
			return fmt.Errorf("read stdin: %w", readErr)
		}
		if len(data) > 0 {
			body = bytes.NewReader(data)
		}
	case opts.dataFile != "":
		data, readErr := os.ReadFile(opts.dataFile)
		if readErr != nil {
			return fmt.Errorf("read %s: %w", opts.dataFile, readErr)
		}
		body = bytes.NewReader(data)
	case opts.data != "":
		body = strings.NewReader(opts.data)
	}

	// Build HTTP request.
	req, err := http.NewRequestWithContext(cmd.Context(), opInfo.Method, brokerURL, body)
	if err != nil {
		return fmt.Errorf("build request: %w", err)
	}

	// Forward the agent bearer token to the broker.
	// SEC-1: never send the bearer over plaintext to a non-loopback broker.
	if err := auth.RequireSecureURL(brokerURL); err != nil {
		return &ux.CodedError{
			Code:       ux.CodeTransportError,
			Msg:        err.Error(),
			Actionable: "Use an https broker URL, or a loopback (127.0.0.1/localhost) http broker for local installs.",
		}
	}
	req.Header.Set("Authorization", "Bearer "+token)

	// Auto-set Content-Type for body requests.
	hasContentType := false
	for _, kv := range opts.headers {
		k, _, ok := strings.Cut(kv, "=")
		if ok && strings.EqualFold(strings.TrimSpace(k), "content-type") {
			hasContentType = true
			break
		}
	}
	if body != nil && !hasContentType {
		req.Header.Set("Content-Type", "application/json")
	}

	// Merge custom headers.
	for _, kv := range opts.headers {
		k, v, ok := strings.Cut(kv, "=")
		if !ok {
			return badFlagKV("--header", kv)
		}
		req.Header.Set(strings.TrimSpace(k), strings.TrimSpace(v))
	}

	// Correlation (P5.2, F8-6). execute builds a bare request outside the SDK
	// editor chain, so the SDK's session/trace editors never run on this path —
	// we attach the same headers here so a broker-side trace can be joined back
	// to this invocation:
	//   - X-Jentic-Session-Id groups every call of one agent run/batch (the SDK
	//     Config.SessionID, sourced from the active context / $JENTIC_SESSION_ID).
	//   - traceparent is a fresh W3C trace context per execute so distributed
	//     tracing correlates the broker span with this CLI call.
	// A user-provided --header of the same name wins (already set above), so an
	// orchestrator threading its own trace is never clobbered.
	if sid := sessionIDFromContext(cmd); sid != "" && req.Header.Get("X-Jentic-Session-Id") == "" {
		req.Header.Set("X-Jentic-Session-Id", sid)
	}
	if req.Header.Get("traceparent") == "" {
		if tp, ok := newTraceparent(); ok {
			req.Header.Set("traceparent", tp)
		}
	}
	// Idempotency (13 §4, F8-13). A caller-supplied key makes a retried POST/PUT
	// de-duplicated by the broker AND flips the SDK transport's retry-safety for
	// this request (it treats a key-carrying POST as replayable). Without it a
	// plain POST is never retried, so transient 5xx/timeouts surface as failures.
	if opts.idempotencyKey != "" && req.Header.Get("Idempotency-Key") == "" {
		req.Header.Set("Idempotency-Key", opts.idempotencyKey)
	}

	// Dry-run / plan (impl/5.0 §5, F8-15). execute is a mutating (side-effecting)
	// call, so it honors --dry-run/--export-plan: render the fully-resolved
	// request that WOULD be sent — method, broker-wrapped URL, and the correlation
	// headers we just attached — and STOP before firing. The operation name
	// mirrors the effect ("brokerExecute"); the plan-parity test keeps it honest.
	if maybeEmitPlan(cmd, "brokerExecute", executePlanPayload(req)) {
		return nil
	}

	// Send phase. Route through the SDK broker transport (client.BrokerTransport)
	// rather than a bare http.Client so execute inherits the same response policy
	// (401 re-exchange, 429 Retry-After, bounded 5xx/transport backoff — 13 §5)
	// every generated broker call gets, while still composing the broker
	// catch-all URL itself to preserve its exact contract and exit-2 denial
	// handling (plan.md Phase 5 item 1). The 60s ceiling is carried on the base
	// client the policy decorates.
	httpClient := sdkclient.BrokerTransport(sdkclient.Config{
		HTTPClient: &http.Client{Timeout: 60 * time.Second},
	})
	resp, err := httpClient.Do(req)
	if err != nil {
		if jsonOrPretty(cmd, opts.json) {
			_ = writeJSON(a.Out, map[string]any{
				"error":  err.Error(),
				"status": 0,
			})
		}
		coded := &ux.CodedError{
			Code: ux.CodeTransportError,
			Msg:  fmt.Sprintf("transport error: %v", err),
		}
		// UX-4: the classic local papercut is an https default resolving against a
		// plain-http local broker ("server gave HTTP response to HTTPS client").
		// Attach the exact recovery when the signature matches a loopback broker,
		// so the operator isn't left with a bare, unactionable transport error.
		if brokerTLSMismatch(err, brokerURL) {
			coded.Actionable = "The broker resolved to https but is serving http. Set the environment's broker_url " +
				"(`jentic env add <env> --broker-url http://127.0.0.1:8100 --force`), or override this call with " +
				"`--broker-scheme http --broker-host 127.0.0.1:8100`."
		}
		return coded
	}
	defer resp.Body.Close()

	return a.executeOutput(cmd, opts, resp)
}

// brokerTLSMismatch reports whether err is the "https client hit an http server"
// signature against a loopback broker — the local default-scheme papercut UX-4
// makes actionable. Restricted to loopback so we never suggest downgrading a
// remote broker to plaintext.
func brokerTLSMismatch(err error, brokerURL string) bool {
	if err == nil || !strings.Contains(err.Error(), "server gave HTTP response to HTTPS client") {
		return false
	}
	u, perr := url.Parse(brokerURL)
	if perr != nil {
		return false
	}
	return isLoopbackHostname(u.Hostname())
}

// isLoopbackHostname reports whether host is "localhost" or a loopback IP.
func isLoopbackHostname(host string) bool {
	if host == "localhost" {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return false
}

// badFlagKV builds the coded error for a malformed key=value flag (ARCH-4). A
// bad --path/--query/--header value is agent-causable INPUT, so it must surface
// a machine error_code (MISSING_ARGUMENT) with an actionable hint — not a bare
// fmt.Errorf that an agent sees only as an untyped generic-failure string.
func badFlagKV(flag, value string) error {
	return &ux.CodedError{
		Code:       ux.CodeMissingArgument,
		Msg:        fmt.Sprintf("invalid %s value %q; expected key=value", flag, value),
		Actionable: fmt.Sprintf("Pass %s as key=value (e.g. %s id=123).", flag, flag),
	}
}

// executePlanPayload summarizes the resolved outbound request for --dry-run/
// --export-plan without consuming its body reader (the body may still be needed
// if this weren't a dry-run; execute returns immediately after emitting the
// plan, but keeping this read-only avoids any coupling). Sensitive headers are
// redacted by the Audience's plan render path (M6), so Authorization is safe to
// include for completeness.
func executePlanPayload(req *http.Request) map[string]any {
	headers := make(map[string]string, len(req.Header))
	for k := range req.Header {
		headers[k] = req.Header.Get(k)
	}
	return map[string]any{
		"method":  req.Method,
		"url":     req.URL.String(),
		"headers": headers,
	}
}

// sessionIDFromContext returns the resolved X-Jentic-Session-Id for this
// invocation from the ActiveState the root interceptor injected (sourced from
// the active context or $JENTIC_SESSION_ID). Empty when no session id is set —
// correlation is best-effort and never blocks a call.
//
// It falls back to $JENTIC_SESSION_ID directly when the resolved state does not
// carry one: that is the exact source the SDK loader (client/config) reads, so
// the header is present on the execute path whenever the env var is set,
// independent of whether the CLI's ActiveState round-tripped it.
//
// The value is untrusted env input, so it passes through the SDK's
// client.SanitizeSessionID (charset + length clamp, SEC-5) — the same
// normalization the SDK applies on its own session editor.
func sessionIDFromContext(cmd *cobra.Command) string {
	if st := clictx.FromContext(cmd.Context()); st != nil && st.ResolvedState != nil {
		if st.SessionID != "" {
			return sdkclient.SanitizeSessionID(st.SessionID)
		}
	}
	return sdkclient.SanitizeSessionID(os.Getenv("JENTIC_SESSION_ID"))
}

// newTraceparent builds a fresh W3C Trace Context `traceparent` header value
// (version-00): "00-<32 hex trace-id>-<16 hex span-id>-01" with the sampled flag
// set. execute is the root of its own trace (it does not continue an inbound
// one), so a new random trace/span id per call is correct. Returns ok=false only
// if the crypto RNG fails, in which case the header is simply omitted.
func newTraceparent() (string, bool) {
	var traceID [16]byte
	var spanID [8]byte
	if _, err := rand.Read(traceID[:]); err != nil {
		return "", false
	}
	if _, err := rand.Read(spanID[:]); err != nil {
		return "", false
	}
	return fmt.Sprintf("00-%s-%s-01", hex.EncodeToString(traceID[:]), hex.EncodeToString(spanID[:])), true
}

// parseMethodPath checks if target is in METHOD:/path format (a broker-relative
// path, e.g. GET:/v1/pets). Returns the method and path if valid, or empty
// strings if not in this format. A METHOD:URL absolute form (GET:https://…) is
// deliberately NOT matched here — that is handled as an inspectable identifier.
func parseMethodPath(target string) (method, path string) {
	idx := strings.IndexByte(target, ':')
	if idx < 1 || idx >= len(target)-1 || target[idx+1] != '/' {
		return "", ""
	}
	// Reject the scheme separator of an absolute URL (https://…): the char
	// after ':' is '/', but it's followed by another '/'.
	if idx+2 < len(target) && target[idx+2] == '/' {
		return "", ""
	}
	m := strings.ToUpper(target[:idx])
	switch m {
	case "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS":
		return m, target[idx+1:]
	default:
		return "", ""
	}
}

func (a *app) resolveOperation(cmd *cobra.Command, token, baseURL string, opts *executeOptions, target string) (*operationInfo, error) {
	// METHOD:/path → broker-relative direct send (uses --broker-host/scheme).
	if method, path := parseMethodPath(target); method != "" {
		return &operationInfo{Method: method, Path: path}, nil
	}

	// METHOD URL / METHOD:URL (absolute) and opaque operation_id both resolve
	// via inspect, which returns the absolute upstream URL to send to.
	client := apiclient.New(baseURL)
	inspectBody, err := client.Inspect(cmd.Context(), token, target, opts.revision, "json")
	if err != nil {
		var he *apiclient.HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
			if jsonOrPretty(cmd, opts.json) || opts.raw {
				_ = writeJSON(a.Out, map[string]any{
					"error":  fmt.Sprintf("operation %q not found", target),
					"status": 0,
				})
			}
			return nil, &ux.CodedError{
				Code:       ux.CodeResolveFailed,
				Msg:        fmt.Sprintf("operation %q not found", target),
				Actionable: "jentic search \"<what you want to do>\"",
			}
		}
		// A non-404 inspect failure (transport, 5xx, malformed) still exits 2,
		// but surface the cause so the agent isn't left with a bare exit code.
		return nil, &ux.CodedError{
			Code: ux.CodeResolveFailed,
			Msg:  fmt.Sprintf("resolve %q failed: %v", target, err),
		}
	}

	var opInfo operationInfo
	if err := json.Unmarshal(inspectBody, &opInfo); err != nil {
		return nil, fmt.Errorf("decode inspect response: %w", err)
	}
	if opInfo.Method == "" || opInfo.URL == "" {
		return nil, errInspectMissingFields
	}
	return &opInfo, nil
}

func (a *app) executeOutput(cmd *cobra.Command, opts *executeOptions, resp *http.Response) error {
	if opts.raw {
		// Read (bounded by the broker transport's cap) then redact before
		// streaming, so --raw matches the redaction guarantee of the JSON and
		// `jentic api` paths (SEC-2). A secret in an upstream body must not leak
		// just because the caller asked for raw output.
		body, err := io.ReadAll(resp.Body)
		if err != nil {
			return fmt.Errorf("read response: %w", err)
		}
		if _, err := a.Out.Write(ux.RedactBytes(body)); err != nil {
			return err
		}
		if isBrokerDenial(resp) {
			return brokerDeniedErr(resp)
		}
		return nil
	}

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read response: %w", err)
	}

	if jsonOrPretty(cmd, opts.json) {
		if err := a.executeJSONOutput(resp, respBody); err != nil {
			return err
		}
	} else {
		a.executePrettyOutput(resp, respBody)
	}

	// A broker denial (403/409/424/401) means the call did not run; exit 2 so a
	// scripted agent can branch on the denial instead of mistaking the 4xx body
	// for success. The exit code keys off the *status*, not the presence of an
	// agent_directive: some denials (e.g. action_denied from a permission rule)
	// carry no directive, and gating exit 2 on a parsed directive would let those
	// silently exit 0 — the exact regression this surfacing is meant to remove.
	// When a directive *is* present it enriches the message with recovery steps.
	if isBrokerDenial(resp) {
		if directive, ok := parseAgentDirective(resp, respBody); ok {
			a.printAgentDirective(directive)
		}
		return brokerDeniedErr(resp)
	}
	return nil
}

// brokerDeniedErr is the typed denial every broker-denial exit shares (AGT-6):
// BROKER_DENIED (exit 2) with the denying HTTP status in Details, so the agent
// envelope and the exit code come from the same table. The response body /
// directive recovery text is printed by the caller before this is returned.
func brokerDeniedErr(resp *http.Response) *ux.CodedError {
	return &ux.CodedError{
		Code:    ux.CodeBrokerDenied,
		Msg:     "the broker denied this call before it reached the upstream API",
		Details: map[string]any{"http_status": resp.StatusCode},
	}
}

// isBrokerDenial reports whether a response is one the broker itself emitted to
// deny a call the agent can recover from: missing toolkit binding → 403,
// ambiguous toolkit → 409, credential needs reconnect → 401, no credential →
// 424. Each carries an agent_directive (see broker/web/errors.STATUS_BY_ERROR).
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
func isBrokerDenial(resp *http.Response) bool {
	if resp == nil {
		return false
	}
	if errorOrigin(resp) == errorOriginUpstream {
		return false
	}
	switch resp.StatusCode {
	case http.StatusUnauthorized,
		http.StatusForbidden,
		http.StatusConflict,
		http.StatusFailedDependency:
		return true
	default:
		return false
	}
}

// errorOriginUpstream is the Jentic-Error-Origin value the broker stamps on a
// mirrored upstream response (broker ErrorOrigin.UPSTREAM). The matching header
// name mirrors broker/core/headers.JenticHeader.ERROR_ORIGIN.
const errorOriginUpstream = "upstream"

func errorOrigin(resp *http.Response) string {
	if resp == nil {
		return ""
	}
	return strings.ToLower(strings.TrimSpace(resp.Header.Get("Jentic-Error-Origin")))
}

// agentDirective mirrors the broker's problem+json agent_directive extension
// member (broker/core/exceptions.py AgentDirective). The strategy vocabulary
// below mirrors broker AgentStrategy; until the contract is a shared OpenAPI
// schema, the Python test test_directive_factories_emit_known_strategies and
// this list must be kept in lock-step (review P1-1).
type agentDirective struct {
	Strategy    string         `json:"strategy"`
	Parameters  map[string]any `json:"parameters"`
	Instruction string         `json:"human_readable_instruction"`
}

// Recovery strategies the broker may emit in an agent_directive (mirrors
// broker AgentStrategy): wait, retry, modify_headers, prompt_human,
// switch_toolkit, fatal. Only the ones this CLI branches on are named here.
const (
	directiveWait  = "wait"
	directiveRetry = "retry"
)

// parseAgentDirective extracts an agent_directive from a denial response body.
// It only treats recoverable broker-denial responses as directives so a normal
// 4xx (including an upstream pass-through with an incidental
// "agent_directive"-shaped body) can't trip the exit code.
func parseAgentDirective(resp *http.Response, body []byte) (agentDirective, bool) {
	if !isBrokerDenial(resp) {
		return agentDirective{}, false
	}
	var envelope struct {
		Directive *agentDirective `json:"agent_directive"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil || envelope.Directive == nil {
		return agentDirective{}, false
	}
	return *envelope.Directive, true
}

// printAgentDirective renders a recovery directive to stderr, lifting the
// suggested_command / provisioning_url out of parameters so the agent (or its
// operator) sees the exact next action without parsing JSON.
func (a *app) printAgentDirective(d agentDirective) {
	fmt.Fprintln(a.Err, theme.Warn.Render("Denied — recovery required:"))
	if d.Instruction != "" {
		fmt.Fprintln(a.Err, "  "+d.Instruction)
	}
	if cmd, ok := d.Parameters["suggested_command"].(string); ok && cmd != "" {
		fmt.Fprintln(a.Err, "  run: "+theme.Accent.Render(cmd))
	}
	if u, ok := d.Parameters["provisioning_url"].(string); ok && u != "" {
		fmt.Fprintln(a.Err, "  open: "+theme.Accent.Render(u))
	}
	if cands, ok := d.Parameters["candidates"].([]any); ok && len(cands) > 0 {
		parts := make([]string, 0, len(cands))
		for _, c := range cands {
			if s, isStr := c.(string); isStr {
				parts = append(parts, s)
			}
		}
		if len(parts) > 0 {
			fmt.Fprintln(a.Err, "  candidates: "+theme.Accent.Render(strings.Join(parts, ", ")))
		}
	}
	// A wait/retry directive carries a backoff hint the agent should honor before
	// retrying; surface it so the recovery loop doesn't hot-spin.
	if d.Strategy == directiveWait || d.Strategy == directiveRetry {
		if secs, ok := d.Parameters["retry_after_seconds"]; ok {
			fmt.Fprintf(a.Err, "  retry after: %v\n", secs)
		}
	}
}

func (a *app) executeJSONOutput(resp *http.Response, body []byte) error {
	headers := make(map[string]string)
	for k := range resp.Header {
		headers[k] = resp.Header.Get(k)
	}

	var parsedBody any
	if err := json.Unmarshal(body, &parsedBody); err != nil {
		parsedBody = string(body)
	}

	envelope := map[string]any{
		"status":  resp.StatusCode,
		"headers": headers,
		"body":    parsedBody,
	}
	if execID := resp.Header.Get("Jentic-Execution-Id"); execID != "" {
		envelope["execution_id"] = execID
	}

	return writeJSON(a.Out, envelope)
}

func (a *app) executePrettyOutput(resp *http.Response, body []byte) {
	statusLine := fmt.Sprintf("HTTP %d %s", resp.StatusCode, http.StatusText(resp.StatusCode))
	switch {
	case resp.StatusCode >= 200 && resp.StatusCode < 300:
		fmt.Fprintln(a.Out, theme.Success.Render(statusLine))
	case resp.StatusCode >= 400:
		fmt.Fprintln(a.Out, theme.Warn.Render(statusLine))
	default:
		fmt.Fprintln(a.Out, statusLine)
	}

	for k, vs := range resp.Header {
		if strings.HasPrefix(k, "Jentic-") {
			fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf("  %s: %s", k, strings.Join(vs, ", "))))
		}
	}

	fmt.Fprintln(a.Out)
	if len(body) > 0 {
		// Redact the upstream body before display (SEC-2): the pretty path is
		// human-facing but can still carry secrets echoed by an upstream API.
		var pretty bytes.Buffer
		if err := json.Indent(&pretty, body, "", "  "); err == nil {
			_, _ = a.Out.Write(ux.RedactBytes(pretty.Bytes()))
		} else {
			_, _ = a.Out.Write(ux.RedactBytes(body))
		}
		fmt.Fprintln(a.Out)
	}
}
