package ux

import (
	"bytes"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func TestCamelToSnake(t *testing.T) {
	cases := map[string]string{
		"apiKey":        "api_key",
		"APIKey":        "api_key",
		"HTTPSProxy":    "https_proxy",
		"clientSecret":  "client_secret",
		"refreshToken":  "refresh_token",
		"already_snake": "already_snake",
		"PublicKey":     "public_key",
	}
	for in, want := range cases {
		if got := camelToSnake(in); got != want {
			t.Errorf("camelToSnake(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestIsSensitiveKey(t *testing.T) {
	sensitive := []string{
		"authorization", "password", "secret", "token", "api_key", "apiKey",
		"private_key", "client_secret", "access_token", "refresh_token",
		"webhook_secret", "db_password", "clientSecret", "credentials",
	}
	notSensitive := []string{
		"next_token", "token_count", "public_key", "key_id", "secret_name",
		"password_policy", "name", "id", "url", "count", "has_api_key",
	}
	for _, k := range sensitive {
		if !isSensitiveKey(k) {
			t.Errorf("isSensitiveKey(%q) = false, want true", k)
		}
	}
	for _, k := range notSensitive {
		if isSensitiveKey(k) {
			t.Errorf("isSensitiveKey(%q) = true, want false (false-positive)", k)
		}
	}
}

// Layer 1 — typed redact tag.
type taggedSecret struct {
	Name     string `json:"name"`
	APIKey   string `json:"api_key" redact:"true"`
	Password string `json:"password"` // caught by key heuristic (layer 2 on generic), too
}

func TestSafeMarshal_TypedTagRedaction(t *testing.T) {
	out := safeMarshal(taggedSecret{Name: "svc", APIKey: "sk-live-xyz", Password: "hunter2"})
	s := string(out)
	if strings.Contains(s, "sk-live-xyz") {
		t.Errorf("redact:\"true\" field leaked: %s", s)
	}
	if strings.Contains(s, "hunter2") {
		t.Errorf("password value leaked: %s", s)
	}
	if !strings.Contains(s, `"name":"svc"`) {
		t.Errorf("non-sensitive field was dropped/mangled: %s", s)
	}
}

// Layer 1 via generated SensitiveFields registry (no struct tag).
type genLike struct {
	Token string `json:"token_value"`
	Name  string `json:"name"`
}

func TestSafeMarshal_RegisteredSensitiveFields(t *testing.T) {
	RegisterSensitiveFields(reflect.TypeOf(genLike{}).PkgPath(), map[string][]string{"genLike": {"token_value"}})
	out := string(safeMarshal(genLike{Token: "supersecret", Name: "ok"}))
	if strings.Contains(out, "supersecret") {
		t.Errorf("registered sensitive field leaked: %s", out)
	}
	if !strings.Contains(out, `"name":"ok"`) {
		t.Errorf("non-sensitive field dropped: %s", out)
	}
}

// Layer 2 — key heuristic on generic maps.
func TestSafeMarshal_KeyHeuristic(t *testing.T) {
	data := map[string]any{
		"access_token": "at-123",
		"public_key":   "pub-ok",
		"nested": map[string]any{
			"client_secret": "cs-456",
			"next_token":    "cursor-ok",
		},
	}
	out := string(safeMarshal(data))
	if strings.Contains(out, "at-123") || strings.Contains(out, "cs-456") {
		t.Errorf("sensitive map keys leaked: %s", out)
	}
	if !strings.Contains(out, "pub-ok") || !strings.Contains(out, "cursor-ok") {
		t.Errorf("false-positive: legitimate keys were redacted: %s", out)
	}
}

// Layer 3 — byte backstop for secrets in free-form strings.
func TestRedactSensitive_ByteBackstop(t *testing.T) {
	in := []byte(`{"log":"Authorization: Bearer abc.def.ghi failed"}`)
	out := string(redactSensitive(in))
	if strings.Contains(out, "abc.def.ghi") {
		t.Errorf("bearer token in free-form string leaked: %s", out)
	}

	pem := []byte("prefix -----BEGIN PRIVATE KEY-----\nMIIBVg==\n-----END PRIVATE KEY----- suffix")
	if strings.Contains(string(redactSensitive(pem)), "MIIBVg==") {
		t.Errorf("PEM private key leaked")
	}
}

func TestRedactValue_DoesNotMutateInput(t *testing.T) {
	in := map[string]any{"api_key": "secret", "name": "keep"}
	_ = safeMarshal(in)
	if in["api_key"] != "secret" {
		t.Errorf("redaction mutated caller's data: %v", in)
	}
}

func TestRedactValue_DepthCap(t *testing.T) {
	// Build a map nested deeper than maxRedactDepth.
	var node any = "leaf"
	for range maxRedactDepth + 5 {
		node = map[string]any{"child": node}
	}
	out := string(safeMarshal(node))
	if !strings.Contains(out, "too deep") {
		t.Errorf("depth cap not applied: %s", out[:min(200, len(out))])
	}
}

func TestSafeMarshal_FailSafeOnUnencodable(t *testing.T) {
	// A channel cannot be JSON-encoded; the funnel must emit the render_failed
	// envelope, never an empty/half document.
	out := string(safeMarshal(map[string]any{"bad": make(chan int)}))
	if !strings.Contains(out, "render_failed") {
		t.Errorf("expected render_failed fallback, got: %s", out)
	}
	// It must be valid JSON.
	if !json.Valid([]byte(out)) {
		t.Errorf("fail-safe output is not valid JSON: %s", out)
	}
}

func TestMarshalRedacted_StampsSchemaVersion(t *testing.T) {
	out := string(safeMarshal(Result{Status: StatusCreated, Resource: "environment", Name: "local"}))
	if !strings.Contains(out, `"schema_version":"1"`) {
		t.Errorf("Result schema_version not stamped: %s", out)
	}
	pout := string(safeMarshal(Page{Items: []int{1, 2}, NextToken: "c1"}))
	if !strings.Contains(pout, `"schema_version":"1"`) {
		t.Errorf("Page schema_version not stamped: %s", pout)
	}
}

func TestSafeMarshalIndent_IsIndented(t *testing.T) {
	out := safeMarshalIndent(map[string]any{"a": 1})
	if !bytes.Contains(out, []byte("\n")) {
		t.Errorf("indent output not multi-line: %s", out)
	}
}

// TestRegisteredSensitiveNameRedactedInBytesAndValue pins GEN-21: a field a plane
// declared `x-sensitive` must be redacted on the RAW passthrough paths (byte
// backstop + untyped map value) even when its name does NOT trip the generic
// key heuristics — because the raw path has no typed struct to key layer-1 by.
// We register an innocuous-looking name ("vault_handle") that isSensitiveKey
// would let through, then assert both RedactBytes and redactValue scrub it.
func TestRegisteredSensitiveNameRedactedInBytesAndValue(t *testing.T) {
	const field = "vault_handle"
	if isSensitiveKey(field) {
		t.Fatalf("test premise broken: %q must NOT be caught by the generic heuristics", field)
	}
	RegisterSensitiveFields(reflect.TypeOf(genLike{}).PkgPath(), map[string][]string{"Widget": {field}})

	// Byte backstop (jentic api passthrough / execute --raw).
	body := []byte(`{"vault_handle":"abc123","name":"safe"}`)
	got := string(RedactBytes(body))
	if strings.Contains(got, "abc123") {
		t.Errorf("x-sensitive field leaked through RedactBytes: %s", got)
	}
	if !strings.Contains(got, `"vault_handle":"[REDACTED]"`) {
		t.Errorf("x-sensitive field not redacted in byte pass: %s", got)
	}
	if strings.Contains(got, `"name":"[REDACTED]"`) {
		t.Errorf("byte pass over-redacted a non-sensitive field: %s", got)
	}

	// Untyped structured pass (layer 2).
	red := redactValue(map[string]any{"vault_handle": "abc123", "name": "safe"}).(map[string]any)
	if red["vault_handle"] != "[REDACTED]" {
		t.Errorf("x-sensitive field not redacted in value pass: %v", red)
	}
	if red["name"] != "safe" {
		t.Errorf("value pass over-redacted a non-sensitive field: %v", red)
	}
}

// TestRedactBytesRedactsNonStringValues pins the round-3 P0 hardening: the byte
// backstop's reKV regex matched `"key":"string"` pairs ONLY, so a secret carried
// as a JSON number, object, array, or bool reached stdout untouched on the raw
// path (execute --raw, inspect, apis spec, api passthrough). RedactBytes now
// parses valid JSON and runs the structured key pass, which redacts a sensitive
// key regardless of its value's JSON type.
func TestRedactBytesRedactsNonStringValues(t *testing.T) {
	cases := []struct {
		name       string
		body       string
		mustGone   string // substring that must NOT survive
		mustRedact string // json fragment that must appear
	}{
		{
			name:       "number token",
			body:       `{"token":1234567890,"page":2}`,
			mustGone:   "1234567890",
			mustRedact: `"token":"[REDACTED]"`,
		},
		{
			name:       "object secret",
			body:       `{"credentials":{"access_key":"AKIA123","region":"eu"},"ok":true}`,
			mustGone:   "AKIA123",
			mustRedact: `"credentials":"[REDACTED]"`,
		},
		{
			name:       "array api_key",
			body:       `{"api_key":["k1","k2"],"count":2}`,
			mustGone:   "k1",
			mustRedact: `"api_key":"[REDACTED]"`,
		},
		{
			name:       "bool password",
			body:       `{"password":false}`,
			mustGone:   "false",
			mustRedact: `"password":"[REDACTED]"`,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := string(RedactBytes([]byte(tc.body)))
			if strings.Contains(got, tc.mustGone) {
				t.Errorf("non-string secret leaked through RedactBytes: %s", got)
			}
			if !strings.Contains(got, tc.mustRedact) {
				t.Errorf("expected %q in redacted output, got: %s", tc.mustRedact, got)
			}
		})
	}
}

// TestRedactBytesPreservesNonSensitiveNonString ensures the structured pass does
// NOT clobber legitimate non-string values on non-sensitive keys — a redactor
// that corrupts pagination cursors/counts is as bad as one that leaks.
func TestRedactBytesPreservesNonSensitiveNonString(t *testing.T) {
	body := `{"next_token":"cursor-abc","count":42,"items":[{"id":1}],"ok":true}`
	got := string(RedactBytes([]byte(body)))
	// next_token is an allowlisted pagination cursor — must survive verbatim.
	if !strings.Contains(got, `"next_token":"cursor-abc"`) {
		t.Errorf("allowlisted next_token was clobbered: %s", got)
	}
	for _, want := range []string{`"count":42`, `"id":1`, `"ok":true`} {
		if !strings.Contains(got, want) {
			t.Errorf("non-sensitive value corrupted, missing %q: %s", want, got)
		}
	}
}

// TestRedactBytesPreservesShapeWhenNothingRedacted pins the WriteJSON contract
// (regression from the round-3 P0): when a body has NOTHING sensitive, RedactBytes
// must return it byte-for-byte (no re-marshal, no key reordering, no
// re-indentation). WriteJSON feeds already-indented JSON through here and a golden
// test asserts the exact bytes; re-marshaling would silently compact/reorder it.
func TestRedactBytesPreservesShapeWhenNothingRedacted(t *testing.T) {
	// Indented, deliberately NON-alphabetical key order.
	indented := "{\n  \"b\": \"2\",\n  \"a\": \"1\"\n}\n"
	if got := string(RedactBytes([]byte(indented))); got != indented {
		t.Errorf("indented non-sensitive JSON reshaped:\ngot  %q\nwant %q", got, indented)
	}
	// Compact form must likewise pass through untouched.
	compact := `{"b":"2","a":"1"}`
	if got := string(RedactBytes([]byte(compact))); got != compact {
		t.Errorf("compact non-sensitive JSON reshaped:\ngot  %q\nwant %q", got, compact)
	}
}

// TestRedactBytesNonJSONFallsBackToByteBackstop ensures a non-JSON body (e.g. a
// Markdown inspect body or a YAML spec) is still scrubbed by the byte backstop
// and is not mangled by a failed JSON parse.
func TestRedactBytesNonJSONFallsBackToByteBackstop(t *testing.T) {
	// Markdown with an embedded bearer token in a code fence.
	body := "# Operation\n\nAuthorization: Bearer sk-live-SECRET123\n\nSee docs."
	got := string(RedactBytes([]byte(body)))
	if strings.Contains(got, "sk-live-SECRET123") {
		t.Errorf("bearer token leaked through non-JSON path: %s", got)
	}
	if !strings.Contains(got, "# Operation") {
		t.Errorf("non-JSON body was mangled: %s", got)
	}
}
