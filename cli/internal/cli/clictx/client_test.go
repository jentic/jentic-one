package clictx

import (
	"context"
	"encoding/pem"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
)

// TestConfigFromState_MapsResolvedState verifies the ActiveState -> client.Config
// projection carries the control/broker URLs and identity/environment through.
func TestConfigFromState_MapsResolvedState(t *testing.T) {
	state := &ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:        "ci",
			EnvironmentName:     "prod",
			BaseURL:             "https://ctl.example",
			BrokerURL:           "https://brk.example",
			InjectedBearerToken: "at_x",
		},
		Mode: ModeAgent,
	}
	cfg, err := configFromState(state)
	if err != nil {
		t.Fatalf("configFromState: %v", err)
	}
	if cfg.ControlBaseURL != "https://ctl.example" || cfg.BrokerBaseURL != "https://brk.example" {
		t.Errorf("urls = %q/%q", cfg.ControlBaseURL, cfg.BrokerBaseURL)
	}
	if cfg.IdentityName != "ci" || cfg.EnvironmentName != "prod" {
		t.Errorf("identity/env = %q/%q", cfg.IdentityName, cfg.EnvironmentName)
	}
	if cfg.InjectedBearerToken != "at_x" {
		t.Errorf("injected token = %q", cfg.InjectedBearerToken)
	}
}

// TestConfigFromState_BadCACertFailsClosed pins SEC-20: when ca_cert_path is set
// but the bundle can't be loaded, configFromState must ERROR (fail closed)
// rather than silently drop to system roots — the operator's explicit trust
// decision must not be downgraded without a word.
func TestConfigFromState_BadCACertFailsClosed(t *testing.T) {
	// A path that doesn't exist.
	state := &ActiveState{ResolvedState: &sdkconfig.ResolvedState{
		BaseURL:    "https://ctl.example",
		CACertPath: "/definitely/not/a/real/ca-bundle.pem",
	}}
	if _, err := configFromState(state); err == nil {
		t.Fatal("configFromState must fail closed when ca_cert_path is unreadable (SEC-20)")
	}

	// A path that exists but is not a valid PEM.
	f := t.TempDir() + "/bad.pem"
	if err := os.WriteFile(f, []byte("not a certificate"), 0o600); err != nil {
		t.Fatal(err)
	}
	state.CACertPath = f
	if _, err := configFromState(state); err == nil {
		t.Fatal("configFromState must fail closed when ca_cert_path has no usable cert (SEC-20)")
	}

	// Empty path is fine (no custom CA → SDK default).
	state.CACertPath = ""
	if _, err := configFromState(state); err != nil {
		t.Fatalf("empty ca_cert_path must not error: %v", err)
	}
}

// TestGetControlClient_RequiresState: without an ActiveState in context (the root
// interceptor never ran) the adapter errors rather than building a half-configured
// client.
func TestGetControlClient_RequiresState(t *testing.T) {
	if _, err := GetControlClient(context.Background()); err == nil {
		t.Fatal("expected an error with no ActiveState in context")
	}
}

// TestGetClients_NilResolvedStateErrors is the AGT-1 regression: the root
// interceptor's no-config degrade path used to inject an ActiveState with a nil
// embedded *ResolvedState, and every client getter then panicked (nil-deref, Go
// runtime exit 2 — colliding with ExitDenied). All three getters must return an
// actionable error, never panic.
func TestGetClients_NilResolvedStateErrors(t *testing.T) {
	ctx := WithActiveState(context.Background(), &ActiveState{Mode: ModeAgent})
	if _, err := GetControlClient(ctx); err == nil {
		t.Fatal("GetControlClient: expected an error for nil ResolvedState")
	}
	if _, err := GetControlRawClient(ctx); err == nil {
		t.Fatal("GetControlRawClient: expected an error for nil ResolvedState")
	}
}

