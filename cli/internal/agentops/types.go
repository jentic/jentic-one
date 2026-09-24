// Package agentops is the UX-free execute/inspect core shared by the `jentic`
// command tree and future embedders (the local MCP server, phase 1 of the
// local-MCP plan). It owns the resolve → build → send → classify lifecycle of a
// brokered call, lifted out of the cobra layer.
//
// Dependency rules (phase-0 §0.2, deliberate and load-bearing):
//   - agentops receives token strings and a RESOLVED broker target. Token
//     acquisition (sessions, credential resolution) and broker-target
//     precedence (flag reading, environment broker_url, SEC-21 pinning,
//     fail-closed guards) stay caller-side.
//   - Control-plane resolve/inspect calls go through the narrow Inspector
//     seam, never the whole generated client surface.
//   - No io.Writer output, no cobra, no exit-code mapping, no TTY detection,
//     no stdin. Errors are *ux.CodedError values carrying the closed-enum
//     machine contract; the exit taxonomy stays in ux/contract.go.
package agentops

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// Inspector is the narrow control-plane seam agentops needs: the raw inspect
// call returning the FULL inspect document (JSON/Markdown/OpenAPI negotiated by
// format), not just the {method,url} projection ResolveOperation reads from it.
// The raw form is deliberate: the MCP inspect_operation tool needs the whole
// body, and `jentic inspect` delegates through the same seam. The api layer's
// generated-SDK wrapper satisfies it; token acquisition happens behind the
// implementation, never here.
type Inspector interface {
	// Inspect resolves an operation (opaque id or METHOD:url) to its full
	// structural document. format is one of "json", "markdown", or "openapi";
	// revision optionally pins a revision ID. A non-2xx backend response
	// surfaces as *HTTPError.
	Inspect(ctx context.Context, target, revision, format string) ([]byte, error)
}

// Operation holds the resolved HTTP method and target for an operation. URL is
// an absolute upstream URL (from inspect or a METHOD:URL target); Path is a
// broker-relative path (from a METHOD:/path target). Exactly one of URL or
// Path is set.
type Operation struct {
	Method string `json:"method"`
	URL    string `json:"url"`
	Path   string `json:"-"`
}

// KV is one parsed key=value pair (path parameter, query parameter, or
// header). The caller owns the flag-syntax parsing — under MCP the parameters
// arrive structured, so the key=value CLI surface never reaches this package.
// A slice (not a map) preserves the caller's ordering semantics exactly: path
// substitution applies pairs in order (a repeated key is a no-op after the
// first replaced every placeholder), and repeated query keys accumulate.
type KV struct {
	Key   string
	Value string
}

// ExecuteRequest is the UX-free input to BuildRequest/Execute: a resolved
// operation, the request surface (params/headers/body), the RESOLVED broker
// target, and the caller's credentials/correlation strings. No flag state, no
// session objects.
type ExecuteRequest struct {
	// Method is the HTTP method of the resolved operation.
	Method string
	// URL is the absolute upstream URL to route through the broker. Exactly one
	// of URL or Path is set (mirroring Operation).
	URL string
	// Path is a broker-relative path sent to the broker host verbatim.
	Path string

	// PathParams substitute {key} placeholders in the upstream target
	// (percent-escaped, in order).
	PathParams []KV
	// QueryParams are appended to the upstream target before broker-wrapping.
	QueryParams []KV
	// Headers are extra request headers, applied after the automatic ones so a
	// caller-supplied header always wins.
	Headers []KV
	// Body is the request body reader, or nil for a bodyless request (nil vs a
	// reader — even an empty one — gates the automatic Content-Type, matching
	// the historical behavior). The stdin fallback and --data/--data-file
	// reading stay caller-side (under stdio MCP, stdin is the JSON-RPC wire).
	Body io.Reader

	// BrokerScheme/BrokerHost are the RESOLVED broker target — precedence
	// (defaults < environment broker_url < flags) has already run caller-side.
	BrokerScheme string
	BrokerHost   string

	// Token is the agent bearer forwarded to the broker (never to the upstream).
	Token string
	// SessionID, when set, is stamped as X-Jentic-Session-Id (already sanitized
	// by the caller). Correlation is best-effort and never blocks a call.
	SessionID string
	// IdempotencyKey, when set, is stamped as Idempotency-Key so a retried
	// POST/PUT is de-duplicated by the broker and treated as replayable by the
	// SDK transport's retry policy.
	IdempotencyKey string
}

