package api

// mcp_http.go is `jentic mcp --http`: the isolated-local-daemon serving mode
// (local-MCP phase 3 item 9, master §3.7.5 top rung). The SAME assembled
// mcpServer (tools, resources, envelopes, attribution) is served over
// stateless Streamable HTTP (spec 2026-07-28) instead of stdio — the golden
// transcripts must not be able to tell the transports apart.
//
// Key-custody model: this daemon holds exactly ONE context's keys (the root
// --context flag / $JENTIC_CONTEXT, like every other invocation) and signs
// every backend call with them. A caller who can reach the daemon acts as
// that context's agent — so WHO can reach it is the security boundary:
//
//   - Unix-domain socket (--socket; the default): callers authenticate by OS
//     identity. The daemon verifies each connection's peer credential
//     (SO_PEERCRED / LOCAL_PEERCRED) against an allowlist (--allow-uid;
//     default: the daemon's own uid and root). Nothing is stored at rest on
//     the client side — this is the §3.7.5 credential-less posture.
//   - TCP loopback (--listen 127.0.0.1:…): no peer identity exists on TCP,
//     so a bearer token is REQUIRED (--token-file); --allow-unauthenticated
//     is the explicit, loopback-only opt-out.
//   - TCP non-loopback: refused unless --allow-non-loopback AND TLS
//     (--tls-cert/--tls-key) AND a token are all present (phase acceptance).
//
// The daemon idle-exits (--idle-timeout, default 15m) so a socket-activated
// unit (systemd LISTEN_FDS, launchd inetd-wait) spawns on first connection
// and leaves nothing running — see deploy/mcp-daemon/.

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
)

// mcpHTTPOptions carries the `jentic mcp --http` serving flags.
type mcpHTTPOptions struct {
	listen               string        // TCP host:port (empty = no TCP)
	socket               string        // unix socket path (empty + no listen = default socket)
	tokenFile            string        // bearer token file for TCP callers
	tlsCert, tlsKey      string        // TLS keypair for non-loopback serving
	allowNonLoopback     bool          // explicit opt-in for a non-loopback bind
	allowUnauthenticated bool          // explicit opt-out of token auth (loopback TCP only)
	allowOrigins         []string      // extra allowed Origin values (loopback always passes)
	allowUIDs            []int         // peer uids allowed on the unix socket (daemon uid + root always)
	idleTimeout          time.Duration // exit after this long with no requests; 0 disables
	fromLaunchd          bool          // inherit the launchd inetd-wait listener on fd 0
}

// defaultMCPIdleTimeout is the idle-exit default: long enough to survive a
// model "thinking" between tool calls, short enough that a socket-activated
// daemon does not linger with keys in memory.
const defaultMCPIdleTimeout = 15 * time.Minute

// mcpHTTPPath is the served endpoint path — parity with the control plane's
// mounted /mcp, so client configs differ only in host.
const mcpHTTPPath = "/mcp"

// defaultMCPSocketPath is <XDG state dir>/mcp.sock — the same-uid manual-run
// default. Service deployments override it (systemd socket units own the
// path, owner, and mode; see deploy/mcp-daemon/).
func defaultMCPSocketPath() (string, error) {
	stateDir, err := sdkconfig.StateDir()
	if err != nil {
		return "", fmt.Errorf("resolving state dir for the mcp socket: %w", err)
	}
	return filepath.Join(stateDir, "mcp.sock"), nil
}

// mcpBindPosture is the validated serving decision resolveMCPBindPosture
// produces from the flags: exactly one transport, with the auth material the
// posture rules require already loaded.
type mcpBindPosture struct {
	network string // "unix" or "tcp"
	addr    string // socket path or host:port
	token   []byte // non-nil when TCP callers must present it
	useTLS  bool
}

