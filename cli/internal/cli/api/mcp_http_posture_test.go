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

// TestSystemdListener pins the LISTEN_FDS handshake: ignored when absent,
// when LISTEN_PID is missing (the sd_listen_fds protocol — vars without a
// pid are not addressed to anyone), or when it names another pid; an error
// on malformed or multi-socket handoffs; adoption consumes (scrubs) the
// activation vars.
func TestSystemdListener(t *testing.T) {
	env := func(m map[string]string) func(string) string {
		return func(k string) string { return m[k] }
	}
	scrubbed := func() (map[string]bool, func(string)) {
		set := map[string]bool{}
		return set, func(k string) { set[k] = true }
	}

	t.Run("no env is no activation", func(t *testing.T) {
		unset, record := scrubbed()
		if ln, err := systemdListener(123, env(nil), record); ln != nil || err != nil {
			t.Errorf("no env: ln=%v err=%v, want nil/nil", ln, err)
		}
		if len(unset) != 0 {
			t.Errorf("nothing to scrub without activation vars, scrubbed %v", unset)
		}
	})

	t.Run("LISTEN_FDS without LISTEN_PID is not addressed to us", func(t *testing.T) {
		unset, record := scrubbed()
		ln, err := systemdListener(123, env(map[string]string{"LISTEN_FDS": "1"}), record)
		if ln != nil || err != nil {
			t.Errorf("pid-less activation: ln=%v err=%v, want nil/nil (a stray LISTEN_FDS must never adopt fd 3)", ln, err)
		}
		if len(unset) != 0 {
			t.Errorf("another process's vars must not be scrubbed, scrubbed %v", unset)
		}
	})

	t.Run("another pid's activation is ignored and left in place", func(t *testing.T) {
		unset, record := scrubbed()
		ln, err := systemdListener(123, env(map[string]string{"LISTEN_FDS": "1", "LISTEN_PID": strconv.Itoa(456)}), record)
		if ln != nil || err != nil {
			t.Errorf("another pid's activation: ln=%v err=%v, want nil/nil", ln, err)
		}
		if len(unset) != 0 {
			t.Errorf("another process's vars must not be scrubbed, scrubbed %v", unset)
		}
	})

	t.Run("malformed LISTEN_FDS errors", func(t *testing.T) {
		_, record := scrubbed()
		if _, err := systemdListener(123, env(map[string]string{"LISTEN_FDS": "junk", "LISTEN_PID": "123"}), record); err == nil {
			t.Error("malformed LISTEN_FDS must error")
		}
	})

	t.Run("multi-socket handoff errors", func(t *testing.T) {
		_, record := scrubbed()
		if _, err := systemdListener(123, env(map[string]string{"LISTEN_FDS": "2", "LISTEN_PID": "123"}), record); err == nil {
			t.Error("a multi-socket handoff must error")
		}
	})

	t.Run("adoption scrubs the activation vars", func(t *testing.T) {
		restore := activationListenerFromFD
		t.Cleanup(func() { activationListenerFromFD = restore })
		fake := fakeAddrListener{addr: &net.UnixAddr{Name: "/run/fake.sock", Net: "unix"}}
		activationListenerFromFD = func(fd uintptr, _ string) (net.Listener, error) {
			if fd != listenFDsStart {
				t.Errorf("adopted fd = %d, want %d", fd, listenFDsStart)
			}
			return fake, nil
		}
		unset, record := scrubbed()
		ln, err := systemdListener(123, env(map[string]string{"LISTEN_FDS": "1", "LISTEN_PID": "123"}), record)
		if err != nil || ln != fake {
			t.Fatalf("adoption: ln=%v err=%v, want the injected listener", ln, err)
		}
		for _, key := range []string{"LISTEN_FDS", "LISTEN_PID", "LISTEN_FDNAMES"} {
			if !unset[key] {
				t.Errorf("%s must be scrubbed after adoption, scrubbed only %v", key, unset)
			}
		}
	})
}

