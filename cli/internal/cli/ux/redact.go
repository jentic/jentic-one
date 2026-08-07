// Package ux is the CLI's audience-aware I/O layer (impl/3.1, impl/3.2). Commands
// never call fmt.Println / huh directly — they go through an Audience, so the CLI
// shape-shifts by mode (human vs agent/service-account) and every output byte,
// on BOTH stdout and stderr, passes the fail-closed redaction funnel in this file.
package ux

import (
	"encoding/json"
	"fmt"
	"io"
	"reflect"
	"regexp"
	"strings"
	"sync"
	"time"
)

// --- Layer 1 support: generated SensitiveFields tables -----------------------

// sensitiveFieldsByType maps a generated struct's type NAME (e.g.
// "CredentialResponse") to the json names of its `x-sensitive` properties
// (impl/2.1 §4b). oapi-codegen emits no custom struct tags, so redactTagged can't
// see a `redact:"true"` on generated types; instead it consults this table by
// type name. The composition layer registers the real per-plane tables via
// RegisterSensitiveFields so ux stays decoupled from client/generated (and unit-
// testable). Guarded by a mutex only for registration-at-init safety.
var (
	sensitiveFieldsMu   sync.RWMutex
	sensitiveFieldsType = map[string]map[string]bool{}
)

// RegisterSensitiveFields merges a plane's generated SensitiveFields table
// (type name -> []json field names) into the redaction registry. Idempotent and
// additive; call once per plane at startup.
func RegisterSensitiveFields(table map[string][]string) {
	sensitiveFieldsMu.Lock()
	defer sensitiveFieldsMu.Unlock()
	for typeName, fields := range table {
		set := sensitiveFieldsType[typeName]
		if set == nil {
			set = make(map[string]bool, len(fields))
			sensitiveFieldsType[typeName] = set
		}
		for _, f := range fields {
			set[f] = true
		}
	}
}

func sensitiveFieldsFor(typeName string) map[string]bool {
	sensitiveFieldsMu.RLock()
	defer sensitiveFieldsMu.RUnlock()
	return sensitiveFieldsType[typeName]
}

// --- Layer 2 support: key heuristics -----------------------------------------

// sensitiveKeyExact: keys that ARE a secret name (exact, case-insensitive after
// camelToSnake). Includes the bare spellings of the suffix rules below, because a
// bare name never matches its own "_"-prefixed suffix ("api_key" doesn't end in
// "_api_key"). The impl/0.0 §2 redaction unit tests assert these.
var sensitiveKeyExact = map[string]bool{
	"authorization": true,
	"x-api-key":     true,
	"jwt":           true,
	"assertion":     true,
	"cookie":        true,
	"set-cookie":    true,
	"password":      true,
	"passwd":        true,
	"secret":        true,
	"token":         true,
	"api_key":       true,
	"apikey":        true,
	"private_key":   true,
	"privatekey":    true,
	"signing_key":   true,
	"signingkey":    true,
	"credential":    true,
	"credentials":   true,
}

// sensitiveKeySuffixes: redact only when the (snake-cased) key ENDS with one of
// these. Suffix (not substring) matching is what excludes the false positives
// (next_token, token_count, public_key, key_id, secret_name, password_policy).
var sensitiveKeySuffixes = []string{
	"_token",
	"_secret",
	"_password",
	"_passwd",
	"_api_key",
	"_apikey",
	"_private_key",
	"_privatekey",
	"_signing_key",
	"_credential",
	"_credentials",
}

// sensitiveKeyAllowlist: keys that LOOK sensitive by the suffix/exact rules but are
// known-safe and MUST NOT be redacted, because redacting them corrupts output
// agents depend on. `next_token` is a pagination cursor (ends in `_token`);
// `has_api_key`/`has_credentials` are booleans reporting presence, not the secret
// itself. These win over the exact/suffix matches below (impl/3.1 §1 names them as
// the false positives the scoped design must protect). The arch sweep
// (tests/arch, Test1H) calls the exported IsSensitiveKey rather than mirroring
// this list, so it can never disagree with the runtime redactor (F8-35).
var sensitiveKeyAllowlist = map[string]bool{
	"next_token":      true,
	"has_api_key":     true,
	"has_credentials": true,
	"has_credential":  true,
}

