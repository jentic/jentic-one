package cmdcore

import (
	"bytes"
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

func TestFlagsAllowPrompt(t *testing.T) {
	newCmd := func() *cobra.Command {
		c := &cobra.Command{Use: "register", Run: func(*cobra.Command, []string) {}}
		c.Flags().String("url", "", "")
		c.Flags().String("env", "", "")
		c.Flags().String("name", "", "")
		c.Flags().String("broker-url", "", "")
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

// TestSetupFlagsAllowPrompt proves setup's extended field-flag set
// treats a flag-driven skill-target run (e.g. --operator) as non-interactive,
// where the bare register set would still prompt.
func TestSetupFlagsAllowPrompt(t *testing.T) {
	newCmd := func() *cobra.Command {
		c := &cobra.Command{Use: "setup", Run: func(*cobra.Command, []string) {}}
		c.Flags().String("url", "", "")
		c.Flags().String("env", "", "")
		c.Flags().String("name", "", "")
		c.Flags().String("broker-url", "", "")
		c.Flags().StringSlice("operator", nil, "")
		c.Flags().Bool("all", false, "")
		c.Flags().String("scope", "", "")
		c.Flags().Bool("skip-skill", false, "")
		return c
	}

	if !flagsAllowPrompt(newCmd(), false, SetupFieldFlags...) {
		t.Errorf("bare setup (no flags, no --yes) should allow prompting")
	}
	for _, f := range []string{"operator", "all", "scope", "skip-skill"} {
		c := newCmd()
		if err := c.Flags().Set(f, valueFor(f)); err != nil {
			t.Fatalf("set %s: %v", f, err)
		}
		if flagsAllowPrompt(c, false, SetupFieldFlags...) {
			t.Errorf("setting --%s should suppress prompting for setup", f)
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

// TestValidateBrokerURL pins the --broker-url guard: an explicit broker must be
// an absolute http(s) URL, and https is required for any non-loopback host —
// `jentic execute` sends the agent bearer to this URL (SEC-1), so a plaintext
// remote broker must be rejected at onboarding, not discovered at the first
// execute.
func TestValidateBrokerURL(t *testing.T) {
	valid := []string{
		"https://broker.jentic.example.com",
		"https://broker.example.com:8443",
		"http://127.0.0.1:8100", // plaintext allowed only for loopback
		"http://localhost:8100",
		"http://[::1]:8100",
	}
	for _, in := range valid {
		if err := validateBrokerURL(in); err != nil {
			t.Errorf("validateBrokerURL(%q) = %v, want nil", in, err)
		}
	}

	invalid := []string{
		"http://broker.example.com", // bearer over plaintext to a remote host
		"http://10.0.0.5:8100",
		"broker.example.com", // not absolute
		"ftp://broker.example.com",
		"https://", // no host
		"not a url",
	}
	for _, in := range invalid {
		err := validateBrokerURL(in)
		if err == nil {
			t.Errorf("validateBrokerURL(%q) = nil, want rejection", in)
			continue
		}
		var coded *ux.CodedError
		if !errors.As(err, &coded) || coded.Code != ux.CodeMissingArgument {
			t.Errorf("validateBrokerURL(%q) error = %v, want coded MISSING_ARGUMENT", in, err)
		}
	}
}

// TestSeedBrokerURL pins the new-environment broker resolution: an explicit
// --broker-url always wins (remote one-command onboarding), otherwise only a
// loopback control plane gets the co-located seed — a remote URL with no
// explicit broker stays empty (never derived).
func TestSeedBrokerURL(t *testing.T) {
	cases := []struct {
		explicit, installURL, want string
	}{
		{"https://broker.example.com", "https://jentic.example.com", "https://broker.example.com"},
		{"https://broker.example.com", "http://127.0.0.1:8000", "https://broker.example.com"}, // explicit beats the loopback seed
		{"", "http://127.0.0.1:8000", "http://127.0.0.1:8100"},
		{"", "https://jentic.example.com", ""},
	}
	for _, c := range cases {
		if got := seedBrokerURL(c.explicit, c.installURL); got != c.want {
			t.Errorf("seedBrokerURL(%q, %q) = %q, want %q", c.explicit, c.installURL, got, c.want)
		}
	}
}

// TestApplyBrokerURL pins the semantics both register arms share when an
// explicit --broker-url meets an EXISTING environment: filling an empty
// broker_url is a completion (always allowed), re-setting the same value is an
// idempotent no-op, repointing a DIFFERENT broker refuses without --force
// (every context bound to the env depends on it), --force repoints, and a
// missing environment is a coded failure, never an implicit create.
func TestApplyBrokerURL(t *testing.T) {
	const envName = "prod"
	newCfg := func(broker string) *sdkconfig.Config {
		return &sdkconfig.Config{Environments: map[string]sdkconfig.Env{
			envName: {BaseURL: "https://jentic.example.com", BrokerURL: broker},
		}}
	}

	t.Run("fills an empty broker_url", func(t *testing.T) {
		cfg := newCfg("")
		if err := applyBrokerURL(cfg, envName, "https://broker.example.com", false); err != nil {
			t.Fatalf("fill empty: %v", err)
		}
		if got := cfg.Environments[envName].BrokerURL; got != "https://broker.example.com" {
			t.Errorf("broker_url = %q, want the filled value", got)
		}
	})

	t.Run("same value is a no-op", func(t *testing.T) {
		cfg := newCfg("https://broker.example.com")
		if err := applyBrokerURL(cfg, envName, "https://broker.example.com", false); err != nil {
			t.Fatalf("idempotent re-set: %v", err)
		}
	})

	t.Run("different value refuses without force", func(t *testing.T) {
		cfg := newCfg("https://broker-a.example.com")
		err := applyBrokerURL(cfg, envName, "https://broker-b.example.com", false)
		var coded *ux.CodedError
		if !errors.As(err, &coded) || coded.Code != ux.CodeMissingArgument {
			t.Fatalf("repoint without force = %v, want coded MISSING_ARGUMENT", err)
		}
		if got := cfg.Environments[envName].BrokerURL; got != "https://broker-a.example.com" {
			t.Errorf("broker_url mutated on refusal: %q", got)
		}
	})

	t.Run("force repoints", func(t *testing.T) {
		cfg := newCfg("https://broker-a.example.com")
		if err := applyBrokerURL(cfg, envName, "https://broker-b.example.com", true); err != nil {
			t.Fatalf("force repoint: %v", err)
		}
		if got := cfg.Environments[envName].BrokerURL; got != "https://broker-b.example.com" {
			t.Errorf("broker_url = %q, want the forced value", got)
		}
	})

	t.Run("missing environment is a coded failure", func(t *testing.T) {
		cfg := &sdkconfig.Config{Environments: map[string]sdkconfig.Env{}}
		err := applyBrokerURL(cfg, envName, "https://broker.example.com", false)
		var coded *ux.CodedError
		if !errors.As(err, &coded) || coded.Code != ux.CodeResolveFailed {
			t.Fatalf("missing env = %v, want coded RESOLVE_FAILED", err)
		}
	})
}

// TestAgentClaimURL pins the claim link the enterprise console expects: the SPA
// agent-claim page under /app with the single-use token in the `token` query
// param (url-escaped) — AgentClaimPage reads searchParams.get("token") — and no
// query string when the token is empty.
func TestAgentClaimURL(t *testing.T) {
	const base = "https://jentic.example.com"
	const id = "cid-123"

	got := agentClaimURL(base, id, "tok en/with+special")
	const want = "https://jentic.example.com/app/agents/cid-123/claim?token=tok+en%2Fwith%2Bspecial"
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

// TestSiblingContextInEnv proves the multi-agent "why a new context" pointer
// (UX5) fires only when a SECOND agent joins an env that already had one: it
// finds the pre-existing sibling context (same env, different context) and is
// silent (returns "") for the common single-agent case.
func TestSiblingContextInEnv(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(dir, "config"))
	t.Setenv("XDG_STATE_HOME", filepath.Join(dir, "state"))

	// First agent in env "qa1": no sibling yet.
	if err := sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
		cfg.Contexts["qa1-bot"] = sdkconfig.Context{Environment: "qa1", Identity: "bot"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if got := siblingContextInEnv("qa1", "qa1-bot"); got != "" {
		t.Errorf("single agent in env should have no sibling, got %q", got)
	}

	// Second agent joins env "qa1": the first context is the sibling. A context
	// in a DIFFERENT env must be ignored.
	if err := sdkconfig.MutateConfig(func(cfg *sdkconfig.Config) error {
		cfg.Contexts["qa1-bot5"] = sdkconfig.Context{Environment: "qa1", Identity: "bot5"}
		cfg.Contexts["prod-bot"] = sdkconfig.Context{Environment: "prod", Identity: "bot"}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if got := siblingContextInEnv("qa1", "qa1-bot5"); got != "qa1-bot" {
		t.Errorf("sibling in same env = %q, want %q", got, "qa1-bot")
	}
}
