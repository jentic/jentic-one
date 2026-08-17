package api

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	legacyconfig "github.com/jentic/jentic-one/cli/internal/config"
)

// These tests pin `jentic register` as the single V2 onboarding front door
// (register.go): one command takes a fresh machine to a working, approved
// context; an active context re-registers in place. The legacy --profile/
// --base-url arm was REMOVED at the activation release (14 BC-1) — the flags
// no longer exist.

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

// TestRegister_ContextNameCollisionRefusedWithoutForce pins F2 (round-3 #4):
// SanitizeName(env + "-" + name) is many-to-one (it clamps to 64 chars), so two
// DIFFERENT identities whose derived context name collapses to the same string
// must NOT silently repoint the context. The second register (a different
// identity, same derived context) is refused without --force.
func TestRegister_ContextNameCollisionRefusedWithoutForce(t *testing.T) {
	withXDG(t)
	srv, _ := bootstrapServer(t, 0)

	// env "qa" → context = "qa-" + name, clamped to 64 chars. Two names sharing
	// the first 61 chars (so "qa-"+prefix hits the 64-char clamp identically) but
	// differing afterwards derive the SAME context name.
	prefix := strings.Repeat("a", 61)
	nameA := prefix + "x" // 62 chars, valid
	nameB := prefix + "y" // 62 chars, valid, same 64-char clamp of "qa-"+name

	if err := runJentic(t, "register", "--url", srv.URL, "--name", nameA, "--env", "qa", "--timeout", "5s"); err != nil {
		t.Fatalf("register A: %v", err)
	}

	err := runJentic(t, "register", "--url", srv.URL, "--name", nameB, "--env", "qa", "--timeout", "5s")
	if err == nil {
		t.Fatal("expected the colliding second register to be refused without --force")
	}
	if !strings.Contains(err.Error(), "already binds identity") {
		t.Errorf("error = %v, want a context-collision refusal", err)
	}

	// The first agent's binding must be intact (no silent hijack).
	cfg, lerr := sdkconfig.Load()
	if lerr != nil {
		t.Fatal(lerr)
	}
	ctxName := sdkconfig.SanitizeName("qa-" + nameA)
	if c, ok := cfg.Contexts[ctxName]; !ok || c.Identity != nameA {
		t.Errorf("context %q = %+v, want it still bound to the first identity %q", ctxName, c, nameA)
	}
}