// isSensitiveKey reports whether a (case-insensitive) key should be redacted. The
// key is normalized camelCase -> snake_case first so apiKey/clientSecret match the
// same rules as api_key/client_secret (many JS backends emit camelCase).
func isSensitiveKey(key string) bool {
	k := camelToSnake(key)
	if sensitiveKeyAllowlist[k] {
		return false // known-safe: beats the exact/suffix rules below
	}
	if sensitiveKeyExact[k] {
		return true
	}
	for _, suf := range sensitiveKeySuffixes {
		if strings.HasSuffix(k, suf) {
			return true
		}
	}
	return false
}

// IsSensitiveKey is the exported form of the redactor's secret-shaped-key
// predicate. It exists so the architecture sweep (tests/arch, Test1H) asserts
// against the ACTUAL runtime heuristic — allowlist included — rather than a
// hand-maintained copy that can silently drift (F8-35). It is the single source
// of truth for "does this property name look like a secret".
func IsSensitiveKey(key string) bool { return isSensitiveKey(key) }

// camelToSnake lowercases a key, inserting `_` at word boundaries. ACRONYM-AWARE:
// a run of capitals is one word, so "APIKey" -> "api_key" and "HTTPSProxy" ->
// "https_proxy" (a naive per-capital split gives "a_p_i_key", which matches
// nothing and lets the key escape). Existing separators pass through.
func camelToSnake(key string) string {
	rs := []rune(key)
	var b strings.Builder
	for i, r := range rs {
		if r >= 'A' && r <= 'Z' {
			prevLower := i > 0 && (rs[i-1] >= 'a' && rs[i-1] <= 'z' || rs[i-1] >= '0' && rs[i-1] <= '9')
			nextLower := i+1 < len(rs) && rs[i+1] >= 'a' && rs[i+1] <= 'z'
			prevUpper := i > 0 && rs[i-1] >= 'A' && rs[i-1] <= 'Z'
			if prevLower || (prevUpper && nextLower) {
				b.WriteByte('_')
			}
			r += 'a' - 'A'
		}
		b.WriteRune(r)
	}
	return strings.ReplaceAll(b.String(), "__", "_")
}

// redactValue returns a REDACTED DEEP COPY of an already-unmarshaled generic value
// (map/slice/scalar). It never mutates the input (a command may reuse the struct
// after Render). A depth cap guards adversarially deep/cyclic payloads: beyond
// maxRedactDepth the subtree is replaced wholesale — fail CLOSED.
const maxRedactDepth = 64

func redactValue(v any) any { return redactValueDepth(v, 0) }

func redactValueDepth(v any, depth int) any {
	if depth > maxRedactDepth {
		return "[REDACTED: too deep]"
	}
	switch t := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, val := range t {
			if isSensitiveKey(k) {
				out[k] = "[REDACTED]"
			} else {
				out[k] = redactValueDepth(val, depth+1)
			}
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, val := range t {
			out[i] = redactValueDepth(val, depth+1)
		}
		return out
	default:
		return v
	}
}

// Precompiled byte-backstop patterns (compiled once at init, not per call).
var (
	// reKV matches any JSON "key": "value" pair; the ReplaceAllFunc below re-checks
	// the captured key through isSensitiveKey so the byte pass and the structured
	// pass share ONE definition of sensitive (incl. the allowlist — otherwise the
	// byte pass would clobber a legitimate next_token the structured pass preserved).
	reKV     = regexp.MustCompile(`(?i)"([a-z0-9_\-]+)"(\s*:\s*)"[^"]*"`)
	reBearer = regexp.MustCompile(`(?i)(bearer\s+)[a-zA-Z0-9_\-\.=]+`)
	rePEM    = regexp.MustCompile(`(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----`)
)