// fakeAddrListener injects an arbitrary net.Addr — the "fake inherited
// listener" harness for the activation checks.
type fakeAddrListener struct{ addr net.Addr }

func (l fakeAddrListener) Accept() (net.Conn, error) { return nil, net.ErrClosed }
func (l fakeAddrListener) Close() error              { return nil }
func (l fakeAddrListener) Addr() net.Addr            { return l.addr }

// TestCheckActivatedListener pins the F1 fix: the inherited socket's FAMILY
// must match the flag posture (the auth gate is selected from the flags), in
// BOTH directions — a unix socket under a TCP posture would serve with no
// gate at all, and a TCP socket under a unix posture would brick at accept.
// The pre-existing TCP addr-class re-validation stays pinned too.
func TestCheckActivatedListener(t *testing.T) {
	unixAddr := &net.UnixAddr{Name: "/run/jentic-mcp/mcp.sock", Net: "unix"}
	loopback := &net.TCPAddr{IP: net.ParseIP("127.0.0.1"), Port: 8200}
	nonLoopback := &net.TCPAddr{IP: net.ParseIP("10.0.0.5"), Port: 8200}
	unspecified := &net.TCPAddr{IP: net.IPv4zero, Port: 8200}

	unixPosture := &mcpBindPosture{network: "unix", addr: "/run/jentic-mcp/mcp.sock"}
	tcpTokenPosture := &mcpBindPosture{network: "tcp", addr: "127.0.0.1:8200", token: []byte("tok")}
	tcpFullPosture := &mcpBindPosture{network: "tcp", addr: "10.0.0.5:8200", token: []byte("tok"), useTLS: true}

	cases := []struct {
		name    string
		addr    net.Addr
		posture *mcpBindPosture
		wantErr string // substring; empty = must pass
	}{
		{"unix socket under a unix posture passes", unixAddr, unixPosture, ""},
		{"unix socket under a TCP posture is the auth-downgrade — refused", unixAddr, tcpTokenPosture, "peer-cred gate"},
		{"TCP socket under a unix posture is the bricked converse — refused", loopback, unixPosture, "cannot apply to TCP"},
		{"loopback TCP under a TCP posture passes", loopback, tcpTokenPosture, ""},
		{"non-loopback TCP without TLS+token refused", nonLoopback, tcpTokenPosture, "--tls-cert"},
		{"non-loopback TCP with TLS+token passes", nonLoopback, tcpFullPosture, ""},
		{"all-interfaces TCP without TLS+token refused", unspecified, tcpTokenPosture, "all interfaces"},
		{"an unknown address family is refused", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 1}, unixPosture, "neither unix nor tcp"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := checkActivatedListener(fakeAddrListener{addr: tc.addr}, tc.posture)
			if tc.wantErr == "" {
				if err != nil {
					t.Fatalf("want pass, got %v", err)
				}
				return
			}
			if err == nil {
				t.Fatalf("want a startup refusal mentioning %q, got nil", tc.wantErr)
			}
			if !strings.Contains(err.Error(), tc.wantErr) {
				t.Errorf("refusal %q must mention %q", err.Error(), tc.wantErr)
			}
		})
	}
}

