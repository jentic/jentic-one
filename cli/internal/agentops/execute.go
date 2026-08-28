package agentops

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	sdkclient "github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// ErrInspectMissingFields reports an inspect document that resolved but carries
// no usable method/url pair.
var ErrInspectMissingFields = errors.New("inspect response missing method or url")

// ParseMethodPath checks if target is in METHOD:/path format (a broker-relative
// path, e.g. GET:/v1/pets). Returns the method and path if valid, or empty
// strings if not in this format. A METHOD:URL absolute form (GET:https://…) is
// deliberately NOT matched here — that is handled as an inspectable identifier.
func ParseMethodPath(target string) (method, path string) {
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
	case http.MethodGet, http.MethodPost, http.MethodPut, http.MethodPatch,
		http.MethodDelete, http.MethodHead, http.MethodOptions:
		return m, target[idx+1:]
	default:
		return "", ""
	}
}

// ResolveOperation resolves an execute target to its method and destination:
// METHOD:/path short-circuits to a broker-relative Operation with no network
// call; METHOD:URL (absolute) and opaque operation_ids resolve via the
// Inspector, which returns the absolute upstream URL to send to. Failures are
// coded RESOLVE_FAILED so the caller's exit taxonomy maps them to exit 2.
func ResolveOperation(ctx context.Context, ins Inspector, target, revision string) (*Operation, error) {
	if method, path := ParseMethodPath(target); method != "" {
		return &Operation{Method: method, Path: path}, nil
	}

	inspectBody, err := ins.Inspect(ctx, target, revision, "json")
	if err != nil {
		var he *HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
			// AGT-23: single-source the failure on stderr (the coded envelope
			// below). No unversioned {error,status:0} on stdout.
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

	var op Operation
	if err := json.Unmarshal(inspectBody, &op); err != nil {
		return nil, fmt.Errorf("decode inspect response: %w", err)
	}
	if op.Method == "" || op.URL == "" {
		return nil, ErrInspectMissingFields
	}
	return &op, nil
}

// BuildRequest assembles the outbound broker request from a resolved
// ExecuteRequest: path-parameter substitution, query append, the broker
// catch-all URL, the SEC-1 secure-transport guard, bearer/correlation/
// idempotency headers. It does NOT send — the CLI's --dry-run/--export-plan
// hook renders the built request and stops before firing.
//
// All traffic goes through the Jentic broker so the agent authenticates to
// the broker with its own token (Authorization: Bearer) and the broker
// injects the stored upstream credential. We never send the agent token to
// the upstream API directly. The broker is addressed as a catch-all proxy:
// {brokerScheme}://{brokerHost}/{upstreamURL} (mirroring the broker's
// /{upstream_url:path} route).
//
//   - r.URL (absolute upstream URL, from inspect / METHOD:url) is prefixed
//     with the broker host.
//   - r.Path (broker-relative METHOD:/path) is sent to the broker host
//     verbatim — the caller supplied the broker path themselves.
func BuildRequest(ctx context.Context, r ExecuteRequest) (*http.Request, error) {
	var upstream string
	brokerRelative := r.URL == ""
	if brokerRelative {
		upstream = r.Path
	} else {
		upstream = r.URL
	}
	for _, kv := range r.PathParams {
		upstream = strings.ReplaceAll(upstream, "{"+kv.Key+"}", url.PathEscape(kv.Value))
	}

	// Append query params to the upstream URL (before broker-wrapping).
	if len(r.QueryParams) > 0 {
		qv := url.Values{}
		for _, kv := range r.QueryParams {
			qv.Add(kv.Key, kv.Value)
		}
		sep := "?"
		if strings.Contains(upstream, "?") {
			sep = "&"
		}
		upstream += sep + qv.Encode()
	}

	var brokerURL string
	if brokerRelative {
		brokerURL = r.BrokerScheme + "://" + r.BrokerHost + upstream
	} else {
		brokerURL = r.BrokerScheme + "://" + r.BrokerHost + "/" + upstream
	}

	req, err := http.NewRequestWithContext(ctx, r.Method, brokerURL, r.Body)
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}

	// Forward the agent bearer token to the broker.
	// SEC-1: never send the bearer over plaintext to a non-loopback broker.
	if err := auth.RequireSecureURL(brokerURL); err != nil {
		return nil, &ux.CodedError{
			Code:       ux.CodeTransportError,
			Msg:        err.Error(),
			Actionable: "Use an https broker URL, or a loopback (127.0.0.1/localhost) http broker for local installs.",
		}
	}
	req.Header.Set("Authorization", "Bearer "+r.Token)

	// Auto-set Content-Type for body requests.
	hasContentType := false
	for _, kv := range r.Headers {
		if strings.EqualFold(strings.TrimSpace(kv.Key), "content-type") {
			hasContentType = true
			break
		}
	}
	if r.Body != nil && !hasContentType {
		req.Header.Set("Content-Type", "application/json")
	}

	// Merge custom headers.
	for _, kv := range r.Headers {
		req.Header.Set(strings.TrimSpace(kv.Key), strings.TrimSpace(kv.Value))
	}

	// Correlation (P5.2, F8-6). execute builds a bare request outside the SDK
	// editor chain, so the SDK's session/trace editors never run on this path —
	// we attach the same headers here so a broker-side trace can be joined back
	// to this invocation:
	//   - X-Jentic-Session-Id groups every call of one agent run/batch (the
	//     caller resolves and sanitizes it from the active context /
	//     $JENTIC_SESSION_ID).
	//   - traceparent is a fresh W3C trace context per execute so distributed
	//     tracing correlates the broker span with this CLI call.
	// A caller-provided header of the same name wins (already set above), so an
	// orchestrator threading its own trace is never clobbered.
	if r.SessionID != "" && req.Header.Get("X-Jentic-Session-Id") == "" {
		req.Header.Set("X-Jentic-Session-Id", r.SessionID)
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
	if r.IdempotencyKey != "" && req.Header.Get("Idempotency-Key") == "" {
		req.Header.Set("Idempotency-Key", r.IdempotencyKey)
	}

	return req, nil
}

// Do sends a built broker request through the SDK broker transport
// (client.BrokerTransport) rather than a bare http.Client, so execute inherits
// the same response policy (401 re-exchange, 429 Retry-After, bounded
// 5xx/transport backoff — 13 §5) every generated broker call gets, while still
// composing the broker catch-all URL itself to preserve its exact contract and
// exit-2 denial handling. The 60s ceiling is carried on the base client the
// policy decorates. The response body is read bounded (MaxBodyBytes) into the
// returned ExecuteResult.
func Do(req *http.Request) (*ExecuteResult, error) {
	httpClient := sdkclient.BrokerTransport(sdkclient.Config{
		HTTPClient: &http.Client{Timeout: 60 * time.Second},
	})
	// G704 (SSRF) is intentional, not a finding: dialing a caller-chosen broker
	// target IS this function's contract. The URL is guarded upstream —
	// BuildRequest enforces SEC-1 (no bearer over plaintext to a non-loopback
	// host) and the CLI layer enforces the SEC-21 machine-mode broker pin and
	// the remote-CP/loopback fail-closed guard before the request reaches here.
	resp, err := httpClient.Do(req) //nolint:gosec // G704: see above — brokered execute exists to call the resolved target.
	if err != nil {
		// AGT-23: a failure is single-sourced on stderr as the coded envelope
		// below. We deliberately do NOT also write an unversioned
		// {error,status:0} to stdout — that double-signalled the same failure on
		// two streams, and an agent parsing stdout would see a bogus "response".
		coded := &ux.CodedError{
			Code: ux.CodeTransportError,
			Msg:  fmt.Sprintf("transport error: %v", err),
		}
		// UX-4: the classic local papercut is an https default resolving against a
		// plain-http local broker ("server gave HTTP response to HTTPS client").
		// Attach the exact recovery when the signature matches a loopback broker,
		// so the operator isn't left with a bare, unactionable transport error.
		if brokerTLSMismatch(err, req.URL.String()) {
			coded.Actionable = "The broker resolved to https but is serving http. Set the environment's broker_url " +
				"(`jentic env add <env> --broker-url http://127.0.0.1:8100 --force`), or override this call with " +
				"`--broker-scheme http --broker-host 127.0.0.1:8100`."
		}
		return nil, coded
	}
	defer resp.Body.Close()

	body, err := sdkclient.ReadAllBounded(resp.Body, sdkclient.MaxBodyBytes)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}
	return &ExecuteResult{
		Status:      resp.StatusCode,
		Headers:     resp.Header,
		Body:        body,
		ExecutionID: resp.Header.Get("Jentic-Execution-Id"),
	}, nil
}

// Execute is the one-call UX-free core (ExecuteRequest → ExecuteResult):
// BuildRequest + Do. The CLI drives the two phases itself so its --dry-run/
// --export-plan hook can render the built request and stop; embedders with no
// plan surface (the MCP handler) call this.
func Execute(ctx context.Context, r ExecuteRequest) (*ExecuteResult, error) {
	req, err := BuildRequest(ctx, r)
	if err != nil {
		return nil, err
	}
	return Do(req)
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
	return IsLoopbackHost(u.Hostname())
}

// IsLoopbackHost reports whether host is "localhost" or a loopback IP. It is
// the single loopback classifier the execute path and the CLI's broker-target
// guards share (the auth layer keeps its own private copy by design).
func IsLoopbackHost(host string) bool {
	if host == "localhost" {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return false
}