// redactSensitive is the NARROW byte-level backstop applied after marshaling (and
// directly to error strings on the stderr path). It catches secrets embedded in
// free-form strings (log lines, error bodies) that carried no sensitive key.
//
// We INTENTIONALLY omit a generic "three base64url segments" JWT matcher: it
// false-positives on dotted version strings and file paths, silently corrupting
// valid output. Keyed JWTs are handled by the structured pass; a bare JWT in a log
// line is rare enough to accept the residual risk over mangling every dotted token.
func redactSensitive(data []byte) []byte {
	out := reKV.ReplaceAllFunc(data, func(m []byte) []byte {
		sub := reKV.FindSubmatch(m)
		if sub == nil || !isSensitiveKey(string(sub[1])) {
			return m // not a sensitive key (honors the allowlist): leave untouched
		}
		return []byte(`"` + string(sub[1]) + `"` + string(sub[2]) + `"[REDACTED]"`)
	})
	out = reBearer.ReplaceAll(out, []byte(`${1}[REDACTED]`))
	out = rePEM.ReplaceAll(out, []byte(`[REDACTED PRIVATE KEY]`))
	return out
}

// redactString is the stderr convenience wrapper (error messages / slog lines):
// the byte backstop over a string. Used by every ReportError path (review M6).
func redactString(s string) string { return string(redactSensitive([]byte(s))) }

// RedactBytes is the exported byte-level backstop for command code that emits a
// raw upstream/API body (e.g. the `jentic api` passthrough, `execute --raw`)
// rather than a marshaled envelope. It applies the SAME free-form-string scrub as
// every other output path so a secret in an API response can't leak to a machine
// parser. It does NOT reshape or re-marshal the payload — the body stays the
// API's own JSON.
func RedactBytes(data []byte) []byte { return redactSensitive(data) }

// safeMarshal / safeMarshalIndent are the single funnel every Render path uses.
// They redact by struct tag (typed reflection), by field name (key heuristics),
// AND by pattern (byte backstop). safeMarshal emits compact JSON (agent output);
// safeMarshalIndent emits 2-space-indented JSON (human output). The indentation is
// fixed by design (no caller needs another width) so it is not a parameter.
func safeMarshal(data any) []byte       { return marshalRedacted(data, false) }
func safeMarshalIndent(data any) []byte { return marshalRedacted(data, true) }

// MarshalForFile is the exported, redacted, indented marshal for commands that
// write an envelope to a file (e.g. `history export -o out.json`) instead of
// through the Audience. It runs the SAME three-layer redaction funnel and
// schema-version stamping as stdout output, so a secret can never leak just
// because the destination is a file. Indented for human readability; still valid
// machine JSON.
func MarshalForFile(data any) []byte { return marshalRedacted(data, true) }

// WriteJSONLine writes ONE compact, redacted JSON document followed by a newline
// to w. It is the streaming primitive for tail-style commands (e.g.
// `events watch`) that emit an unbounded NDJSON sequence rather than a single
// terminal envelope — Render is for one final document, this is for a stream. It
// runs the same redaction funnel as every other output path, so a streamed event
// cannot leak a secret. Errors from the underlying writer are returned so the
// caller can stop the tail (e.g. on a closed pipe).
func WriteJSONLine(w io.Writer, v any) error {
	line := append(safeMarshal(v), '\n')
	_, err := w.Write(line)
	return err
}

// currentSchemaVersion pins the machine-contract envelope shape (13 §2/§6).
const currentSchemaVersion = "1"

func marshalRedacted(data any, indent bool) []byte {
	// Envelope defaulting: Result/Page carry schema_version (13 §2/§6). Call sites
	// may leave it empty; stamp the current version so emitted JSON always has it.
	switch v := data.(type) {
	case Result:
		if v.SchemaVersion == "" {
			v.SchemaVersion = currentSchemaVersion
		}
		data = v
	case Page:
		if v.SchemaVersion == "" {
			v.SchemaVersion = currentSchemaVersion
		}
		data = v
	case Export:
		if v.SchemaVersion == "" {
			v.SchemaVersion = currentSchemaVersion
		}
		data = v
	case Plan:
		if v.SchemaVersion == "" {
			v.SchemaVersion = currentSchemaVersion
		}
		data = v
	}

	// THREE-LAYER FAIL-CLOSED REDACTION (order matters):
	//  1 TYPED (exact): walk the typed value; redact `redact:"true"` fields and
	//    generated fields listed in SensitiveFields. MUST run before any json
	//    round-trip because marshaling to interface{} discards struct tags.
	//  2 KEY heuristics: redactValue redacts untagged sensitive map keys.
	//  3 BYTE backstop: redactSensitive scrubs secrets in free-form strings.
	generic := redactTagged(reflect.ValueOf(data))
	generic = redactValue(generic)

	var out []byte
	var err error
	if indent {
		out, err = json.MarshalIndent(generic, "", "  ")
	} else {
		out, err = json.Marshal(generic)
	}
	if err != nil {
		// FAIL-SAFE (Audience.Render contract): unencodable payload (channel/func) is
		// a programmer bug. Never emit an empty/half-written document — fall back to a
		// guaranteed-encodable envelope of string fields only (no redaction round-trip
		// needed; its contents are static).
		out, _ = json.Marshal(renderFailure{Error: "render_failed", Detail: err.Error()})
	}
	return redactSensitive(out)
}

