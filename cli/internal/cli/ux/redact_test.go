package ux

import (
	"bytes"
	"encoding/json"
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
	RegisterSensitiveFields(map[string][]string{"genLike": {"token_value"}})
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
