package api

import (
	"bytes"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	legacyconfig "github.com/jentic/jentic-one/cli/internal/config"
)

// These tests pin `jentic register` as the single V2 onboarding front door
// (register.go): one command takes a fresh machine to a working, approved
// context; an active context re-registers in place. The legacy --profile/
// --base-url arm was REMOVED at the activation release (14 BC-1) — the flags
// no longer exist.

// fastPollV2 shrinks the shared approval-poll cadence (cmdcore package vars)
// for the duration of a test.
func fastPollV2(t *testing.T) {
	t.Helper()
	oi, om, os := cmdcore.PollInitialDelay, cmdcore.PollMaxDelay, cmdcore.PollDelayStep
	cmdcore.PollInitialDelay = 2 * time.Millisecond
	cmdcore.PollMaxDelay = 5 * time.Millisecond
	cmdcore.PollDelayStep = 1 * time.Millisecond
	t.Cleanup(func() {
		cmdcore.PollInitialDelay, cmdcore.PollMaxDelay, cmdcore.PollDelayStep = oi, om, os
	})
}

// assertRegApproved asserts the (identity, env) registration record and the
// cached XDG token the approval wait minted.
func assertRegApproved(t *testing.T, identity, env string) {
	t.Helper()
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	reg, ok := cfg.Identities[identity].Environments[env]
	if !ok {
		t.Fatalf("no registration state for %s/%s; config: %+v", identity, env, cfg)
	}
	if reg.ClientID != "agnt_boot" || reg.Status != "approved" {
		t.Errorf("registration = %+v, want agnt_boot/approved", reg)
	}
	tokens, err := auth.ReadTokens(auth.IdentityRef{Identity: identity, Environment: env})
	if err != nil || tokens == nil || tokens.AccessToken == "" {
		t.Errorf("no cached XDG token after approval: %v %+v", err, tokens)
	}
}

// TestRegister_FreshMachine_OneCommand: on a machine with NO config anywhere,
// `jentic register --url <install>` must create the environment + identity +
// context trio, activate it, register, wait for approval, and mint — one
// command, zero prerequisite steps.
func TestRegister_FreshMachine_OneCommand(t *testing.T) {
	withXDG(t)
	srv, registers := bootstrapServer(t, 0) // approved immediately

	if err := runJentic(t, "register", "--url", srv.URL, "--name", "crawler", "--env", "qa", "--timeout", "5s"); err != nil {
		t.Fatalf("register --url: %v", err)
	}

	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if env, ok := cfg.Environments["qa"]; !ok || env.BaseURL != srv.URL {
		t.Errorf("environment qa = %+v, want base_url %s", env, srv.URL)
	}
	if id, ok := cfg.Identities["crawler"]; !ok || id.Type != "agent" {
		t.Errorf("identity crawler = %+v, want type agent", id)
	}
	if ctxCfg, ok := cfg.Contexts["qa-crawler"]; !ok || ctxCfg.Environment != "qa" || ctxCfg.Identity != "crawler" {
		t.Errorf("context qa-crawler = %+v, want qa/crawler", ctxCfg)
	}
	if cfg.ActiveContext != "qa-crawler" {
		t.Errorf("active context = %q, want qa-crawler", cfg.ActiveContext)
	}
	assertRegApproved(t, "crawler", "qa")
	if n := registers.Load(); n != 1 {
		t.Errorf("register endpoint hit %d times, want 1", n)
	}
}

// TestRegister_FreshMachine_Rerun: re-running the same command must be
// idempotent — reuse the trio and the existing client_id, not double-register.
func TestRegister_FreshMachine_Rerun(t *testing.T) {
	withXDG(t)
	srv, registers := bootstrapServer(t, 0)

	for range 2 {
		if err := runJentic(t, "register", "--url", srv.URL, "--name", "crawler", "--env", "qa", "--timeout", "5s"); err != nil {
			t.Fatalf("register --url: %v", err)
		}
	}
	if n := registers.Load(); n != 1 {
		t.Errorf("register endpoint hit %d times across two runs, want 1 (resumable)", n)
	}
	assertRegApproved(t, "crawler", "qa")
}

