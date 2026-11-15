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
		"apiKey":                 "api_key",
		"APIKey":                 "api_key",
		"HTTPSProxy":             "https_proxy",
		"clientSecret":           "client_secret",
		"refreshToken":           "refresh_token",
		"already_snake":          "already_snake",
		"PublicKey":              "public_key",
		"X-Auth-Token":           "x_auth_token",
		"X-Shopify-Access-Token": "x_shopify_access_token",
		"Api-Key":                "api_key",
		"Proxy-Authorization":    "proxy_authorization",
		"next-token":             "next_token",
		"multi---sep__key":       "multi_sep_key",
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
		"X-Auth-Token", "X-Access-Token", "X-Shopify-Access-Token",
		"Api-Key", "x-api-key", "X-API-Key",
		"access-token", "refresh-token", "client-secret", "private-key",
		"Proxy-Authorization", "proxy-authorization", "Set-Cookie",
	}
	notSensitive := []string{
		"next_token", "next-token", "Next-Token",
		"token_count", "public_key", "key_id", "secret_name",
		"password_policy", "name", "id", "url", "count",
		"has_api_key", "has-api-key", "has-credentials", "has-credential",
		"content-type", "x-request-id", "idempotency-key",
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

func TestRedactBytes_HeaderStyleSensitiveKey(t *testing.T) {
	body := strings.Join([]string{
		"HTTP/1.1 401 Unauthorized",
		"X-Api-Key: key_header_secret_123",            // pragma: allowlist secret
		"Set-Cookie: sid=cookie_secret_456; HttpOnly", // pragma: allowlist secret
		"Note: the token expired",
	}, "\n")
	got := string(RedactBytes([]byte(body)))
	for _, secret := range []string{"key_header_secret_123", "cookie_secret_456"} { // pragma: allowlist secret
		if strings.Contains(got, secret) {
			t.Errorf("header secret %q leaked through RedactBytes: %s", secret, got)
		}
	}
	for _, want := range []string{
		"X-Api-Key: [REDACTED]",
		"Set-Cookie: [REDACTED]",
		"Note: the token expired",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("expected %q in output, got:\n%s", want, got)
		}
	}
}

func TestRedactBytes_YAMLSensitiveKey(t *testing.T) {
	body := strings.Join([]string{
		"service: demo",
		"credentials:",
		"  api_key: key_yaml_secret_456",      // pragma: allowlist secret
		"  client_secret: cs_yaml_secret_789", // pragma: allowlist secret
		"  next_token: abc",
		"  token_count: 5",
		"  public_key: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5",
		"  key_id: kid_01HXYZ",
		"Note: the token expired",
	}, "\n")
	got := string(RedactBytes([]byte(body)))
	for _, secret := range []string{"key_yaml_secret_456", "cs_yaml_secret_789"} { // pragma: allowlist secret
		if strings.Contains(got, secret) {
			t.Errorf("YAML secret %q leaked through RedactBytes: %s", secret, got)
		}
	}
	for _, want := range []string{
		"  api_key: [REDACTED]",
		"  client_secret: [REDACTED]",
		"  next_token: abc",
		"  token_count: 5",
		"  public_key: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5",
		"  key_id: kid_01HXYZ",
		"Note: the token expired",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("expected %q in output, got:\n%s", want, got)
		}
	}
}