// resolveMCPBindPosture enforces the serving rules before anything binds:
// unix sockets are the credential-less peer-cred path; TCP requires a token
// (loopback may opt out explicitly); non-loopback requires opt-in + TLS +
// token. Refusals here are the acceptance-box "refuses without TLS+token".
func resolveMCPBindPosture(opts *mcpHTTPOptions) (*mcpBindPosture, error) {
	if opts.listen != "" && opts.socket != "" {
		return nil, errors.New("--listen and --socket are mutually exclusive — serve one transport per daemon")
	}

	var token []byte
	if opts.tokenFile != "" {
		var err error
		token, err = readMCPTokenFile(opts.tokenFile)
		if err != nil {
			return nil, err
		}
	}

	if opts.listen == "" {
		// Unix socket (explicit or default): peer credentials are the auth.
		path := opts.socket
		if path == "" {
			var err error
			path, err = defaultMCPSocketPath()
			if err != nil {
				return nil, err
			}
		}
		if opts.allowUnauthenticated {
			return nil, errors.New("--allow-unauthenticated applies to --listen only; the unix socket already authenticates by peer credential")
		}
		return &mcpBindPosture{network: "unix", addr: path, token: token}, nil
	}

	host, _, err := net.SplitHostPort(opts.listen)
	if err != nil {
		return nil, fmt.Errorf("--listen %q is not a host:port address: %w", opts.listen, err)
	}
	loopback := hostIsLoopback(host)
	hasTLS := opts.tlsCert != "" || opts.tlsKey != ""
	if hasTLS && (opts.tlsCert == "" || opts.tlsKey == "") {
		return nil, errors.New("--tls-cert and --tls-key must be provided together")
	}

	if !loopback {
		// The phase acceptance box: a non-loopback bind is explicit opt-in
		// AND refuses without TLS + token — never plaintext, never open.
		if !opts.allowNonLoopback {
			return nil, fmt.Errorf("refusing the non-loopback bind %q: pass --allow-non-loopback (and provide --tls-cert/--tls-key and --token-file)", opts.listen)
		}
		if !hasTLS {
			return nil, fmt.Errorf("refusing the non-loopback bind %q without TLS: provide --tls-cert and --tls-key", opts.listen)
		}
		if len(token) == 0 {
			return nil, fmt.Errorf("refusing the non-loopback bind %q without a caller token: provide --token-file", opts.listen)
		}
		if opts.allowUnauthenticated {
			return nil, errors.New("--allow-unauthenticated is loopback-only; a non-loopback bind always requires the token")
		}
		return &mcpBindPosture{network: "tcp", addr: opts.listen, token: token, useTLS: true}, nil
	}

	// Loopback TCP: no peer identity exists, so require the token unless the
	// operator explicitly accepts that every local uid may use this
	// context's keys (the documented single-user-machine trade-off).
	if len(token) == 0 && !opts.allowUnauthenticated {
		return nil, fmt.Errorf("refusing the loopback bind %q without auth: TCP has no peer identity, so provide --token-file (or pass --allow-unauthenticated to accept that every local user may act as this context)", opts.listen)
	}
	return &mcpBindPosture{network: "tcp", addr: opts.listen, token: token, useTLS: hasTLS}, nil
}

// readMCPTokenFile loads the shared-secret token TCP callers must present.
// The file is refused when group/world-accessible — a readable token would
// silently void the boundary the daemon exists to draw.
func readMCPTokenFile(path string) ([]byte, error) {
	info, err := os.Stat(path)
	if err != nil {
		return nil, fmt.Errorf("reading --token-file: %w", err)
	}
	if info.Mode().Perm()&0o077 != 0 {
		return nil, fmt.Errorf("--token-file %s is group/world-accessible (mode %04o) — chmod it to 0600", path, info.Mode().Perm())
	}
	raw, err := os.ReadFile(path) //nolint:gosec // the operator-chosen --token-file, not untrusted input.
	if err != nil {
		return nil, fmt.Errorf("reading --token-file: %w", err)
	}
	token := []byte(strings.TrimSpace(string(raw)))
	if len(token) == 0 {
		return nil, fmt.Errorf("--token-file %s is empty", path)
	}
	return token, nil
}