// TestRegister_ActiveContext_RegistersActivePair: with a context already
// active, bare `jentic register` registers THAT identity with THAT environment
// — no localhost default, no legacy profile.
func TestRegister_ActiveContext_RegistersActivePair(t *testing.T) {
	withXDG(t)
	srv, _ := bootstrapServer(t, 0)

	if err := runJentic(t, "env", "add", "local", "--url", srv.URL); err != nil {
		t.Fatalf("env add: %v", err)
	}
	if err := runJentic(t, "identity", "add", "agent1", "--type", "agent"); err != nil {
		t.Fatalf("identity add: %v", err)
	}
	if err := runJentic(t, "context", "create", "c1", "--env", "local", "--identity", "agent1"); err != nil {
		t.Fatalf("context create: %v", err)
	}
	if err := runJentic(t, "context", "use", "c1"); err != nil {
		t.Fatalf("context use: %v", err)
	}

	if err := runJentic(t, "register", "--timeout", "5s"); err != nil {
		t.Fatalf("register (active context): %v", err)
	}
	assertRegApproved(t, "agent1", "local")

	// The legacy store must be untouched: registration went to the XDG store.
	paths, _ := legacyconfig.NewPaths()
	if fc, err := legacyconfig.Load(paths); err == nil && fc != nil && fc.Loaded {
		t.Errorf("legacy ~/.jentic config appeared during a V2 register")
	}
}

// TestRegister_V2PendingThenApproved: a pending registration prints the
// approval console link and keeps polling until the operator approves.
func TestRegister_V2PendingThenApproved(t *testing.T) {
	withXDG(t)
	fastPollV2(t)
	srv, _ := bootstrapServer(t, 2) // two pending polls before approval

	out, err := runJenticCapture(t, "register", "--url", srv.URL, "--name", "crawler", "--env", "qa", "--timeout", "30s")
	if err != nil {
		t.Fatalf("register: %v\nout:\n%s", err, out)
	}
	for _, want := range []string{"Approve this agent", "agnt_boot", "Agent approved"} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
	assertRegApproved(t, "crawler", "qa")
}

