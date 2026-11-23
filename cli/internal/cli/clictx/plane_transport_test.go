package clictx

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
)

func generateSelfSignedCert(t *testing.T, commonName string) ([]byte, tls.Certificate) {
	t.Helper()
	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("generate key: %v", err)
	}
	tmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: commonName},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(time.Hour),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		IPAddresses:           []net.IP{net.ParseIP("127.0.0.1")},
		DNSNames:              []string{"localhost", commonName},
		BasicConstraintsValid: true,
		IsCA:                  true,
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &priv.PublicKey, priv)
	if err != nil {
		t.Fatalf("create certificate: %v", err)
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyDER, err := x509.MarshalECPrivateKey(priv)
	if err != nil {
		t.Fatalf("marshal private key: %v", err)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER})
	tlsCert, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil {
		t.Fatalf("x509 key pair: %v", err)
	}
	tlsCert.Leaf, err = x509.ParseCertificate(der)
	if err != nil {
		t.Fatalf("parse leaf cert: %v", err)
	}
	return certPEM, tlsCert
}

func writeSrvCertPEM(t *testing.T, srv *httptest.Server) string {
	t.Helper()
	pemPath := filepath.Join(t.TempDir(), "ca.pem")
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: srv.Certificate().Raw})
	if err := os.WriteFile(pemPath, pemBytes, 0o600); err != nil {
		t.Fatalf("write ca bundle: %v", err)
	}
	return pemPath
}

func TestPlaneTransport_PreservesDefaultTransportPosture(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer srv.Close()
	pemPath := writeSrvCertPEM(t, srv)

	hc, err := AuthHTTPClient(context.Background(), pemPath)
	if err != nil {
		t.Fatalf("AuthHTTPClient: %v", err)
	}
	pinned, ok := hc.Transport.(*http.Transport)
	if !ok {
		t.Fatalf("transport = %T, want *http.Transport", hc.Transport)
	}
	def := http.DefaultTransport.(*http.Transport)

	if pinned.Proxy == nil {
		t.Error("Proxy is nil, want non-nil default proxy function")
	}
	if pinned.TLSHandshakeTimeout != def.TLSHandshakeTimeout {
		t.Errorf("TLSHandshakeTimeout = %v, want %v", pinned.TLSHandshakeTimeout, def.TLSHandshakeTimeout)
	}
	if pinned.IdleConnTimeout != def.IdleConnTimeout {
		t.Errorf("IdleConnTimeout = %v, want %v", pinned.IdleConnTimeout, def.IdleConnTimeout)
	}
	if pinned.ForceAttemptHTTP2 != def.ForceAttemptHTTP2 {
		t.Errorf("ForceAttemptHTTP2 = %v, want %v", pinned.ForceAttemptHTTP2, def.ForceAttemptHTTP2)
	}
	if pinned.TLSClientConfig == nil {
		t.Fatal("TLSClientConfig is nil")
	}
	if pinned.TLSClientConfig.MinVersion != tls.VersionTLS12 {
		t.Errorf("MinVersion = %#x, want %#x", pinned.TLSClientConfig.MinVersion, tls.VersionTLS12)
	}
	if pinned.TLSClientConfig.RootCAs == nil {
		t.Fatal("RootCAs is nil")
	}
	if _, err := srv.Certificate().Verify(x509.VerifyOptions{Roots: pinned.TLSClientConfig.RootCAs}); err != nil {
		t.Errorf("bundle cert did not verify against RootCAs: %v", err)
	}
	_, otherCert := generateSelfSignedCert(t, "other.example")
	if _, err := otherCert.Leaf.Verify(x509.VerifyOptions{Roots: pinned.TLSClientConfig.RootCAs}); err == nil {
		t.Error("unrelated cert verified against RootCAs; want only the configured bundle")
	}
}

