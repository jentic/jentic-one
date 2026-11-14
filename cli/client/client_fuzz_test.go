package client

import (
	"strings"
	"testing"
)

func FuzzSanitizeSessionID(f *testing.F) {
	seeds := []string{
		"",
		"550e8400-e29b-41d4-a716-446655440000",
		"ci:run_42.attempt-1",
		"sess\r\nX-Injected: 1",
		`a "quoted id"`,
		"\r\n\x00 ",
		strings.Repeat("a", 300),
		"sess 123\t<script>",
	}
	for _, s := range seeds {
		f.Add(s)
	}

	f.Fuzz(func(t *testing.T, s string) {
		got := SanitizeSessionID(s)
		if len(got) > maxSessionIDLen {
			t.Fatalf("SanitizeSessionID(%q) length = %d, want <= %d", s, len(got), maxSessionIDLen)
		}
		for i := range len(got) {
			c := got[i]
			allowed := (c >= 'a' && c <= 'z') ||
				(c >= 'A' && c <= 'Z') ||
				(c >= '0' && c <= '9') ||
				c == '.' || c == '_' || c == ':' || c == '-'
			if !allowed {
				t.Fatalf("SanitizeSessionID(%q) contains disallowed byte %q at index %d in %q", s, c, i, got)
			}
		}
		if again := SanitizeSessionID(got); again != got {
			t.Fatalf("SanitizeSessionID(%q) = %q is not idempotent (got %q)", s, got, again)
		}
	})
}
