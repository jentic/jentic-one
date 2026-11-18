package config_test

import (
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/client/config"
)

func FuzzSanitizeName(f *testing.F) {
	seeds := []string{
		"work",
		"My_Profile",
		"api.jentic.com",
		"_leading",
		"trailing_",
		"UPPER",
		"123",
		"",
		"   ",
		"$$$",
		"-only-",
		"9lives",
		"...42...",
		"../escape",
		"../../etc/passwd",
		"jentic.file-less-agent",
		"has_underscore",
		"has.dot",
		"0-",
		strings.Repeat("a", 80),
		strings.Repeat("a-", 40),
	}
	for _, s := range seeds {
		f.Add(s)
	}

	f.Fuzz(func(t *testing.T, s string) {
		got := config.SanitizeName(s)
		if got != "" && !config.ValidName(got) {
			t.Fatalf("SanitizeName(%q) = %q does not satisfy ValidName", s, got)
		}
		if again := config.SanitizeName(got); again != got {
			t.Fatalf("SanitizeName(%q) = %q is not idempotent: SanitizeName(%q) = %q", s, got, got, again)
		}

		for _, ref := range []auth.IdentityRef{
			{Identity: s, Environment: "prod"},
			{Identity: "agent", Environment: s},
			{Identity: s, Environment: s},
		} {
			if stem, err := ref.Stem(); err == nil {
				assertSafeStem(t, ref, stem)
			}
		}

		if got != "" {
			ref := auth.IdentityRef{Identity: got, Environment: got}
			stem, err := ref.Stem()
			if err != nil {
				t.Fatalf("IdentityRef{%q, %q}.Stem() unexpected error for ValidName: %v", got, got, err)
			}
			assertSafeStem(t, ref, stem)
		}
	})
}

func assertSafeStem(t *testing.T, ref auth.IdentityRef, stem string) {
	t.Helper()
	if strings.ContainsAny(stem, `/\.`) {
		t.Fatalf("IdentityRef%+v.Stem() = %q contains path separator or dot", ref, stem)
	}
	if strings.Count(stem, "_") != 1 {
		t.Fatalf("IdentityRef%+v.Stem() = %q contains '_' count %d, want 1", ref, stem, strings.Count(stem, "_"))
	}
}
