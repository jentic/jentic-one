package ux

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func FuzzRedactBytes(f *testing.F) {
	sensitiveKeys := make([]string, 0, len(sensitiveKeyExact)+len(sensitiveKeySuffixes))
	for k := range sensitiveKeyExact {
		sensitiveKeys = append(sensitiveKeys, k)
	}
	for _, suf := range sensitiveKeySuffixes {
		sensitiveKeys = append(sensitiveKeys, "client"+suf)
	}

	for _, k := range sensitiveKeys {
		seed, err := json.Marshal(map[string]string{
			k:      "canary_secret_value_987654",
			"name": "public_item",
		})
		if err != nil {
			f.Fatalf("marshal seed for %q: %v", k, err)
		}
		f.Add(seed)
	}
	f.Add([]byte(`{"next_token":"cursor-123","count":2,"nested":{"api_key":12345}}`))
	f.Add([]byte("{\n  \"password\": \"hunter2\",\n  \"ok\": true\n}"))

	f.Fuzz(func(t *testing.T, data []byte) {
		out := RedactBytes(data)

		var parsed any
		if trimmed := bytesTrimSpace(data); len(trimmed) > 0 && (trimmed[0] == '{' || trimmed[0] == '[') {
			if json.Unmarshal(data, &parsed) == nil && !containsRegexDelimitersInJSONStrings(parsed) {
				if !json.Valid(out) {
					t.Fatalf("RedactBytes(%q) produced invalid JSON: %q", data, out)
				}
			}
		}

		if len(sensitiveKeys) == 0 {
			return
		}
		key := sensitiveKeys[int(firstByte(data))%len(sensitiveKeys)]
		if !isSensitiveKey(key) {
			t.Fatalf("expected %q to be a sensitive key", key)
		}
		const canary = "canary_fuzz_secret_7f3a9c1e"
		payload, err := json.Marshal(map[string]string{
			key:    canary + sanitizeCanarySuffix(data),
			"safe": "kept",
		})
		if err != nil {
			t.Fatalf("json.Marshal: %v", err)
		}
		redacted := RedactBytes(payload)
		if !json.Valid(redacted) {
			t.Fatalf("RedactBytes(%s) produced invalid JSON: %s", payload, redacted)
		}
		if bytes.Contains(redacted, []byte(canary)) {
			t.Fatalf("RedactBytes(%s) leaked canary in %s", payload, redacted)
		}
	})
}

func firstByte(b []byte) byte {
	if len(b) == 0 {
		return 0
	}
	return b[0]
}

func sanitizeCanarySuffix(b []byte) string {
	var sb strings.Builder
	for i := 0; i < len(b) && sb.Len() < 16; i++ {
		c := b[i]
		if (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') {
			sb.WriteByte(c)
		}
	}
	return sb.String()
}

func containsRegexDelimitersInJSONStrings(v any) bool {
	switch val := v.(type) {
	case map[string]any:
		for k, child := range val {
			if strings.ContainsAny(k, "\"\\\n\r=:") || containsRegexDelimitersInJSONStrings(child) {
				return true
			}
		}
	case []any:
		for _, child := range val {
			if containsRegexDelimitersInJSONStrings(child) {
				return true
			}
		}
	case string:
		if strings.ContainsAny(val, "\"\\\n\r=:") || strings.Contains(val, "-----BEGIN ") {
			return true
		}
	}
	return false
}