// TestGetControlClient_BuildsWithState: with a control URL present the adapter
// constructs a client.
func TestGetControlClient_BuildsWithState(t *testing.T) {
	state := &ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{BaseURL: "https://ctl.example"},
		Mode:          ModeHuman,
	}
	ctx := WithActiveState(context.Background(), state)
	c, err := GetControlClient(ctx)
	if err != nil {
		t.Fatalf("GetControlClient: %v", err)
	}
	if c == nil {
		t.Fatal("nil control client")
	}
}

// markerTransport tags a wrapped RoundTripper so tests can prove the hook
// composed over (never displaced) the resolved base transport.
type markerTransport struct{ base http.RoundTripper }

func (m markerTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	return m.base.RoundTrip(req)
}

// TestAuthHTTPClient_PinnedAndHooked pins the #1205 mint-transport builder:
// the client the RFC 7523 exchange rides must carry the SEC-20 CA-pinned
// transport (fail closed on a broken bundle) with the context's TransportHook
// composed over it — the same construction path every plane client uses.
func TestAuthHTTPClient_PinnedAndHooked(t *testing.T) {
	// A set-but-broken bundle fails closed (SEC-20), exactly like the
	// generated clients.
	if _, err := AuthHTTPClient(context.Background(), "/definitely/not/a/real/ca-bundle.pem"); err == nil {
		t.Fatal("AuthHTTPClient must fail closed on an unreadable ca_cert_path (SEC-20)")
	}

	// A valid bundle yields a transport pinned to that pool.
	srv := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer srv.Close()
	pemPath := filepath.Join(t.TempDir(), "ca.pem")
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: srv.Certificate().Raw})
	if err := os.WriteFile(pemPath, pemBytes, 0o600); err != nil {
		t.Fatalf("write ca bundle: %v", err)
	}
	hc, err := AuthHTTPClient(context.Background(), pemPath)
	if err != nil {
		t.Fatalf("AuthHTTPClient with a valid bundle: %v", err)
	}
	pinned, ok := hc.Transport.(*http.Transport)
	if !ok || pinned.TLSClientConfig == nil || pinned.TLSClientConfig.RootCAs == nil {
		t.Fatalf("transport = %T, want an *http.Transport pinned to the custom CA pool", hc.Transport)
	}

	// The context's TransportHook composes OVER the pinned transport: the hook
	// receives the pinning decision as its base (wrap, never displace).
	var hookBase http.RoundTripper
	ctx := WithTransportHook(context.Background(), func(base http.RoundTripper) http.RoundTripper {
		hookBase = base
		return markerTransport{base: base}
	})
	hc, err = AuthHTTPClient(ctx, pemPath)
	if err != nil {
		t.Fatalf("AuthHTTPClient with hook: %v", err)
	}
	if _, ok := hc.Transport.(markerTransport); !ok {
		t.Fatalf("transport = %T, want the hook's wrapper", hc.Transport)
	}
	base, ok := hookBase.(*http.Transport)
	if !ok || base.TLSClientConfig == nil || base.TLSClientConfig.RootCAs == nil {
		t.Errorf("hook base = %T, want the CA-pinned transport as the inner RoundTripper", hookBase)
	}

	// No bundle and no hook: a plain default client (system roots).
	hc, err = AuthHTTPClient(context.Background(), "")
	if err != nil {
		t.Fatalf("AuthHTTPClient with no bundle: %v", err)
	}
	if hc.Transport != nil {
		t.Errorf("transport = %T, want nil (default transport on system roots)", hc.Transport)
	}
	// #1207: every client this constructor hands out — the mint, the broker
	// leg, the raw control fetches — refuses to follow redirects.
	if hc.CheckRedirect == nil {
		t.Fatal("AuthHTTPClient must refuse redirects (#1207)")
	}
	if rerr := hc.CheckRedirect(nil, nil); !errors.Is(rerr, http.ErrUseLastResponse) {
		t.Errorf("CheckRedirect returned %v, want http.ErrUseLastResponse", rerr)
	}
}
