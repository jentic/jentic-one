package api

// mcp_http_gate.go is the platform gate in front of the SDK's stateless
// Streamable HTTP handler — the Go twin of the Python mount's _gate
// (src/jentic_one/mcp/app.py): strict Origin validation (403), bearer auth
// with the pre-auth method whitelist (401 challenge), and the
// session-context merge that carries the daemon's ActiveState + transport
// hook into every request's handler context.

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
)

// mcpPreAuthMethods are the JSON-RPC methods served WITHOUT a credential on
// a token-gated TCP daemon — the same discovery whitelist as the Python
// mount (§3.3): a client can always see the tool surface before
// authenticating. resources/read is deliberately NOT here: reading a skill
// may fetch the hosted copy with the daemon's keys, so it stays behind auth
// on the network transport (the stdio server's pre-auth read is same-uid by
// construction; a TCP caller is not).
var mcpPreAuthMethods = map[string]bool{
	"initialize":                true,
	"notifications/initialized": true,
	"ping":                      true,
	"tools/list":                true,
	"resources/list":            true,
	"resources/templates/list":  true,
}

// mcpMaxPreAuthBody bounds the body buffered for the pre-auth method sniff —
// above the SDK transport's own 4 MiB bound so the sniff never rejects what
// the transport would have accepted.
const mcpMaxPreAuthBody = 8 << 20

// mcpHTTPGate wraps the SDK handler with the platform checks. Order matters
// and mirrors the Python mount: Origin (403) → auth (401) → transport (405
// GET, Accept/Content-Type validation).
type mcpHTTPGate struct {
	next         http.Handler
	sessionCtx   context.Context //nolint:containedctx // the daemon session's values (ActiveState, transport hook) merged under every request context.
	token        []byte          // nil/empty = no token auth (peer-cred or explicit opt-out)
	allowOrigins map[string]bool
	logger       *slog.Logger
}

func (g *mcpHTTPGate) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Values on the daemon's session context (the interceptor's ActiveState,
	// the attribution transport hook) must reach the tool handlers exactly as
	// they do over stdio; cancellation/deadline stay the request's own.
	r = r.WithContext(mergedValueContext{Context: r.Context(), fallback: g.sessionCtx})

	if !g.originAllowed(r) {
		g.logger.Warn("mcp request refused: origin not allowed", "origin", r.Header.Get("Origin"))
		writeJSONStatus(w, http.StatusForbidden, `{"detail":"Origin not allowed"}`)
		return
	}

	if len(g.token) > 0 {
		r2, ok := g.authenticate(w, r)
		if !ok {
			return
		}
		r = r2
	}

	g.next.ServeHTTP(w, r)
}

// authenticate enforces the bearer token, letting an unauthenticated POST
// through only when every JSON-RPC method in its body is on the pre-auth
// whitelist. It returns the (possibly body-rebuffered) request.
func (g *mcpHTTPGate) authenticate(w http.ResponseWriter, r *http.Request) (*http.Request, bool) {
	if cred := bearerCredential(r); cred != "" {
		if subtle.ConstantTimeCompare([]byte(cred), g.token) != 1 {
			g.unauthorized(w)
			return nil, false
		}
		return r, true
	}

	if r.Method != http.MethodPost {
		g.unauthorized(w)
		return nil, false
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, mcpMaxPreAuthBody+1))
	if err != nil || int64(len(body)) > mcpMaxPreAuthBody {
		writeJSONStatus(w, http.StatusRequestEntityTooLarge, `{"detail":"Request body too large"}`)
		return nil, false
	}
	if !allPreAuthMethods(body) {
		g.unauthorized(w)
		return nil, false
	}
	r.Body = io.NopCloser(bytes.NewReader(body))
	r.ContentLength = int64(len(body))
	return r, true
}

func (g *mcpHTTPGate) unauthorized(w http.ResponseWriter) {
	w.Header().Set("WWW-Authenticate", "Bearer")
	writeJSONStatus(w, http.StatusUnauthorized, `{"detail":"Unauthorized"}`)
}

// originAllowed is the strict Origin validation (spec §security, DNS
// rebinding): absent passes (no real MCP client sends one), a present value
// must be a loopback origin or an explicitly allowed one. The request's own
// Host header is never consulted — in the rebinding attack it is
// attacker-controlled (the SDK's localhost Host protection runs additionally
// inside the transport).
func (g *mcpHTTPGate) originAllowed(r *http.Request) bool {
	origin := r.Header.Get("Origin")
	if origin == "" {
		return true
	}
	if g.allowOrigins[strings.ToLower(strings.TrimRight(origin, "/"))] {
		return true
	}
	u, err := url.Parse(origin)
	if err != nil || u.Scheme == "" || u.Host == "" {
		return false
	}
	return hostIsLoopback(u.Hostname())
}

// normalizedOriginSet lower-cases and trims the --allow-origin values so the
// comparison is exact-but-case-insensitive.
func normalizedOriginSet(origins []string) map[string]bool {
	set := make(map[string]bool, len(origins))
	for _, o := range origins {
		if o = strings.ToLower(strings.TrimRight(strings.TrimSpace(o), "/")); o != "" {
			set[o] = true
		}
	}
	return set
}

