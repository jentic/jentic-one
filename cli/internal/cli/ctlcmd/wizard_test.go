package ctlcmd

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
)

// Test that setupRequired reads the signal from the path that actually carries
// it. The combined app exposes a generic root /health (no setup_required) plus
// /admin/health (the real signal); probing /health first must NOT be mistaken
// for "no setup needed".
func TestSetupRequired_CombinedModePrefersAdminHealth(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/admin/health":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"status":"ok","surface":"admin","setup_required":true}`))
		case "/health":
			// Combined-app root liveness probe — no setup_required field.
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"status":"ok"}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	got, err := setupRequired(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !got {
		t.Fatalf("setupRequired = false, want true (admin/health reports setup_required:true)")
	}
}

func TestSetupRequired_Standalone(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"status":"ok","setup_required":false}`))
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	got, err := setupRequired(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got {
		t.Fatalf("setupRequired = true, want false")
	}
}

// A 200 that never carries setup_required (only a generic liveness probe) must
// surface an error rather than silently returning false.
func TestSetupRequired_NoSignalAnywhere(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	}))
	defer srv.Close()

	if _, err := setupRequired(context.Background(), srv.URL); err == nil {
		t.Fatalf("expected error when no path carries setup_required, got nil")
	}
}

// adminAlreadyExists must require TWO consistent reads before concluding an
// admin exists, so a startup-race blip (DB momentarily unreachable, where the
// admin /health endpoint fails open with setup_required:false) cannot make the
// wizard skip account creation on a fresh box.
func TestAdminAlreadyExists_RequiresTwoConsistentReads(t *testing.T) {
	// Shrink the confirm delay so the test does not sleep for real.
	orig := adminConfirmDelay
	adminConfirmDelay = time.Millisecond
	defer func() { adminConfirmDelay = orig }()

	app := &app{App: &cmdcore.App{}}

	t.Run("stable false stays false (admin really exists)", func(t *testing.T) {
		srv := httptest.NewServer(adminHealthHandler(`{"status":"ok","surface":"admin","setup_required":false}`))
		defer srv.Close()
		if !app.adminAlreadyExists(context.Background(), srv.URL) {
			t.Fatalf("adminAlreadyExists = false, want true for two stable false reads")
		}
	})

	t.Run("first false then true is treated as not-existing (race blip)", func(t *testing.T) {
		var n int
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/admin/health" {
				w.WriteHeader(http.StatusNotFound)
				return
			}
			n++
			w.Header().Set("Content-Type", "application/json")
			if n == 1 {
				_, _ = w.Write([]byte(`{"status":"ok","surface":"admin","setup_required":false}`))
			} else {
				_, _ = w.Write([]byte(`{"status":"ok","surface":"admin","setup_required":true}`))
			}
		}))
		defer srv.Close()
		if app.adminAlreadyExists(context.Background(), srv.URL) {
			t.Fatalf("adminAlreadyExists = true, want false when confirm read flips to setup_required:true")
		}
	})

	t.Run("first true short-circuits to not-existing", func(t *testing.T) {
		srv := httptest.NewServer(adminHealthHandler(`{"status":"ok","surface":"admin","setup_required":true}`))
		defer srv.Close()
		if app.adminAlreadyExists(context.Background(), srv.URL) {
			t.Fatalf("adminAlreadyExists = true, want false when setup is required")
		}
	})
}

func adminHealthHandler(body string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/admin/health" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(body))
	}
}

// healthHandler answers GET /health with the given status (200 carries a body so
// serverinfo.Probe reports Running=true; any other status reports it down).
func healthHandler(status int) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		if status != http.StatusOK {
			w.WriteHeader(status)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok","version":"1.2.3"}`))
	}
}

// fastPolls shrinks an app's poll cadence so wait-loop tests don't sleep for
// real (AR2-5: cadence is per-App, not a package global).
func fastPolls(a *app) {
	a.SetPollCadence(time.Millisecond, 2*time.Millisecond, time.Millisecond)
}

// An already-running stack is detected on the first probe, instantly, with no
// waiting — the common `jenticctl wizard` re-run case.
func TestWizardWaitForStack_ImmediateUp(t *testing.T) {
	srv := httptest.NewServer(healthHandler(http.StatusOK))
	defer srv.Close()

	app := &app{App: &cmdcore.App{Out: io.Discard}}
	if !app.wizardWaitForStack(context.Background(), srv.URL) {
		t.Fatalf("wizardWaitForStack = false, want true for an already-running stack")
	}
}

// Regression (#697): the install → wizard handoff must wait out a cold start
// instead of a single short probe — the stack is down for the first probes
// (still booting after `compose up -d`) then becomes healthy.
func TestWizardWaitForStack_WaitsOutColdStart(t *testing.T) {
	var probes int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		// Down for the first two probes (cold start), healthy thereafter.
		if atomic.AddInt32(&probes, 1) < 3 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok","version":"1.2.3"}`))
	}))
	defer srv.Close()

	app := &app{App: &cmdcore.App{Out: io.Discard}}
	fastPolls(app)
	if !app.wizardWaitForStack(context.Background(), srv.URL) {
		t.Fatalf("wizardWaitForStack = false, want true once the stack finishes booting")
	}
	if got := atomic.LoadInt32(&probes); got < 3 {
		t.Fatalf("probes = %d, want >= 3 (should have retried past the cold start)", got)
	}
}

// When the stack never becomes ready, step 0 gives up at the deadline (false)
// rather than blocking forever — the caller then prints the recovery guidance.
func TestWizardWaitForStack_TimesOutWhenNeverReady(t *testing.T) {
	orig := wizardStackReadyTimeout
	wizardStackReadyTimeout = 15 * time.Millisecond
	t.Cleanup(func() { wizardStackReadyTimeout = orig })

	srv := httptest.NewServer(healthHandler(http.StatusServiceUnavailable))
	defer srv.Close()

	app := &app{App: &cmdcore.App{Out: io.Discard}}
	fastPolls(app)
	if app.wizardWaitForStack(context.Background(), srv.URL) {
		t.Fatalf("wizardWaitForStack = true, want false when the stack never becomes ready")
	}
}

// A cancelled context aborts the wait promptly (false), so Ctrl-C during the
// install → wizard handoff doesn't hang.
func TestWizardWaitForStack_ContextCancel(t *testing.T) {
	srv := httptest.NewServer(healthHandler(http.StatusServiceUnavailable))
	defer srv.Close()

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	app := &app{App: &cmdcore.App{Out: io.Discard}}
	fastPolls(app)
	if app.wizardWaitForStack(ctx, srv.URL) {
		t.Fatalf("wizardWaitForStack = true, want false when the context is cancelled")
	}
}