func TestRedactBytes_AnyAuthScheme(t *testing.T) {
	cases := []struct {
		name   string
		in     string
		secret string
		isJSON bool
	}{
		{
			name:   "Token scheme header",
			in:     "Authorization: Token tok_scheme_secret_111", // pragma: allowlist secret
			secret: "tok_scheme_secret_111",                      // pragma: allowlist secret
		},
		{
			name:   "Digest scheme",
			in:     "upstream error: Authorization: Digest dXNlcjpkigest_secret_222", // pragma: allowlist secret
			secret: "dXNlcjpkigest_secret_222",                                       // pragma: allowlist secret
		},
		{
			name:   "ApiKey scheme",
			in:     "upstream error: Authorization: ApiKey ak_custom_scheme_333", // pragma: allowlist secret
			secret: "ak_custom_scheme_333",                                       // pragma: allowlist secret
		},
		{
			name:   "AWS4-HMAC-SHA256 scheme",
			in:     "upstream error: Authorization: AWS4-HMAC-SHA256 sigv4_cred_secret_444", // pragma: allowlist secret
			secret: "sigv4_cred_secret_444",                                                 // pragma: allowlist secret
		},
		{
			name:   "Proxy-Authorization header",
			in:     "Proxy-Authorization: Token pk_proxy_scheme_555", // pragma: allowlist secret
			secret: "pk_proxy_scheme_555",                            // pragma: allowlist secret
		},
		{
			name:   "inline in JSON string",
			in:     `{"detail":"upstream rejected Authorization: Token tok_inline_secret_666"}`, // pragma: allowlist secret
			secret: "tok_inline_secret_666",                                                     // pragma: allowlist secret
			isJSON: true,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			out := RedactBytes([]byte(tc.in))
			got := string(out)
			if strings.Contains(got, tc.secret) {
				t.Errorf("auth scheme secret %q leaked: %s", tc.secret, got)
			}
			if !strings.Contains(got, "[REDACTED]") {
				t.Errorf("expected [REDACTED] in output: %s", got)
			}
			if tc.isJSON && !json.Valid(out) {
				t.Errorf("redacted JSON is invalid: %s", got)
			}
		})
	}
}

func TestRedactBytes_JAKValue(t *testing.T) {
	jsonBody := []byte(`{"key":"jak_live_secret_key_123456","name":"prod"}`) // pragma: allowlist secret
	gotJSON := string(RedactBytes(jsonBody))
	if strings.Contains(gotJSON, "jak_live_secret_key_123456") { // pragma: allowlist secret
		t.Errorf("jak_ key in JSON value leaked: %s", gotJSON)
	}
	if !strings.Contains(gotJSON, `"key":"jak_[REDACTED]"`) {
		t.Errorf("expected jak_[REDACTED] in JSON output, got: %s", gotJSON)
	}
	if !strings.Contains(gotJSON, `"name":"prod"`) {
		t.Errorf("non-sensitive field mangled: %s", gotJSON)
	}

	textBody := []byte("failed to authenticate using jak_free_text_secret_987654 against upstream") // pragma: allowlist secret
	gotText := string(RedactBytes(textBody))
	if strings.Contains(gotText, "jak_free_text_secret_987654") { // pragma: allowlist secret
		t.Errorf("jak_ key in free text leaked: %s", gotText)
	}
	if !strings.Contains(gotText, "jak_[REDACTED]") {
		t.Errorf("expected jak_[REDACTED] in text output, got: %s", gotText)
	}
}

func TestRedactBytes_EscapedQuoteJSONFragment(t *testing.T) {
	// Non-JSON text embedding a JSON fragment whose sensitive value contains an escaped quote.
	body := []byte(`upstream error body: {"password":"ab\"cd_secret_tail","user":"alice"`) // pragma: allowlist secret
	got := string(RedactBytes(body))
	if strings.Contains(got, "cd_secret_tail") || strings.Contains(got, `ab\"cd`) { // pragma: allowlist secret
		t.Errorf("escaped-quote password tail leaked: %s", got)
	}
	if !strings.Contains(got, `"password":"[REDACTED]"`) {
		t.Errorf("expected redacted password key-value, got: %s", got)
	}
	if !strings.Contains(got, `"user":"alice"`) {
		t.Errorf("non-sensitive field in fragment mangled: %s", got)
	}
}