// hostIsLoopback reports whether a --listen host can only be reached from
// this machine: "localhost" or a literal loopback IP. Anything unparseable
// (a DNS name like 127.0.0.1.evil.example) is NOT loopback — the same
// parsed-IP posture as the SEC-1 broker check.
func hostIsLoopback(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// mcpHTTPE runs the daemon: resolve the posture, bind (or inherit) the
// listener, and serve the assembled mcpServer behind the platform gates
// until the context is cancelled or the idle timeout fires.
func (a *app) mcpHTTPE(ctx context.Context, s *mcpServer, opts *mcpHTTPOptions, logger *slog.Logger) error {
	posture, err := resolveMCPBindPosture(opts)
	if err != nil {
		return err
	}

	ln, activated, err := resolveMCPListener(posture, opts, logger)
	if err != nil {
		return err
	}
	defer func() { _ = ln.Close() }()

	// A socket-activated listener was bound by the init system, not by the
	// flags — re-check what we actually inherited so a misconfigured unit
	// (e.g. a non-loopback ListenStream without TLS) fails closed too.
	if activated {
		if err := checkActivatedListener(ln, posture); err != nil {
			return err
		}
	}

	if posture.network == "unix" {
		allowed := allowedPeerUIDSet(opts.allowUIDs)
		ln = &peerCredListener{Listener: ln, allowed: allowed, logger: logger}
		logger.Info("mcp http daemon serving on unix socket",
			"path", ln.Addr().String(), "allowed_uids", uidSetForLog(allowed))
	} else {
		logger.Info("mcp http daemon serving on tcp",
			"addr", ln.Addr().String(), "tls", posture.useTLS, "token_auth", len(posture.token) > 0)
	}

	idle := newIdleWatchdog(opts.idleTimeout)
	handler := s.httpHandler(ctx, posture, opts.allowOrigins, idle, logger)

	srv := &http.Server{
		Handler:           handler,
		ReadHeaderTimeout: 30 * time.Second,
	}

	serveErr := make(chan error, 1)
	go func() {
		if posture.useTLS {
			cert, err := tls.LoadX509KeyPair(opts.tlsCert, opts.tlsKey)
			if err != nil {
				serveErr <- fmt.Errorf("loading the TLS keypair: %w", err)
				return
			}
			srv.TLSConfig = &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS12}
			serveErr <- srv.ServeTLS(ln, "", "")
			return
		}
		serveErr <- srv.Serve(ln)
	}()

	var reason string
	select {
	case <-ctx.Done():
		reason = "signal"
	case <-idle.fired():
		reason = "idle-exit"
	case err := <-serveErr:
		return fmt.Errorf("mcp http daemon: %w", err)
	}
	logger.Info("mcp http daemon shutting down", "reason", reason)

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		return fmt.Errorf("mcp http daemon shutdown: %w", err)
	}
	return nil
}

// resolveMCPListener produces the serving listener: an inherited
// socket-activation fd when present (systemd LISTEN_FDS, launchd fd 0), else
// a fresh bind per the validated posture. The bool reports inheritance.
func resolveMCPListener(posture *mcpBindPosture, opts *mcpHTTPOptions, logger *slog.Logger) (net.Listener, bool, error) {
	if opts.fromLaunchd {
		ln, err := launchdListener()
		if err != nil {
			return nil, false, err
		}
		logger.Info("inherited the launchd inetd-wait listener")
		return ln, true, nil
	}
	if ln, err := systemdListener(os.Getpid(), os.Getenv); err != nil {
		return nil, false, err
	} else if ln != nil {
		logger.Info("inherited the systemd socket-activation listener")
		return ln, true, nil
	}

	if posture.network == "unix" {
		if err := removeStaleSocket(posture.addr); err != nil {
			return nil, false, err
		}
		if err := os.MkdirAll(filepath.Dir(posture.addr), 0o700); err != nil {
			return nil, false, fmt.Errorf("creating the socket dir: %w", err)
		}
		ln, err := net.Listen("unix", posture.addr)
		if err != nil {
			return nil, false, fmt.Errorf("binding the unix socket %s: %w", posture.addr, err)
		}
		// Same-uid default for manual runs; service units own the mode when
		// they create the socket themselves.
		if err := os.Chmod(posture.addr, 0o600); err != nil {
			_ = ln.Close()
			return nil, false, fmt.Errorf("restricting the socket mode: %w", err)
		}
		return ln, false, nil
	}

	ln, err := net.Listen("tcp", posture.addr)
	if err != nil {
		return nil, false, fmt.Errorf("binding %s: %w", posture.addr, err)
	}
	return ln, false, nil
}