// renderFailure is the fixed shape emitted when a payload cannot be marshaled. It
// contains only string fields so it can itself never fail to encode.
type renderFailure struct {
	Error  string `json:"error"`
	Detail string `json:"detail"`
}

// redactTagged walks a TYPED reflect.Value and returns a generic (map/slice/scalar)
// copy in which every struct field tagged `redact:"true"` — or, for generated
// structs, every field whose json name is in that type's SensitiveFields entry —
// is replaced with "[REDACTED]". It is the typed equivalent of json.Marshal
// (honors `json` tags, omitempty, `-`) but can SEE the redact tag / consult the
// table because it runs before the value is flattened to interface{}.
func redactTagged(v reflect.Value) any {
	for v.Kind() == reflect.Pointer || v.Kind() == reflect.Interface {
		if v.IsNil() {
			return nil
		}
		v = v.Elem()
	}

	switch v.Kind() {
	case reflect.Struct:
		// time.Time (and any json.Marshaler) must go to the encoder verbatim, not be
		// walked field-by-field, or we corrupt its encoding.
		if v.Type() == timeType || implementsJSONMarshaler(v) {
			return v.Interface()
		}
		out := make(map[string]any)
		t := v.Type()
		tableFields := sensitiveFieldsFor(t.Name()) // generated SensitiveFields, if any
		for i := range t.NumField() {
			f := t.Field(i)
			if f.PkgPath != "" { // unexported: json ignores it, so do we
				continue
			}
			name, omitempty, skip := jsonFieldName(f)
			if skip {
				continue
			}
			fv := v.Field(i)
			if omitempty && fv.IsZero() {
				continue
			}
			if f.Tag.Get("redact") == "true" || (tableFields != nil && tableFields[name]) {
				out[name] = "[REDACTED]"
				continue
			}
			out[name] = redactTagged(fv)
		}
		return out
	case reflect.Map:
		if v.IsNil() {
			return nil
		}
		out := make(map[string]any, v.Len())
		for _, key := range v.MapKeys() {
			out[fmt.Sprintf("%v", key.Interface())] = redactTagged(v.MapIndex(key))
		}
		return out
	case reflect.Slice, reflect.Array:
		if v.Kind() == reflect.Slice && v.IsNil() {
			return nil
		}
		out := make([]any, v.Len())
		for i := range v.Len() {
			out[i] = redactTagged(v.Index(i))
		}
		return out
	default:
		if !v.IsValid() {
			return nil
		}
		return v.Interface()
	}
}

var (
	timeType          = reflect.TypeOf(time.Time{})
	jsonMarshalerType = reflect.TypeOf((*json.Marshaler)(nil)).Elem()
)

func implementsJSONMarshaler(v reflect.Value) bool {
	t := v.Type()
	return t.Implements(jsonMarshalerType) ||
		(v.CanAddr() && reflect.PointerTo(t).Implements(jsonMarshalerType))
}

// jsonFieldName resolves a struct field's effective JSON key, mirroring
// encoding/json: `json:"-"` skips; `json:"name,omitempty"` renames + flags
// omitempty; an empty/absent tag falls back to the Go field name.
func jsonFieldName(f reflect.StructField) (name string, omitempty, skip bool) {
	tag := f.Tag.Get("json")
	if tag == "-" {
		return "", false, true
	}
	parts := strings.Split(tag, ",")
	name = parts[0]
	if name == "" {
		name = f.Name
	}
	for _, opt := range parts[1:] {
		if opt == "omitempty" {
			omitempty = true
		}
	}
	return name, omitempty, false
}
