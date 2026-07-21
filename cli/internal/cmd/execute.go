package cmd

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/apiclient"
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
	pathParams   []string
	queryParams  []string
	headers      []string
	data         string
	dataFile     string
	raw          bool
	json         bool
	brokerScheme string
	brokerHost   string
	revision     string
}

func newExecuteCmd(app *App) *cobra.Command {
	ident := &identityOptions{}
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
			"      (inspect error, e.g. unknown operation_id)",
		Example: "  jentic execute GET:https://rest.coincap.io/v3/markets --json\n" +
			"  jentic execute listPets --query limit=10 --json\n" +
			"  jentic execute GET:/v1/pets/{petId} --path petId=123 --raw\n" +
			"  echo '{\"name\":\"Bob\"}' | jentic execute POST:/v1/users --json",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.executeE(cmd, ident, opts, args[0])
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
	ident.bind(cmd)

	return cmd
}

func (a *App) executeE(cmd *cobra.Command, ident *identityOptions, opts *executeOptions, target string) error {
	baseURL, token, err := a.agentSession(cmd.Context(), ident)
	if err != nil {
		return err
	}

	// Resolve the broker target with precedence defaults < config.yaml < flags,
	// mirroring `jentic run`. Without this, execute always targets the built-in
	// default (broker.jentic.ai); a local install can point at its own broker via
	// ~/.jentic/config.yaml (broker.scheme/host) instead of passing flags on
	// every call.
	fileCfg, err := config.Load(a.Paths)
	if err != nil {
		return err
	}
	flags := cmd.Flags()
	opts.brokerScheme = fileCfg.ResolvedBrokerScheme(opts.brokerScheme, flags.Changed("broker-scheme"))
	opts.brokerHost = fileCfg.ResolvedBrokerHost(opts.brokerHost, flags.Changed("broker-host"))

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
			return fmt.Errorf("invalid --path value %q; expected key=value", kv)
		}
		upstream = strings.ReplaceAll(upstream, "{"+k+"}", url.PathEscape(v))
	}

	// Append query params to the upstream URL (before broker-wrapping).
	if len(opts.queryParams) > 0 {
		qv := url.Values{}
		for _, kv := range opts.queryParams {
			k, v, ok := strings.Cut(kv, "=")
			if !ok {
				return fmt.Errorf("invalid --query value %q; expected key=value", kv)
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
			return fmt.Errorf("invalid --header value %q; expected key=value", kv)
		}
		req.Header.Set(strings.TrimSpace(k), strings.TrimSpace(v))
	}

	// Send phase.
	httpClient := &http.Client{Timeout: 60 * time.Second}
	resp, err := httpClient.Do(req)
	if err != nil {
		if jsonOrPretty(cmd, opts.json) {
			_ = writeJSON(a.Out, map[string]any{
				"error":  err.Error(),
				"status": 0,
			})
		} else {
			fmt.Fprintln(a.Err, theme.Warnf("transport error: %v", err))
		}
		return &exitCodeError{code: 1}
	}
	defer resp.Body.Close()

	return a.executeOutput(cmd, opts, resp)
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

func (a *App) resolveOperation(cmd *cobra.Command, token, baseURL string, opts *executeOptions, target string) (*operationInfo, error) {
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
			} else {
				fmt.Fprintln(a.Err, theme.Warnf("operation %q not found", target))
			}
			return nil, &exitCodeError{code: 2}
		}
		// A non-404 inspect failure (transport, 5xx, malformed) still exits 2,
		// but surface the cause so the agent isn't left with a bare exit code.
		fmt.Fprintln(a.Err, theme.Warnf("resolve %q failed: %v", target, err))
		return nil, &exitCodeError{code: 2}
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

func (a *App) executeOutput(cmd *cobra.Command, opts *executeOptions, resp *http.Response) error {
	if opts.raw {
		if _, err := io.Copy(a.Out, resp.Body); err != nil {
			return err
		}
		// In raw mode we stream the body straight through, so we can't parse a
		// directive out of it. Still signal a broker denial via the exit code.
		if isBrokerDenial(resp) {
			return &exitCodeError{code: 2}
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
		return &exitCodeError{code: 2}
	}
	return nil
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
func (a *App) printAgentDirective(d agentDirective) {
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

func (a *App) executeJSONOutput(resp *http.Response, body []byte) error {
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

func (a *App) executePrettyOutput(resp *http.Response, body []byte) {
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
		var pretty bytes.Buffer
		if err := json.Indent(&pretty, body, "", "  "); err == nil {
			fmt.Fprintln(a.Out, pretty.String())
		} else {
			fmt.Fprintln(a.Out, string(body))
		}
	}
}
