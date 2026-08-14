package cmdcore

import (
	"bytes"
	"context"
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
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

// TestIsMachineCtx pins AGT-21's mode gate: register (unfenced, run by agents)
// consults the resolved mode via the context to decide whether its human
// progress prose is allowed on stdout. Agent/service-account => machine (prose
// suppressed); human or a missing state (register outside the interceptor) =>
// prose allowed.
func TestIsMachineCtx(t *testing.T) {
	cases := []struct {
		name string
		st   *clictx.ActiveState
		want bool
	}{
		{"agent", &clictx.ActiveState{Mode: clictx.ModeAgent}, true},
		{"service-account", &clictx.ActiveState{Mode: clictx.ModeServiceAccount}, true},
		{"human", &clictx.ActiveState{Mode: clictx.ModeHuman}, false},
		{"no-state fails open to human", nil, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			ctx := context.Background()
			if c.st != nil {
				ctx = clictx.WithActiveState(ctx, c.st)
			}
			if got := isMachineCtx(ctx); got != c.want {
				t.Errorf("isMachineCtx = %v, want %v", got, c.want)
			}
		})
	}
}

// TestRegisterProgressSuppressedInMachineMode pins AGT-21: a human progress line
// is written to stdout in human mode but MUST NOT reach stdout in agent mode
// (where the single JSON Result is the only sanctioned stdout content).
func TestRegisterProgressSuppressedInMachineMode(t *testing.T) {
	t.Run("human writes prose", func(t *testing.T) {
		app := testApp(t)
		ctx := clictx.WithActiveState(context.Background(), &clictx.ActiveState{Mode: clictx.ModeHuman})
		app.registerProgress(ctx, "hello human")
		if got := app.Out.(*bytes.Buffer).String(); !strings.Contains(got, "hello human") {
			t.Errorf("human progress not written to stdout: %q", got)
		}
	})
	t.Run("agent suppresses prose", func(t *testing.T) {
		app := testApp(t)
		ctx := clictx.WithActiveState(context.Background(), &clictx.ActiveState{Mode: clictx.ModeAgent})
		app.registerProgress(ctx, "hello human")
		if got := app.Out.(*bytes.Buffer).String(); got != "" {
			t.Errorf("agent-mode progress leaked to stdout: %q", got)
		}
	})
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

// TestAgentClaimURL pins the by-convention claim link: the SPA agent-claim page
// under /app carrying the single-use token as a query param (url-escaped), and
// no ?claim_token= when the token is empty.
func TestAgentClaimURL(t *testing.T) {
	const base = "https://jentic.example.com"
	const id = "cid-123"

	got := agentClaimURL(base, id, "tok en/with+special")
	const want = "https://jentic.example.com/app/agents/cid-123/claim?claim_token=tok+en%2Fwith%2Bspecial"
	if got != want {
		t.Errorf("agentClaimURL with token = %q, want %q", got, want)
	}

	if got := agentClaimURL(base, id, ""); got != base+"/app/agents/"+id+"/claim" {
		t.Errorf("agentClaimURL empty token = %q, want no query string", got)
	}
}

// TestPresentClaimAffordance proves the human claim guidance appears once (link +
// raw token + the exact `jentic identity claim` command) when a claim token is
// present in human mode, is suppressed in machine mode (the ux.Result carries the
// machine signal instead), and is byte-for-byte silent on the OSS default (empty
// token) so onboarding output is unchanged there.
func TestPresentClaimAffordance(t *testing.T) {
	const base = "http://127.0.0.1:8000"
	const id = "cid-abc"
	const tok = "clm_secret_once"

	t.Run("human with token shows link + token + command", func(t *testing.T) {
		app := testApp(t)
		ctx := clictx.WithActiveState(context.Background(), &clictx.ActiveState{Mode: clictx.ModeHuman})
		app.presentClaimAffordance(ctx, base, id, tok)
		out := app.Out.(*bytes.Buffer).String()
		for _, want := range []string{
			agentClaimURL(base, id, tok), // clickable link
			tok,                          // raw one-time token
			"jentic identity claim " + id + " --token " + tok, // exact command
			"an agent cannot claim itself",                    // human-only framing
		} {
			if !strings.Contains(out, want) {
				t.Errorf("human claim affordance missing %q in:\n%s", want, out)
			}
		}
	})

	t.Run("machine mode suppresses prose", func(t *testing.T) {
		app := testApp(t)
		ctx := clictx.WithActiveState(context.Background(), &clictx.ActiveState{Mode: clictx.ModeAgent})
		app.presentClaimAffordance(ctx, base, id, tok)
		if got := app.Out.(*bytes.Buffer).String(); got != "" {
			t.Errorf("machine-mode claim prose leaked to stdout: %q", got)
		}
	})

	t.Run("OSS default (no token) is silent", func(t *testing.T) {
		app := testApp(t)
		ctx := clictx.WithActiveState(context.Background(), &clictx.ActiveState{Mode: clictx.ModeHuman})
		app.presentClaimAffordance(ctx, base, id, "")
		if got := app.Out.(*bytes.Buffer).String(); got != "" {
			t.Errorf("empty-token claim affordance must be silent, got: %q", got)
		}
	})
}