func TestPlaneTransport_ReusedForSameBundle(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer srv.Close()
	pemPath := writeSrvCertPEM(t, srv)

	hc1, err := AuthHTTPClient(context.Background(), pemPath)
	if err != nil {
		t.Fatalf("first AuthHTTPClient: %v", err)
	}
	hc2, err := AuthHTTPClient(context.Background(), pemPath)
	if err != nil {
		t.Fatalf("second AuthHTTPClient: %v", err)
	}
	if hc1 == hc2 {
		t.Fatal("AuthHTTPClient must return a fresh *http.Client per call")
	}
	if hc1.Transport != hc2.Transport {
		t.Fatalf("Transport pointers differ across calls for identical bundle: %p vs %p", hc1.Transport, hc2.Transport)
	}

	otherPEM, _ := generateSelfSignedCert(t, "rotated.example")
	if err := os.WriteFile(pemPath, otherPEM, 0o600); err != nil {
		t.Fatalf("rewrite ca bundle: %v", err)
	}
	hc3, err := AuthHTTPClient(context.Background(), pemPath)
	if err != nil {
		t.Fatalf("third AuthHTTPClient after bundle rotation: %v", err)
	}
	if hc3.Transport == hc1.Transport {
		t.Fatal("expected a distinct Transport pointer after rotating the PEM bundle on disk")
	}

	// Evict hc1's transport by filling past planeTransportCacheMax (4 entries).
	for i := range planeTransportCacheMax {
		extraPEM, _ := generateSelfSignedCert(t, "extra.example")
		if err := os.WriteFile(pemPath, extraPEM, 0o600); err != nil {
			t.Fatalf("write extra bundle %d: %v", i, err)
		}
		if _, err := AuthHTTPClient(context.Background(), pemPath); err != nil {
			t.Fatalf("AuthHTTPClient for extra bundle %d: %v", i, err)
		}
	}
	origPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: srv.Certificate().Raw})
	if err := os.WriteFile(pemPath, origPEM, 0o600); err != nil {
		t.Fatalf("restore original ca bundle: %v", err)
	}
	hc4, err := AuthHTTPClient(context.Background(), pemPath)
	if err != nil {
		t.Fatalf("AuthHTTPClient after cache eviction: %v", err)
	}
	if hc4.Transport == hc1.Transport {
		t.Fatal("expected a fresh Transport pointer after cache eviction")
	}
}

func TestPlaneTransport_FailsClosedAfterBundleDeleted(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer srv.Close()
	pemPath := writeSrvCertPEM(t, srv)

	if _, err := AuthHTTPClient(context.Background(), pemPath); err != nil {
		t.Fatalf("initial AuthHTTPClient: %v", err)
	}
	if err := os.Remove(pemPath); err != nil {
		t.Fatalf("remove ca bundle: %v", err)
	}
	if _, err := AuthHTTPClient(context.Background(), pemPath); err == nil {
		t.Fatal("second AuthHTTPClient must fail closed after bundle is deleted from disk (SEC-20)")
	}
}

func TestPlaneTransport_FailsClosedOnGarbageBundle(t *testing.T) {
	pemPath := filepath.Join(t.TempDir(), "garbage.pem")
	if err := os.WriteFile(pemPath, []byte("not-a-valid-pem-bundle"), 0o600); err != nil {
		t.Fatalf("write garbage bundle: %v", err)
	}
	_, err := AuthHTTPClient(context.Background(), pemPath)
	if err == nil {
		t.Fatal("AuthHTTPClient must fail closed on a non-PEM bundle (SEC-20)")
	}
	if !strings.Contains(err.Error(), "contains no usable PEM certificate") {
		t.Errorf("error = %q, want 'contains no usable PEM certificate'", err.Error())
	}
}