// TestResolveMCPListener_SocketNeverLooseAtBind pins the F4 fix: the socket
// file is created under a 0177 umask, so at no instant does it exist with a
// mode looser than 0600 (no bind→chmod window a local uid could race).
func TestResolveMCPListener_SocketNeverLooseAtBind(t *testing.T) {
	sock := shortSocketPath(t)
	ln, _, err := resolveMCPListener(&mcpBindPosture{network: "unix", addr: sock}, &mcpHTTPOptions{}, discardLogger())
	if err != nil {
		t.Fatalf("bind: %v", err)
	}
	t.Cleanup(func() { _ = ln.Close() })
	info, err := os.Stat(sock)
	if err != nil {
		t.Fatalf("stat socket: %v", err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Errorf("socket mode = %04o, want 0600", info.Mode().Perm())
	}
}

// TestEnsureSocketDir pins the parent-dir posture: fresh dirs are private, a
// pre-existing group/world-writable dir without the sticky bit is refused
// (someone else could swap the socket path), and sticky world-writable dirs
// (/tmp) stay usable.
func TestEnsureSocketDir(t *testing.T) {
	t.Run("creates a private dir", func(t *testing.T) {
		dir := filepath.Join(t.TempDir(), "sub")
		if err := ensureSocketDir(dir); err != nil {
			t.Fatalf("ensureSocketDir: %v", err)
		}
		info, err := os.Stat(dir)
		if err != nil {
			t.Fatalf("stat: %v", err)
		}
		if info.Mode().Perm() != 0o700 {
			t.Errorf("created dir mode = %04o, want 0700", info.Mode().Perm())
		}
	})
	t.Run("refuses a pre-existing world-writable dir", func(t *testing.T) {
		dir := filepath.Join(t.TempDir(), "loose")
		if err := os.Mkdir(dir, 0o777); err != nil {
			t.Fatalf("mkdir: %v", err)
		}
		if err := os.Chmod(dir, 0o777); err != nil { // Mkdir's mode is umask-filtered; force it
			t.Fatalf("chmod: %v", err)
		}
		if err := ensureSocketDir(dir); err == nil || !strings.Contains(err.Error(), "writable") {
			t.Errorf("a world-writable socket dir must be refused, got %v", err)
		}
	})
	t.Run("a sticky world-writable dir is allowed", func(t *testing.T) {
		dir := filepath.Join(t.TempDir(), "sticky")
		if err := os.Mkdir(dir, 0o777); err != nil {
			t.Fatalf("mkdir: %v", err)
		}
		if err := os.Chmod(dir, 0o777|os.ModeSticky); err != nil {
			t.Fatalf("chmod: %v", err)
		}
		if err := ensureSocketDir(dir); err != nil {
			t.Errorf("a sticky dir (the /tmp shape) must stay usable, got %v", err)
		}
	})
}

// TestCheckMCPModeFlags pins the F8 fix: daemon-only flags without --http
// (and --bearer-file without --connect) are refused, never silently dropped.
func TestCheckMCPModeFlags(t *testing.T) {
	parse := func(t *testing.T, args ...string) error {
		t.Helper()
		cmd := newMCPCmd(nil)
		if err := cmd.ParseFlags(args); err != nil {
			t.Fatalf("parse %v: %v", args, err)
		}
		return checkMCPModeFlags(cmd.Flags())
	}

	refused := [][]string{
		{"--socket", "/tmp/x.sock"},
		{"--listen", "127.0.0.1:1"},
		{"--token-file", "/tmp/t"},
		{"--tls-cert", "c.pem"},
		{"--tls-key", "k.pem"},
		{"--allow-non-loopback"},
		{"--allow-unauthenticated"},
		{"--allow-origin", "https://x"},
		{"--allow-uid", "501"},
		{"--idle-timeout", "1m"},
		{"--from-launchd"},
	}
	for _, args := range refused {
		err := parse(t, args...)
		if err == nil {
			t.Errorf("%v without --http must be refused, not ignored", args)
			continue
		}
		if !strings.Contains(err.Error(), "requires --http") {
			t.Errorf("refusal for %v must say the flag requires --http, got %q", args, err)
		}
	}

	if err := parse(t, "--bearer-file", "/tmp/b"); err == nil || !strings.Contains(err.Error(), "requires --connect") {
		t.Errorf("--bearer-file without --connect must be refused, got %v", err)
	}

	for _, args := range [][]string{
		{},
		{"--read-only"},
		{"--http", "--listen", "127.0.0.1:1", "--token-file", "/tmp/t", "--idle-timeout", "1m"},
		{"--connect", "unix:///tmp/x.sock"},
		{"--connect", "https://daemon.example", "--bearer-file", "/tmp/b"},
	} {
		if err := parse(t, args...); err != nil {
			t.Errorf("%v must pass the mode-flag check, got %v", args, err)
		}
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