// checkActivatedListener fails closed when the init system handed us a
// listener the flag posture would have refused to bind: a non-loopback TCP
// socket still requires the TLS+token opt-ins.
func checkActivatedListener(ln net.Listener, posture *mcpBindPosture) error {
	addr, ok := ln.Addr().(*net.TCPAddr)
	if !ok {
		return nil // unix socket — peer-cred auth applies regardless of who bound it
	}
	if addr.IP != nil && !addr.IP.IsLoopback() && !addr.IP.IsUnspecified() {
		// A specific non-loopback IP.
		if !posture.useTLS || len(posture.token) == 0 {
			return fmt.Errorf("the activated socket binds non-loopback %s: the unit must also pass --allow-non-loopback with --tls-cert/--tls-key and --token-file", addr)
		}
	}
	if addr.IP == nil || addr.IP.IsUnspecified() {
		if !posture.useTLS || len(posture.token) == 0 {
			return fmt.Errorf("the activated socket binds all interfaces (%s): the unit must also pass --allow-non-loopback with --tls-cert/--tls-key and --token-file", addr)
		}
	}
	return nil
}

// removeStaleSocket clears a leftover socket file from a previous run. A
// LIVE socket (something accepts) is left alone so two daemons cannot fight
// over one path.
func removeStaleSocket(path string) error {
	info, err := os.Stat(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("checking the socket path: %w", err)
	}
	if info.Mode()&os.ModeSocket == 0 {
		return fmt.Errorf("socket path %s exists and is not a socket — refusing to remove it", path)
	}
	conn, err := net.DialTimeout("unix", path, time.Second)
	if err == nil {
		_ = conn.Close()
		return fmt.Errorf("another daemon is already serving on %s", path)
	}
	if err := os.Remove(path); err != nil {
		return fmt.Errorf("removing the stale socket: %w", err)
	}
	return nil
}

// httpHandler assembles the served handler chain for one daemon process:
// path mux → session-context merge → idle tracking → Origin gate → auth
// gate → the SDK's stateless Streamable HTTP handler (which owns 405 GET,
// Accept/Content-Type validation, and the 4 MiB body bound).
func (s *mcpServer) httpHandler(sessionCtx context.Context, posture *mcpBindPosture, allowOrigins []string, idle *idleWatchdog, logger *slog.Logger) http.Handler {
	sdkHandler := mcp.NewStreamableHTTPHandler(
		func(*http.Request) *mcp.Server { return s.server },
		&mcp.StreamableHTTPOptions{
			Stateless:    true,
			JSONResponse: true,
			Logger:       logger,
		},
	)

	gate := &mcpHTTPGate{
		next:         sdkHandler,
		sessionCtx:   sessionCtx,
		token:        posture.token,
		allowOrigins: normalizedOriginSet(allowOrigins),
		logger:       logger,
	}

	mux := http.NewServeMux()
	mux.Handle(mcpHTTPPath, idle.track(gate))
	return mux
}

// --- idle-exit watchdog -------------------------------------------------------

// idleWatchdog fires once when no request has been in flight for the
// configured timeout — the daemon side of socket-activation's spawn/exit
// loop. A zero timeout disables it.
type idleWatchdog struct {
	timeout  time.Duration
	inflight atomic.Int64
	last     atomic.Int64 // UnixNano of the last request completion (or start)
	once     sync.Once
	ch       chan struct{}
}

func newIdleWatchdog(timeout time.Duration) *idleWatchdog {
	w := &idleWatchdog{timeout: timeout, ch: make(chan struct{})}
	w.last.Store(time.Now().UnixNano())
	if timeout > 0 {
		go w.watch()
	}
	return w
}

// fired returns the channel closed when the idle timeout elapses.
func (w *idleWatchdog) fired() <-chan struct{} { return w.ch }

// track wraps a handler so every request holds the watchdog open for its
// duration and resets the idle clock when it completes.
func (w *idleWatchdog) track(next http.Handler) http.Handler {
	return http.HandlerFunc(func(rw http.ResponseWriter, r *http.Request) {
		w.inflight.Add(1)
		w.last.Store(time.Now().UnixNano())
		defer func() {
			w.last.Store(time.Now().UnixNano())
			w.inflight.Add(-1)
		}()
		next.ServeHTTP(rw, r)
	})
}

func (w *idleWatchdog) watch() {
	tick := w.timeout / 4
	if tick > time.Second {
		tick = time.Second
	}
	ticker := time.NewTicker(tick)
	defer ticker.Stop()
	for range ticker.C {
		if w.inflight.Load() > 0 {
			continue
		}
		if time.Since(time.Unix(0, w.last.Load())) >= w.timeout {
			w.once.Do(func() { close(w.ch) })
			return
		}
	}
}
