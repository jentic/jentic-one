package cmdcore

import (
	"testing"

	"github.com/spf13/cobra"
)

func TestFlagsAllowPrompt(t *testing.T) {
	newCmd := func() *cobra.Command {
		c := &cobra.Command{Use: "register", Run: func(*cobra.Command, []string) {}}
		c.Flags().String("url", "", "")
		c.Flags().String("env", "", "")
		c.Flags().String("name", "", "")
		return c
	}

	if !flagsAllowPrompt(newCmd(), false, registerFieldFlags...) {
		t.Errorf("bare register (no flags, no --yes) should allow prompting")
	}
	if flagsAllowPrompt(newCmd(), true, registerFieldFlags...) {
		t.Errorf("--yes should suppress prompting")
	}

	for _, f := range registerFieldFlags {
		c := newCmd()
		if err := c.Flags().Set(f, "x"); err != nil {
			t.Fatalf("set %s: %v", f, err)
		}
		if flagsAllowPrompt(c, false, registerFieldFlags...) {
			t.Errorf("setting --%s should suppress prompting", f)
		}
	}
}

// TestBootstrapFlagsAllowPrompt proves bootstrap's extended field-flag set
// treats a flag-driven skill-target run (e.g. --operator) as non-interactive,
// where the bare register set would still prompt.
func TestBootstrapFlagsAllowPrompt(t *testing.T) {
	newCmd := func() *cobra.Command {
		c := &cobra.Command{Use: "bootstrap", Run: func(*cobra.Command, []string) {}}
		c.Flags().String("url", "", "")
		c.Flags().String("env", "", "")
		c.Flags().String("name", "", "")
		c.Flags().StringSlice("operator", nil, "")
		c.Flags().Bool("all", false, "")
		c.Flags().String("scope", "", "")
		c.Flags().Bool("skip-skill", false, "")
		return c
	}

	if !flagsAllowPrompt(newCmd(), false, BootstrapFieldFlags...) {
		t.Errorf("bare bootstrap (no flags, no --yes) should allow prompting")
	}
	for _, f := range []string{"operator", "all", "scope", "skip-skill"} {
		c := newCmd()
		if err := c.Flags().Set(f, valueFor(f)); err != nil {
			t.Fatalf("set %s: %v", f, err)
		}
		if flagsAllowPrompt(c, false, BootstrapFieldFlags...) {
			t.Errorf("setting --%s should suppress prompting for bootstrap", f)
		}
	}
}

func valueFor(flag string) string {
	switch flag {
	case "all", "skip-skill":
		return "true"
	case "operator":
		return "claude"
	default:
		return "x"
	}
}

// TestLocalBrokerURL pins the local-convenience broker seeding: a loopback
// control-plane URL gets the co-located http broker on the standard port, while
// any remote/enterprise URL gets "" (broker_url must be set explicitly there —
// it is never derived from a remote base_url).
func TestLocalBrokerURL(t *testing.T) {
	cases := map[string]string{
		"http://127.0.0.1:8000":       "http://127.0.0.1:8100",
		"http://localhost:8000":       "http://127.0.0.1:8100", // QA-9: localhost canonicalises to 127.0.0.1
		"http://127.0.0.1:9000":       "http://127.0.0.1:8100", // control port irrelevant; broker is its own port
		"http://[::1]:8000":           "http://[::1]:8100",
		"https://jentic.example.com":  "",
		"https://10.0.0.5:8000":       "",
		"http://192.168.1.10:8000":    "",
		"https://jentic-one.qa1.test": "",
		"not a url":                   "",
	}
	for in, want := range cases {
		if got := localBrokerURL(in); got != want {
			t.Errorf("localBrokerURL(%q) = %q, want %q", in, got, want)
		}
	}
}

// TestNormalizeLoopbackURL pins QA-9's control-plane normalization: a localhost
// URL is rewritten to 127.0.0.1 (preserving scheme/port/path) so the signed
// token audience matches the backend's canonical_base_url; every other host is
// returned verbatim.
func TestNormalizeLoopbackURL(t *testing.T) {
	cases := []struct {
		in      string
		want    string
		changed bool
	}{
		{"http://localhost:8000", "http://127.0.0.1:8000", true},
		{"http://localhost", "http://127.0.0.1", true},
		{"http://localhost:8000/base", "http://127.0.0.1:8000/base", true},
		{"http://127.0.0.1:8000", "http://127.0.0.1:8000", false},
		{"https://jentic.example.com", "https://jentic.example.com", false},
		{"not a url", "not a url", false},
	}
	for _, c := range cases {
		got, changed := normalizeLoopbackURL(c.in)
		if got != c.want || changed != c.changed {
			t.Errorf("normalizeLoopbackURL(%q) = (%q,%v), want (%q,%v)", c.in, got, changed, c.want, c.changed)
		}
	}
}