// ExecuteResult is the UX-free outcome of a brokered call: the relayed status,
// the full response headers, the bounded-read body, and the broker's execution
// id. Rendering (envelope vs pretty vs raw) and exit-code mapping happen
// caller-side; classification lives in classify.go.
type ExecuteResult struct {
	Status      int
	Headers     http.Header
	Body        []byte
	ExecutionID string
}

// Envelope projects the result onto the shared success envelope
// (ux.ExecuteEnvelope, schema version stamped): single-value headers, the body
// parsed as JSON when it decodes (else the raw string).
func (r *ExecuteResult) Envelope() ux.ExecuteEnvelope {
	headers := make(map[string]string, len(r.Headers))
	for k := range r.Headers {
		headers[k] = r.Headers.Get(k)
	}
	var parsedBody any
	if err := json.Unmarshal(r.Body, &parsedBody); err != nil {
		parsedBody = string(r.Body)
	}
	return ux.NewExecuteEnvelope(r.Status, headers, parsedBody, r.ExecutionID)
}

// Denial is the classification of a broker-denied call (classify.go): the
// denying HTTP status plus the recovery directive when the body carried one.
type Denial struct {
	Status int
	// Directive is the parsed agent_directive, or nil when the denial carried
	// none (the caller synthesizes a status-keyed recovery hint instead).
	Directive *ux.Directive
	// DirectiveRaw is the verbatim agent_directive JSON sub-object the denial
	// carried (nil when Directive is nil). Relaying surfaces (the MCP payload)
	// use it so unknown future broker fields survive intact; the CLI's styled
	// renderer keeps reading the typed Directive projection.
	DirectiveRaw json.RawMessage
}

// Err returns the typed denial every broker-denial exit shares (AGT-6):
// BROKER_DENIED with the denying HTTP status in Details, so the agent envelope
// and the exit code come from the same table. The recovery directive is
// rendered by the caller before this is returned.
func (d *Denial) Err() *ux.CodedError {
	return &ux.CodedError{
		Code:    ux.CodeBrokerDenied,
		Msg:     "the broker denied this call before it reached the upstream API",
		Details: map[string]any{"http_status": d.Status},
	}
}

// HTTPError is a non-2xx control-plane response from the Inspector seam. It
// mirrors the shape the hand-written httpx.HTTPError offered — StatusCode plus
// the raw problem-details body — so error-mapping logic ports unchanged. The
// api command tree aliases it (api.HTTPError) so its call sites are untouched.
type HTTPError struct {
	StatusCode int
	Body       string
}

func (e *HTTPError) Error() string {
	return fmt.Sprintf("http %d: %s", e.StatusCode, e.Detail())
}

// Detail extracts an RFC 9457 problem-details message, preferring the most
// specific key (matching the old httpx.HTTPError.Detail order exactly).
func (e *HTTPError) Detail() string {
	p := e.Fields()
	for _, k := range []string{"detail", "title", "error_description", "error"} {
		if v, ok := p[k].(string); ok && v != "" {
			return v
		}
	}
	return e.Body
}

// Fields decodes the problem-details body into a map so callers can read
// extension members. Returns an empty map when the body is not a JSON object.
func (e *HTTPError) Fields() map[string]any {
	var p map[string]any
	if json.Unmarshal([]byte(e.Body), &p) == nil {
		return p
	}
	return map[string]any{}
}

// ParseKVs parses raw key=value strings (the CLI's --path/--query/--header
// value syntax) into KV pairs. Both halves are kept VERBATIM — the header path
// trims whitespace at application time (BuildRequest), path/query never did —
// so the split is pure syntax. badKV builds the caller's typed error for a
// malformed pair (the CLI keeps its coded MISSING_ARGUMENT with the flag name;
// other callers supply their own), so input-shape errors stay caller-owned.
func ParseKVs(raw []string, badKV func(value string) error) ([]KV, error) {
	out := make([]KV, 0, len(raw))
	for _, kv := range raw {
		k, v, ok := strings.Cut(kv, "=")
		if !ok {
			return nil, badKV(kv)
		}
		out = append(out, KV{Key: k, Value: v})
	}
	return out, nil
}