func TestRedact_HyphenatedSecretKeysAndProxyAuthorization(t *testing.T) {
	payload := map[string]any{
		"X-Auth-Token":           "xat_secret_val_101",            // pragma: allowlist secret
		"X-Access-Token":         "xact_secret_val_102",           // pragma: allowlist secret
		"X-Shopify-Access-Token": "shpat_secret_val_103",          // pragma: allowlist secret
		"Api-Key":                "ak_hyphen_secret_104",          // pragma: allowlist secret
		"access-token":           "at_hyphen_secret_105",          // pragma: allowlist secret
		"refresh-token":          "rt_hyphen_secret_106",          // pragma: allowlist secret
		"client-secret":          "cs_hyphen_secret_107",          // pragma: allowlist secret
		"private-key":            "pk_hyphen_secret_108",          // pragma: allowlist secret
		"Proxy-Authorization":    "opaque_proxy_cred_secret_109",  // pragma: allowlist secret
		"next-token":             "cursor_hyphen_allowlisted_999", // pragma: allowlist secret
	}
	allSecrets := []string{
		"xat_secret_val_101",           // pragma: allowlist secret
		"xact_secret_val_102",          // pragma: allowlist secret
		"shpat_secret_val_103",         // pragma: allowlist secret
		"ak_hyphen_secret_104",         // pragma: allowlist secret
		"at_hyphen_secret_105",         // pragma: allowlist secret
		"rt_hyphen_secret_106",         // pragma: allowlist secret
		"cs_hyphen_secret_107",         // pragma: allowlist secret
		"pk_hyphen_secret_108",         // pragma: allowlist secret
		"opaque_proxy_cred_secret_109", // pragma: allowlist secret
	}

	// Structured redactValue pass.
	redMap := redactValue(payload).(map[string]any)
	for k, v := range redMap {
		if k == "next-token" {
			if v != "cursor_hyphen_allowlisted_999" {
				t.Errorf("allowlisted next-token clobbered in redactValue: %v", v)
			}
			continue
		}
		if v != "[REDACTED]" {
			t.Errorf("hyphenated key %q leaked in redactValue: %v", k, v)
		}
	}

	// Raw JSON bytes through BOTH RedactBytes and redactSensitive.
	rawJSON, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}
	for _, fn := range []struct {
		name string
		run  func([]byte) []byte
	}{
		{"RedactBytes", RedactBytes},
		{"redactSensitive", redactSensitive},
	} {
		out := fn.run(rawJSON)
		got := string(out)
		if !json.Valid(out) {
			t.Errorf("%s produced invalid JSON: %s", fn.name, got)
		}
		for _, sec := range allSecrets {
			if strings.Contains(got, sec) {
				t.Errorf("%s leaked secret %q: %s", fn.name, sec, got)
			}
		}
		if !strings.Contains(got, `"next-token":"cursor_hyphen_allowlisted_999"`) {
			t.Errorf("%s clobbered allowlisted next-token: %s", fn.name, got)
		}
	}

	// Proxy-Authorization and Authorization with Bearer + opaque schemes across
	// quoted/truncated/header forms, including direct reAuthScheme matching.
	authInputs := []struct {
		name   string
		in     string
		secret string
	}{
		{
			name:   "Proxy-Authorization Bearer in JSON",
			in:     `{"Proxy-Authorization": "Bearer proxy_bearer_secret_201"}`, // pragma: allowlist secret
			secret: "proxy_bearer_secret_201",                                   // pragma: allowlist secret
		},
		{
			name:   "Proxy-Authorization opaque in JSON",
			in:     `{"Proxy-Authorization": "opaque_proxy_token_202"}`, // pragma: allowlist secret
			secret: "opaque_proxy_token_202",                            // pragma: allowlist secret
		},
		{
			name:   "Truncated JSON quoted Proxy-Authorization Bearer",
			in:     `upstream trace: {"Proxy-Authorization": "Bearer proxy_trunc_secret_203`, // pragma: allowlist secret
			secret: "proxy_trunc_secret_203",                                                 // pragma: allowlist secret
		},
		{
			name:   "Truncated JSON quoted Authorization Token",
			in:     `upstream trace: {"Authorization": "Token auth_trunc_secret_204`, // pragma: allowlist secret
			secret: "auth_trunc_secret_204",                                          // pragma: allowlist secret
		},
		{
			name:   "Header Proxy-Authorization opaque",
			in:     "Proxy-Authorization: opaque_header_proxy_205", // pragma: allowlist secret
			secret: "opaque_header_proxy_205",                      // pragma: allowlist secret
		},
	}
	for _, tc := range authInputs {
		t.Run(tc.name, func(t *testing.T) {
			got := string(redactSensitive([]byte(tc.in)))
			if strings.Contains(got, tc.secret) {
				t.Errorf("redactSensitive leaked %q in: %s", tc.secret, got)
			}
			gotScheme := string(reAuthScheme.ReplaceAll([]byte(tc.in), []byte(`${1}${2}${3}${4}${5}[REDACTED]`)))
			if strings.Contains(gotScheme, tc.secret) {
				t.Errorf("reAuthScheme failed to match %q in: %s", tc.secret, gotScheme)
			}
		})
	}
}