// TestRegister_ContextNameCollisionForceRepoints confirms --force still lets the
// operator deliberately repoint a colliding context.
func TestRegister_ContextNameCollisionForceRepoints(t *testing.T) {
	withXDG(t)
	srv, _ := bootstrapServer(t, 0)

	prefix := strings.Repeat("a", 61)
	nameA := prefix + "x"
	nameB := prefix + "y"

	if err := runJentic(t, "register", "--url", srv.URL, "--name", nameA, "--env", "qa", "--timeout", "5s"); err != nil {
		t.Fatalf("register A: %v", err)
	}
	if err := runJentic(t, "register", "--url", srv.URL, "--name", nameB, "--env", "qa", "--timeout", "5s", "--force"); err != nil {
		t.Fatalf("register B --force: %v", err)
	}

	cfg, lerr := sdkconfig.Load()
	if lerr != nil {
		t.Fatal(lerr)
	}
	ctxName := sdkconfig.SanitizeName("qa-" + nameB)
	if c := cfg.Contexts[ctxName]; c.Identity != nameB {
		t.Errorf("with --force, context %q should repoint to %q, got %+v", ctxName, nameB, c)
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

// runRegisterCtx runs `jentic register …` through the full api tree with a
// caller-supplied context (so a test can cancel it mid-poll). It mirrors
// runJenticCapture but uses root.ExecuteContext, returning app.Out plus the
// error. The poll cadence is already shrunk to milliseconds by testApp.
func runRegisterCtx(ctx context.Context, t *testing.T, args ...string) (string, error) {
	t.Helper()
	app := testApp(t)
	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs(args)
	err := root.ExecuteContext(ctx)
	return out.String(), err
}

// TestRegister_ContextCancelExitsPromptly pins the cancellation branch of the
// approval poll loop (review round-3 #8 F1: `case <-ctx.Done(): return
// ctx.Err()` at register_flow.go was uncovered). A never-approving pending
// server keeps the loop spinning; cancelling the command context must make
// register return ctx.Err() quickly instead of waiting out the full --timeout.
func TestRegister_ContextCancelExitsPromptly(t *testing.T) {
	withXDG(t)

	var polls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/register":
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"client_id":"agnt_boot","status":"pending"}`))
		case "/oauth/token":
			polls.Add(1) // always pending → the loop never exits on its own
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"type":"invalid_grant","status":400,"detail":"agent pending approval","instance":"/oauth/token"}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)

	ctx, cancel := context.WithCancel(context.Background())
	// Cancel shortly after the loop has certainly started polling (cadence is
	// ~2ms in tests), well before the generous --timeout below would fire.
	go func() {
		time.Sleep(50 * time.Millisecond)
		cancel()
	}()

	done := make(chan error, 1)
	go func() {
		_, err := runRegisterCtx(ctx, t, "register", "--url", srv.URL, "--name", "crawler", "--env", "qa", "--timeout", "30s")
		done <- err
	}()

	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("cancel must surface context.Canceled, got %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("register did not return promptly after context cancel (the ctx.Done branch is not honoured)")
	}
}

// TestRegister_AssertionInvalidNoClaimIsFatal pins the non-claim
// assertion-invalid branch (review round-3 #8 F1 case (d)): with NO claim
// token outstanding, an "Assertion is invalid" token-exchange failure is a hard
// NOT_AUTHENTICATED (usually an audience mismatch) — the CLI must abort with the
// coded error immediately, never treat it as pending and hang.
func TestRegister_AssertionInvalidNoClaimIsFatal(t *testing.T) {
	withXDG(t)

	var polls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/register":
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusCreated)
			// No claim_token → claimPending=false → assertion-invalid is fatal.
			_, _ = w.Write([]byte(`{"client_id":"agnt_boot","status":"pending"}`))
		case "/oauth/token":
			polls.Add(1)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"type":"invalid_grant","status":400,"detail":"Assertion is invalid","instance":"/oauth/token"}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)

	// A generous --timeout: if the classifier wrongly treated this as pending it
	// would poll until this fires; the test's own deadline would then trip first.
	_, err := runJenticCapture(t, "register", "--url", srv.URL, "--name", "crawler", "--env", "qa", "--timeout", "30s")
	if err == nil {
		t.Fatal("a non-claim assertion-invalid must be fatal, not pending")
	}
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("error is not coded: %v", err)
	}
	if coded.Code != ux.CodeNotAuthenticated {
		t.Errorf("code = %q, want NOT_AUTHENTICATED (audience-mismatch abort, no hang)", coded.Code)
	}
	// The very first exchange decides it; the loop must not have spun.
	if got := polls.Load(); got != 1 {
		t.Errorf("token exchange hit %d times, want exactly 1 (must abort before polling)", got)
	}
}

// TestRegister_PendingTimeoutCarriesApproveURL pins the non-claim timeout branch
// (review round-3 #8 F1): a pending agent that is never approved times out as
// TIMEOUT_PENDING (exit 3) and the envelope details MUST carry approve_url +
// agent_id so an agent that timed out can still route a human to approve
// (AGT-4/AGT-21).
func TestRegister_PendingTimeoutCarriesApproveURL(t *testing.T) {
	withXDG(t)
	srv, _ := bootstrapServer(t, 1_000_000) // effectively always pending

	_, err := runJenticCapture(t, "register", "--url", srv.URL, "--name", "crawler", "--env", "qa", "--timeout", "20ms")
	if err == nil {
		t.Fatal("expected a pending timeout, got success")
	}
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("error is not coded: %v", err)
	}
	if coded.Code != ux.CodeTimeoutPending {
		t.Fatalf("code = %q, want TIMEOUT_PENDING", coded.Code)
	}
	if coded.Details["agent_id"] != "agnt_boot" {
		t.Errorf("details.agent_id = %v, want agnt_boot", coded.Details["agent_id"])
	}
	approve, _ := coded.Details["approve_url"].(string)
	if approve == "" || !strings.Contains(approve, "agnt_boot") {
		t.Errorf("details.approve_url = %q, want a console URL naming the agent so a timed-out agent can route a human", approve)
	}
	// A non-claim timeout must NOT advertise a claim_url (nothing to claim).
	if _, ok := coded.Details["claim_url"]; ok {
		t.Errorf("non-claim timeout should not carry claim_url, details = %v", coded.Details)
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
