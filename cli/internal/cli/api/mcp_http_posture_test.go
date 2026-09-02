package api

// mcp_http_posture_test.go pins the daemon's bind rules (the acceptance
// box's "non-loopback bind refuses without TLS+token"), the idle-exit
// watchdog, the unix-socket peer-credential boundary, and the
// socket-activation listener adoption.

import (
	"context"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func writeTokenFile(t *testing.T, mode os.FileMode) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(path, []byte("tok_secret\n"), mode); err != nil {
		t.Fatalf("write token file: %v", err)
	}
	return path
}

// shortSocketPath returns a socket path short enough for the platform limit
// (~104 bytes on macOS) — t.TempDir()'s test-name-derived paths overflow it.
func shortSocketPath(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("", "jmcp")
	if err != nil {
		t.Fatalf("mkdtemp: %v", err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	return filepath.Join(dir, "s.sock")
}

func TestResolveMCPBindPosture(t *testing.T) {
	token := writeTokenFile(t, 0o600)
	cases := []struct {
		name    string
		opts    mcpHTTPOptions
		wantErr string // substring; empty = must succeed
	}{
		{"non-loopback without opt-in refuses", mcpHTTPOptions{listen: "0.0.0.0:9999"}, "--allow-non-loopback"},
		{"non-loopback without TLS refuses", mcpHTTPOptions{listen: "10.0.0.5:9999", allowNonLoopback: true, tokenFile: token}, "without TLS"},
		{"non-loopback without token refuses", mcpHTTPOptions{listen: "10.0.0.5:9999", allowNonLoopback: true, tlsCert: "c.pem", tlsKey: "k.pem"}, "--token-file"},
		{"non-loopback with TLS+token serves", mcpHTTPOptions{listen: "10.0.0.5:9999", allowNonLoopback: true, tlsCert: "c.pem", tlsKey: "k.pem", tokenFile: token}, ""},
		{"loopback-prefixed DNS name is not loopback", mcpHTTPOptions{listen: "127.0.0.1.evil.example:9999"}, "--allow-non-loopback"},
		{"loopback without token refuses", mcpHTTPOptions{listen: "127.0.0.1:9999"}, "--token-file"},
		{"loopback with token serves", mcpHTTPOptions{listen: "127.0.0.1:9999", tokenFile: token}, ""},
		{"loopback opt-out serves tokenless", mcpHTTPOptions{listen: "localhost:9999", allowUnauthenticated: true}, ""},
		{"non-loopback never serves unauthenticated", mcpHTTPOptions{listen: "10.0.0.5:9999", allowNonLoopback: true, tlsCert: "c.pem", tlsKey: "k.pem", tokenFile: token, allowUnauthenticated: true}, "loopback-only"},
		{"half a TLS pair refuses", mcpHTTPOptions{listen: "127.0.0.1:9999", tokenFile: token, tlsCert: "c.pem"}, "together"},
		{"unix socket serves credential-less", mcpHTTPOptions{socket: "/tmp/x.sock"}, ""},
		{"unauthenticated opt-out is TCP-only", mcpHTTPOptions{socket: "/tmp/x.sock", allowUnauthenticated: true}, "--listen only"},
		{"listen and socket are exclusive", mcpHTTPOptions{listen: "127.0.0.1:1", socket: "/tmp/x.sock"}, "mutually exclusive"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			posture, err := resolveMCPBindPosture(&tc.opts)
			if tc.wantErr == "" {
				if err != nil {
					t.Fatalf("want success, got %v", err)
				}
				if posture == nil || posture.network == "" {
					t.Fatalf("posture = %+v, want a resolved transport", posture)
				}
				return
			}
			if err == nil {
				t.Fatalf("want a refusal mentioning %q, got posture %+v", tc.wantErr, posture)
			}
			if !strings.Contains(err.Error(), tc.wantErr) {
				t.Errorf("refusal %q must mention %q", err.Error(), tc.wantErr)
			}
		})
	}
}

func TestReadMCPTokenFile_RefusesLooseModes(t *testing.T) {
	if _, err := readMCPTokenFile(writeTokenFile(t, 0o644)); err == nil {
		t.Error("a group/world-readable token file must be refused")
	}
	token, err := readMCPTokenFile(writeTokenFile(t, 0o600))
	if err != nil {
		t.Fatalf("0600 token file: %v", err)
	}
	if string(token) != "tok_secret" {
		t.Errorf("token = %q, want the trimmed file contents", token)
	}
}

// TestIdleWatchdog_FiresAndResets pins the idle-exit acceptance box: the
// watchdog fires after the quiet period, and an in-flight request holds it
// open.
func TestIdleWatchdog_FiresAndResets(t *testing.T) {
	w := newIdleWatchdog(80 * time.Millisecond)
	handler := w.track(http.HandlerFunc(func(_ http.ResponseWriter, _ *http.Request) {
		time.Sleep(120 * time.Millisecond) // longer than the timeout: in-flight must hold it open
	}))

	done := make(chan struct{})
	go func() {
		defer close(done)
		req, _ := http.NewRequestWithContext(context.Background(), http.MethodPost, "/mcp", nil)
		handler.ServeHTTP(nopResponseWriter{}, req)
	}()
	select {
	case <-w.fired():
		t.Fatal("the watchdog fired while a request was in flight")
	case <-done:
	}

	select {
	case <-w.fired():
	case <-time.After(2 * time.Second):
		t.Fatal("the watchdog never fired after the idle period")
	}
}

type nopResponseWriter struct{}

func (nopResponseWriter) Header() http.Header         { return http.Header{} }
func (nopResponseWriter) Write(b []byte) (int, error) { return len(b), nil }
func (nopResponseWriter) WriteHeader(int)             {}

// TestMCPHTTP_UnixSocketPeerCred proves the OS-identity boundary over a real
// unix socket: the daemon's own uid connects and is served; a listener whose
// allowlist excludes this uid closes the connection before HTTP happens.
func TestMCPHTTP_UnixSocketPeerCred(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	serve := func(t *testing.T, allowed map[uint32]bool) string {
		t.Helper()
		sock := shortSocketPath(t)
		ln, err := net.Listen("unix", sock)
		if err != nil {
			t.Fatalf("bind unix socket: %v", err)
		}
		s := newTestMCPServer(t, nil)
		handler := s.httpHandler(context.Background(), &mcpBindPosture{network: "unix"}, nil, newIdleWatchdog(0), discardLogger())
		srv := &http.Server{Handler: handler, ReadHeaderTimeout: 5 * time.Second}
		go func() { _ = srv.Serve(&peerCredListener{Listener: ln, allowed: allowed, logger: discardLogger()}) }()
		t.Cleanup(func() { _ = srv.Close() })
		return sock
	}

	client := func(sock string) *http.Client {
		return &http.Client{
			Timeout: 5 * time.Second,
			Transport: &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				var d net.Dialer
				return d.DialContext(ctx, "unix", sock)
			}},
		}
	}

	t.Run("an allowed uid is served", func(t *testing.T) {
		sock := serve(t, allowedPeerUIDSet(nil)) // own uid + root
		resp, err := client(sock).Post("http://jentic-mcp"+mcpHTTPPath, "application/json", nil)
		if err != nil {
			t.Fatalf("POST over the socket: %v", err)
		}
		_ = resp.Body.Close()
		// The empty body is a 400 from the SDK transport — what matters is
		// that HTTP happened at all (the peer check admitted us).
		if resp.StatusCode == 0 {
			t.Errorf("no HTTP response through the peer-cred listener")
		}
	})

	t.Run("a disallowed uid is cut before HTTP", func(t *testing.T) {
		other := uint32(os.Getuid()) + 12345
		sock := serve(t, map[uint32]bool{other: true})
		resp, err := client(sock).Post("http://jentic-mcp"+mcpHTTPPath, "application/json", nil)
		if err == nil {
			_ = resp.Body.Close()
			t.Fatal("a peer uid off the allowlist must be refused at accept")
		}
	})
}