// TestRegister_ClaimPending_AssertionInvalidIsNotFatal reproduces the enterprise
// claim flow: /register hands back a claim_token, and until the human claims +
// approves in the console, /oauth/token rejects the assertion with the ambiguous
// 400 invalid_grant "Assertion is invalid" — the SAME string the backend uses
// for a real audience mismatch. With a claim outstanding the CLI must treat that
// as PENDING (print the claim link with ?token=, keep waiting, and exit cleanly
// as TIMEOUT_PENDING) rather than hard-failing with the audience-mismatch hint,
// which used to abort the flow the instant it began.
func TestRegister_ClaimPending_AssertionInvalidIsNotFatal(t *testing.T) {
	withXDG(t)
	fastPollV2(t)

	const claimTok = "clm_once_abc"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/register":
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"client_id":"agnt_boot","status":"pending","claim_token":"` + claimTok + `"}`))
		case "/oauth/token":
			// Not-yet-claimed/approved agent: the backend's approval gate fires
			// before signature/audience validation and returns this exact string.
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"type":"invalid_grant","status":400,"detail":"Assertion is invalid","instance":"/oauth/token"}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)

	out, err := runJenticCapture(t, "register", "--url", srv.URL, "--name", "crawler", "--env", "qa", "--timeout", "20ms")

	// The claim affordance must have been shown, pointing at the console page
	// with the token in the `token` query param the page reads.
	if !strings.Contains(out, "Claim ownership of this agent") {
		t.Errorf("claim affordance not shown:\n%s", out)
	}
	if !strings.Contains(out, "/app/agents/agnt_boot/claim?token="+claimTok) {
		t.Errorf("claim link missing or wrong query param (want ?token=):\n%s", out)
	}

	// It must NOT hard-fail on the ambiguous assertion error; it should time out
	// as pending so re-running after the human claims + approves resumes.
	if err == nil {
		t.Fatal("expected a pending timeout, got success (agent was never approved)")
	}
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("error is not coded: %v", err)
	}
	if coded.Code != ux.CodeTimeoutPending {
		t.Errorf("code = %q, want TIMEOUT_PENDING (the audience-mismatch hard-fail must be suppressed while claiming); err: %v", coded.Code, err)
	}
}

// TestRegister_TwoAgentsSameEnv_DistinctContexts: registering a SECOND agent
// name into an env that already has one must NOT hijack the first agent's
// context. Each identity gets its own per-identity context (env "-" name); the
// just-registered one becomes active, and the earlier binding is left intact so
// you can switch back with `jentic context use`.
func TestRegister_TwoAgentsSameEnv_DistinctContexts(t *testing.T) {
	withXDG(t)
	srv, _ := bootstrapServer(t, 0) // approved immediately

	if err := runJentic(t, "register", "--url", srv.URL, "--name", "alpha", "--env", "qa", "--timeout", "5s"); err != nil {
		t.Fatalf("register alpha: %v", err)
	}
	if err := runJentic(t, "register", "--url", srv.URL, "--name", "beta", "--env", "qa", "--timeout", "5s"); err != nil {
		t.Fatalf("register beta: %v", err)
	}

	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}

	// Both contexts exist, each bound to its own identity in the shared env.
	if c, ok := cfg.Contexts["qa-alpha"]; !ok || c.Identity != "alpha" || c.Environment != "qa" {
		t.Errorf("context qa-alpha = %+v, want qa/alpha (first agent must be untouched)", c)
	}
	if c, ok := cfg.Contexts["qa-beta"]; !ok || c.Identity != "beta" || c.Environment != "qa" {
		t.Errorf("context qa-beta = %+v, want qa/beta", c)
	}

	// The just-registered identity is the active one — not the older alpha.
	if cfg.ActiveContext != "qa-beta" {
		t.Errorf("active context = %q, want qa-beta (newest register wins, no silent hijack)", cfg.ActiveContext)
	}

	// One shared environment, not two.
	if _, ok := cfg.Environments["qa"]; !ok || len(cfg.Environments) != 1 {
		t.Errorf("environments = %+v, want a single reused qa env", cfg.Environments)
	}
}

// TestRegister_LegacyFlagsRemoved: the V1 --profile/--base-url arm is gone
// (14 BC-1). The flags must fail as unknown — nothing may silently fall back
// to the legacy store.
func TestRegister_LegacyFlagsRemoved(t *testing.T) {
	withXDG(t)

	app := testApp(t)
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"register", "--profile", "demo", "--base-url", "http://127.0.0.1:1"})
	err := root.Execute()
	if err == nil || !strings.Contains(err.Error(), "unknown flag") {
		t.Fatalf("expected an unknown-flag error for the removed legacy flags, got %v", err)
	}

	// And nothing was written to either store.
	if _, statErr := os.Stat(filepath.Join(app.Paths.Root, "profiles")); statErr == nil {
		t.Error("removed legacy flags must not create a profile store")
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if len(cfg.Environments) != 0 || len(cfg.Identities) != 0 || cfg.ActiveContext != "" {
		t.Errorf("failed register leaked into the XDG store: %+v", cfg)
	}
}

// TestBootstrap_V2FreshMachine: bootstrap composes the same V2 setup arm with
// the skill step — the skill body must be templated with the INSTALL's URL,
// not a localhost default.
func TestBootstrap_V2FreshMachine(t *testing.T) {
	withXDG(t)
	home := t.TempDir()
	t.Setenv("HOME", home)
	srv, _ := bootstrapServer(t, 0)

	if err := runJentic(t,
		"bootstrap", "--url", srv.URL, "--name", "crawler", "--env", "qa",
		"--operator", "generic", "--scope", "user", "--timeout", "5s", "--yes",
	); err != nil {
		t.Fatalf("bootstrap --url: %v", err)
	}
	assertRegApproved(t, "crawler", "qa")
}