// bearerCredential extracts the caller's token: Authorization: Bearer first,
// then the X-Jentic-Api-Key spelling — the same order as the platform's REST
// auth and the Python mount.
func bearerCredential(r *http.Request) string {
	if h := r.Header.Get("Authorization"); strings.HasPrefix(h, "Bearer ") {
		return strings.TrimPrefix(h, "Bearer ")
	}
	return r.Header.Get("X-Jentic-Api-Key")
}

// allPreAuthMethods reports whether a JSON-RPC POST body carries only
// whitelisted method names. Unreadable or ambiguous bodies fail closed.
//
// The raw body is forwarded to the SDK transport unchanged, so this check
// must see exactly the method the SDK will dispatch. Each message's method is
// therefore read with the SDK's own decoder (jsonrpc.DecodeMessage), and on
// top of that the body is walked token by token: any message whose JSON-RPC
// envelope members are duplicated or spelled in a different case, or any body
// with trailing data, is refused rather than left to either decoder's
// tie-breaking.
func allPreAuthMethods(body []byte) bool {
	methods, ok := jsonRPCMethods(body)
	if !ok || len(methods) == 0 {
		return false
	}
	for _, m := range methods {
		if !mcpPreAuthMethods[m] {
			return false
		}
	}
	return true
}

// jsonRPCEnvelopeMembers are the JSON-RPC 2.0 member names whose spelling
// must be exact and unique for the pre-auth sniff to trust a message.
var jsonRPCEnvelopeMembers = []string{"jsonrpc", "id", "method", "params", "result", "error"}

// jsonWhitespace is the insignificant whitespace JSON allows between tokens
// (RFC 8259 §2) — narrower than bytes.TrimSpace's Unicode set.
const jsonWhitespace = " \t\r\n"

// jsonRPCMethods returns the method of every message in a single-message or
// batch body, or ok=false when the body is not an unambiguous JSON-RPC
// request (or non-empty array of them) followed by nothing but whitespace.
func jsonRPCMethods(body []byte) ([]string, bool) {
	trimmed := bytes.Trim(body, jsonWhitespace)
	if len(trimmed) == 0 {
		return nil, false
	}
	if trimmed[0] != '[' {
		m, ok := jsonRPCMethod(trimmed)
		if !ok {
			return nil, false
		}
		return []string{m}, true
	}
	var batch []json.RawMessage
	if !decodeSingleValue(trimmed, &batch) || len(batch) == 0 {
		return nil, false
	}
	methods := make([]string, 0, len(batch))
	for _, raw := range batch {
		m, ok := jsonRPCMethod(bytes.Trim(raw, jsonWhitespace))
		if !ok {
			return nil, false
		}
		methods = append(methods, m)
	}
	return methods, true
}

// jsonRPCMethod returns the method of one JSON-RPC request object as the SDK
// decodes it. It refuses anything that is not a single object, has duplicate
// or case-variant envelope members, or that the SDK does not decode as a
// request.
func jsonRPCMethod(msg []byte) (string, bool) {
	if len(msg) == 0 || msg[0] != '{' || !uniqueExactEnvelope(msg) {
		return "", false
	}
	decoded, err := jsonrpc.DecodeMessage(msg)
	if err != nil {
		return "", false
	}
	req, isRequest := decoded.(*jsonrpc.Request)
	if !isRequest {
		return "", false
	}
	return req.Method, true
}

// uniqueExactEnvelope walks one JSON object's top-level members and reports
// whether every JSON-RPC envelope member appears at most once, spelled
// exactly, with nothing after the closing brace. encoding/json folds case
// (strings.EqualFold, which also folds e.g. U+017F to 's') and unescapes
// member names before this comparison, so any spelling a decoder could map
// onto an envelope member is caught here.
func uniqueExactEnvelope(msg []byte) bool {
	dec := json.NewDecoder(bytes.NewReader(msg))
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		return false
	}
	seen := make(map[string]bool, len(jsonRPCEnvelopeMembers))
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return false
		}
		key, isKey := tok.(string)
		if !isKey {
			return false
		}
		var value json.RawMessage
		if err := dec.Decode(&value); err != nil {
			return false
		}
		for _, member := range jsonRPCEnvelopeMembers {
			if !strings.EqualFold(key, member) {
				continue
			}
			if key != member || seen[member] {
				return false
			}
			seen[member] = true
		}
	}
	if tok, err := dec.Token(); err != nil || tok != json.Delim('}') {
		return false
	}
	_, err := dec.Token()
	return err == io.EOF
}

// decodeSingleValue decodes exactly one JSON value from data, refusing
// trailing content after it.
func decodeSingleValue(data []byte, v any) bool {
	dec := json.NewDecoder(bytes.NewReader(data))
	if err := dec.Decode(v); err != nil {
		return false
	}
	_, err := dec.Token()
	return err == io.EOF
}

func writeJSONStatus(w http.ResponseWriter, status int, body string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(body))
}

// mergedValueContext is the request context with the daemon session's values
// underneath: deadlines, cancellation, and request-scoped values come from
// the request; anything unresolved falls back to the session context (the
// ActiveState and transport hook `jentic mcp` installed at startup).
type mergedValueContext struct {
	context.Context
	fallback context.Context //nolint:containedctx // values-only fallback, never consulted for cancellation.
}

func (c mergedValueContext) Value(key any) any {
	if v := c.Context.Value(key); v != nil {
		return v
	}
	return c.fallback.Value(key)
}