func TestAllowedPeerUIDSet(t *testing.T) {
	set := allowedPeerUIDSet([]int{501, -3})
	for _, uid := range []uint32{uint32(os.Getuid()), 0, 501} {
		if !set[uid] {
			t.Errorf("uid %d must be allowed", uid)
		}
	}
	if len(set) > 3 {
		t.Errorf("negative uids must be dropped, got %v", set)
	}
}

// TestSystemdListener pins the LISTEN_FDS handshake: ignored when absent or
// addressed to another pid, an error on malformed or multi-socket handoffs.
func TestSystemdListener(t *testing.T) {
	env := func(m map[string]string) func(string) string {
		return func(k string) string { return m[k] }
	}
	if ln, err := systemdListener(123, env(nil)); ln != nil || err != nil {
		t.Errorf("no env: ln=%v err=%v, want nil/nil", ln, err)
	}
	if ln, err := systemdListener(123, env(map[string]string{"LISTEN_FDS": "1", "LISTEN_PID": strconv.Itoa(456)})); ln != nil || err != nil {
		t.Errorf("another pid's activation: ln=%v err=%v, want nil/nil", ln, err)
	}
	if _, err := systemdListener(123, env(map[string]string{"LISTEN_FDS": "junk", "LISTEN_PID": "123"})); err == nil {
		t.Error("malformed LISTEN_FDS must error")
	}
	if _, err := systemdListener(123, env(map[string]string{"LISTEN_FDS": "2", "LISTEN_PID": "123"})); err == nil {
		t.Error("a multi-socket handoff must error")
	}
}

// TestListenerFromFD proves inherited-fd adoption against a real socket.
func TestListenerFromFD(t *testing.T) {
	ln, err := net.Listen("unix", shortSocketPath(t))
	if err != nil {
		t.Fatalf("bind: %v", err)
	}
	t.Cleanup(func() { _ = ln.Close() })
	f, err := ln.(*net.UnixListener).File()
	if err != nil {
		t.Fatalf("File: %v", err)
	}
	adopted, err := listenerFromFD(f.Fd(), "test socket")
	if err != nil {
		t.Fatalf("listenerFromFD: %v", err)
	}
	_ = adopted.Close()
}
