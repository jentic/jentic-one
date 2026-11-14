package agentops

import (
	"errors"
	"net/http"
	"strings"
	"testing"
)

var errFuzzBadKV = errors.New("malformed key=value pair")

func FuzzParseMethodPath(f *testing.F) {
	seeds := []string{
		"GET:/v1/pets",
		"post:/v1/pets",
		"PUT:/a/b/c",
		"PATCH:/items/1",
		"DELETE:/items/1",
		"HEAD:/health",
		"OPTIONS:/v1",
		"TRACE:/debug",
		"GET:https://api.example.com/v1/pets",
		"GET://evil.example.com/path",
		"INVALID:/v1/pets",
		":/v1/pets",
		"GET:",
		"GET:relative/path",
		"op_12345",
		"",
	}
	for _, s := range seeds {
		f.Add(s)
	}

	validMethods := map[string]bool{
		http.MethodGet:     true,
		http.MethodPost:    true,
		http.MethodPut:     true,
		http.MethodPatch:   true,
		http.MethodDelete:  true,
		http.MethodHead:    true,
		http.MethodOptions: true,
		http.MethodTrace:   true,
	}

	f.Fuzz(func(t *testing.T, target string) {
		m, p := ParseMethodPath(target)
		if m == "" {
			if p != "" {
				t.Fatalf("ParseMethodPath(%q) = (%q, %q), want empty path when method is empty", target, m, p)
			}
			return
		}
		if !validMethods[m] {
			t.Fatalf("ParseMethodPath(%q) returned unexpected method %q", target, m)
		}
		if !strings.HasPrefix(p, "/") {
			t.Fatalf("ParseMethodPath(%q) path %q does not start with '/'", target, p)
		}
		if strings.HasPrefix(p, "//") {
			t.Fatalf("ParseMethodPath(%q) path %q starts with '//'", target, p)
		}
	})
}

func FuzzParseKVs(f *testing.F) {
	seeds := []string{
		"",
		"a=1",
		"a=1\nb=2",
		"Authorization=Bearer tok\nX-Custom=val=with=equals",
		"missing_equals",
		"a=1\nmissing_equals\nc=3",
		"=empty_key",
		"empty_val=",
	}
	for _, s := range seeds {
		f.Add(s)
	}

	f.Fuzz(func(t *testing.T, raw string) {
		var inputs []string
		if raw != "" {
			inputs = strings.Split(raw, "\n")
		}

		badCalls := 0
		kvs, err := ParseKVs(inputs, func(value string) error {
			badCalls++
			if strings.Contains(value, "=") {
				t.Fatalf("badKV called for input %q that contains '='", value)
			}
			return errFuzzBadKV
		})

		hasErr := err != nil
		parsedAll := err == nil && len(kvs) == len(inputs)
		if hasErr == parsedAll {
			t.Fatalf("ParseKVs(%q): err=%v, len(kvs)=%d, len(inputs)=%d", inputs, err, len(kvs), len(inputs))
		}
		if hasErr {
			if badCalls != 1 || kvs != nil {
				t.Fatalf("ParseKVs(%q) on error: badCalls=%d, kvs=%v", inputs, badCalls, kvs)
			}
			return
		}
		for i, kv := range kvs {
			if strings.Contains(kv.Key, "=") {
				t.Fatalf("ParseKVs(%q)[%d].Key = %q contains '='", inputs, i, kv.Key)
			}
			if kv.Key+"="+kv.Value != inputs[i] {
				t.Fatalf("ParseKVs(%q)[%d] = %+v, does not round-trip %q", inputs, i, kv, inputs[i])
			}
		}
	})
}