func TestPlaneTransport_HonorsHTTPSProxy(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer srv.Close()
	pemPath := writeSrvCertPEM(t, srv)

	var connects atomic.Int32
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodConnect {
			connects.Add(1)
		}
		w.WriteHeader(http.StatusBadGateway)
	}))
	defer proxy.Close()

	t.Setenv("HTTPS_PROXY", proxy.URL)
	t.Setenv("https_proxy", proxy.URL)
	t.Setenv("NO_PROXY", "")
	t.Setenv("no_proxy", "")

	hc, err := AuthHTTPClient(context.Background(), pemPath)
	if err != nil {
		t.Fatalf("AuthHTTPClient: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "https://192.0.2.1:8443/probe", nil)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}
	if resp, err := hc.Do(req); err == nil {
		_ = resp.Body.Close()
	}
	if got := connects.Load(); got != 1 {
		t.Fatalf("proxy CONNECT count = %d, want 1", got)
	}
}

func TestPlaneTransport_RejectsServerOutsideBundle(t *testing.T) {
	srv1 := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv1.Close()
	pemPath := writeSrvCertPEM(t, srv1)

	_, untrustedCert := generateSelfSignedCert(t, "untrusted.example")
	srv2 := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	srv2.TLS = &tls.Config{Certificates: []tls.Certificate{untrustedCert}, MinVersion: tls.VersionTLS12}
	srv2.StartTLS()
	defer srv2.Close()

	hc, err := AuthHTTPClient(context.Background(), pemPath)
	if err != nil {
		t.Fatalf("AuthHTTPClient: %v", err)
	}
	resp, err := hc.Get(srv1.URL)
	if err != nil {
		t.Fatalf("GET pinned server: %v", err)
	}
	_ = resp.Body.Close()

	if resp2, err := hc.Get(srv2.URL); err == nil {
		_ = resp2.Body.Close()
		t.Fatal("GET server outside pinned bundle succeeded; want x509 verification failure")
	} else if !strings.Contains(err.Error(), "x509:") && !strings.Contains(err.Error(), "certificate") {
		t.Errorf("GET server outside bundle error = %v, want x509 certificate error", err)
	}
}

func TestPlaneTransport_NoGoroutineGrowth(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("ok"))
	}))
	defer srv.Close()
	pemPath := writeSrvCertPEM(t, srv)

	before := runtime.NumGoroutine()
	for range 50 {
		hc, err := AuthHTTPClient(context.Background(), pemPath)
		if err != nil {
			t.Fatalf("AuthHTTPClient: %v", err)
		}
		resp, err := hc.Get(srv.URL)
		if err != nil {
			t.Fatalf("GET: %v", err)
		}
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
	}
	after := runtime.NumGoroutine()
	if after > before+10 {
		t.Fatalf("goroutines grew from %d to %d across 50 calls (want at most %d)", before, after, before+10)
	}
}

func TestNoFollowRedirects_RawPlaneClients(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/redirect" {
			http.Redirect(w, r, "/target", http.StatusFound)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	ctx := WithActiveState(context.Background(), &ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			BaseURL:   srv.URL,
			BrokerURL: srv.URL,
		},
		Mode: ModeAgent,
	})

	clients := []struct {
		name  string
		build func() (*http.Client, error)
	}{
		{"AuthHTTPClient", func() (*http.Client, error) { return AuthHTTPClient(ctx, "") }},
		{"ControlHTTPClient", func() (*http.Client, error) { return ControlHTTPClient(ctx) }},
		{"BrokerHTTPClient", func() (*http.Client, error) { return BrokerHTTPClient(ctx) }},
	}

	for _, tc := range clients {
		t.Run(tc.name, func(t *testing.T) {
			hc, err := tc.build()
			if err != nil {
				t.Fatalf("%s: %v", tc.name, err)
			}
			resp, err := hc.Get(srv.URL + "/redirect")
			if err != nil {
				t.Fatalf("%s GET /redirect: %v", tc.name, err)
			}
			_ = resp.Body.Close()
			if resp.StatusCode != http.StatusFound {
				t.Errorf("%s status = %d, want %d (redirect must not be followed)", tc.name, resp.StatusCode, http.StatusFound)
			}
		})
	}
}