func TestRedact_NestedJSONStringPayloads(t *testing.T) {
	innerBody := `{"access_token":"ya29_nested_secret_301","refresh-token":"rt_nested_secret_302","nested":{"client-secret":"cs_nested_secret_303","api_key":987654321},"next-token":"cursor_nested_ok","ok":true}` // pragma: allowlist secret
	envelope := map[string]any{
		"status_code":  200,
		"content_type": "text/plain",
		"body":         innerBody,
	}
	secrets := []string{
		"ya29_nested_secret_301", // pragma: allowlist secret
		"rt_nested_secret_302",   // pragma: allowlist secret
		"cs_nested_secret_303",   // pragma: allowlist secret
		"987654321",
	}

	// 1. Structured redactValue pass on execute-style envelope.
	redEnv := redactValue(envelope).(map[string]any)
	redBody, ok := redEnv["body"].(string)
	if !ok {
		t.Fatalf("expected redacted body to remain a string, got %T", redEnv["body"])
	}
	if !json.Valid([]byte(redBody)) {
		t.Fatalf("redacted body string is not valid JSON: %s", redBody)
	}
	for _, sec := range secrets {
		if strings.Contains(redBody, sec) {
			t.Errorf("redactValue leaked nested JSON string secret %q: %s", sec, redBody)
		}
	}
	if !strings.Contains(redBody, `"next-token":"cursor_nested_ok"`) {
		t.Errorf("redactValue clobbered allowlisted next-token inside body string: %s", redBody)
	}
	if !strings.Contains(redBody, `"ok":true`) {
		t.Errorf("redactValue corrupted non-sensitive field inside body string: %s", redBody)
	}

	// 2. Raw JSON bytes through safeMarshal, RedactBytes, and direct redactSensitive.
	rawEnvelope, err := json.Marshal(envelope)
	if err != nil {
		t.Fatalf("marshal envelope: %v", err)
	}
	for _, fn := range []struct {
		name string
		out  []byte
	}{
		{"safeMarshal", safeMarshal(envelope)},
		{"RedactBytes", RedactBytes(rawEnvelope)},
		{"redactSensitive", redactSensitive(rawEnvelope)},
	} {
		t.Run(fn.name, func(t *testing.T) {
			if !json.Valid(fn.out) {
				t.Fatalf("%s produced invalid JSON: %s", fn.name, fn.out)
			}
			s := string(fn.out)
			for _, sec := range secrets {
				if strings.Contains(s, sec) {
					t.Errorf("%s leaked nested JSON secret %q: %s", fn.name, sec, s)
				}
			}
			if !strings.Contains(s, `cursor_nested_ok`) {
				t.Errorf("%s clobbered allowlisted next-token: %s", fn.name, s)
			}
		})
	}

	// 3. Prefixed non-JSON log line and truncated escaped JSON fragment on stderr/raw path.
	prefixedLog := append([]byte("upstream 502 error: "), rawEnvelope...)
	gotLog := string(redactSensitive(prefixedLog))
	for _, sec := range secrets {
		if strings.Contains(gotLog, sec) {
			t.Errorf("prefixed log redactSensitive leaked %q: %s", sec, gotLog)
		}
	}
	if !strings.Contains(gotLog, "cursor_nested_ok") {
		t.Errorf("prefixed log redactSensitive clobbered next-token: %s", gotLog)
	}

	truncatedEscaped := []byte(`truncated body: "{\"access_token\":\"ya29_trunc_304\",\"client-secret\":\"cs_trunc_305\",\"next-token\":\"cursor_trunc_ok\"`) // pragma: allowlist secret
	gotTrunc := string(redactSensitive(truncatedEscaped))
	if strings.Contains(gotTrunc, "ya29_trunc_304") || strings.Contains(gotTrunc, "cs_trunc_305") { // pragma: allowlist secret
		t.Errorf("truncated escaped JSON leaked secret: %s", gotTrunc)
	}
	if !strings.Contains(gotTrunc, `\"next-token\":\"cursor_trunc_ok\"`) {
		t.Errorf("truncated escaped JSON clobbered allowlisted next-token: %s", gotTrunc)
	}

	// 4. Non-sensitive embedded JSON string preserves exact outer and inner shape.
	safeEnvelope := `{"z":"keep","body":"{\"b\":\"2\",\"a\":\"1\",\"next-token\":\"cursor_safe\"}"}`
	if gotSafe := string(RedactBytes([]byte(safeEnvelope))); gotSafe != safeEnvelope {
		t.Errorf("safe nested JSON string reshaped:\ngot  %s\nwant %s", gotSafe, safeEnvelope)
	}
}
