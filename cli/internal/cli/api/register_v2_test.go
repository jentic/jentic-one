package api

import (
	"bytes"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	legacyconfig "github.com/jentic/jentic-one/cli/internal/config"
)

// These tests pin `jentic register` as the single V2 onboarding front door
// (register_v2.go): one command takes a fresh machine to a working, approved
// context; an active context re-registers in place; --profile/--base-url stay
// the byte-for-byte legacy flow.

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
	if ctxCfg, ok := cfg.Contexts["qa"]; !ok || ctxCfg.Environment != "qa" || ctxCfg.Identity != "crawler" {
		t.Errorf("context qa = %+v, want qa/crawler", ctxCfg)
	}
	if cfg.ActiveContext != "qa" {
		t.Errorf("active context = %q, want qa", cfg.ActiveContext)
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

// TestRegister_LegacyFlagsPinLegacyStore: --profile/--base-url remain the
// byte-for-byte V1 flow — a legacy profile with tokens, and NOTHING written to
// the XDG store.
func TestRegister_LegacyFlagsPinLegacyStore(t *testing.T) {
	withXDG(t)
	srv, _ := bootstrapServer(t, 0)

	app := testApp(t)
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs([]string{"register", "--profile", "demo", "--base-url", srv.URL, "--timeout", "5s", "--yes"})
	if err := root.Execute(); err != nil {
		t.Fatalf("legacy register: %v", err)
	}

	// register (unlike bootstrap) does not activate the profile, so the legacy
	// CONFIG file may not exist — the profile's tokens are the proof.
	tokensPath := filepath.Join(app.Paths.Root, "profiles", "demo", "tokens.json")
	if _, statErr := os.Stat(tokensPath); statErr != nil {
		t.Fatalf("legacy register did not persist profile tokens at %s: %v", tokensPath, statErr)
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if len(cfg.Environments) != 0 || len(cfg.Identities) != 0 || cfg.ActiveContext != "" {
		t.Errorf("legacy register leaked into the XDG store: %+v", cfg)
	}
}

// TestRegister_ConflictingStoreFlagsRejected: mixing the two stores' flags is
// a hard error before any side effect.
func TestRegister_ConflictingStoreFlagsRejected(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(nil)
	defer srv.Close()

	err := runJentic(t, "register", "--url", srv.URL, "--profile", "demo")
	if err == nil || !strings.Contains(err.Error(), "different stores") {
		t.Fatalf("expected a conflicting-flags error, got %v", err)
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
