package egressproxy

import (
	"bufio"
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	// connectHeaderTimeout bounds reading one CONNECT request.
	connectHeaderTimeout = 30 * time.Second
	// tlsHandshakeTimeout bounds the agent's TLS handshake with a leaf.
	tlsHandshakeTimeout = 15 * time.Second
	// tunnelDialTimeout bounds dialling a tunnel host.
	tunnelDialTimeout = 15 * time.Second
	// governedIdleTimeout closes an idle governed keep-alive connection.
	governedIdleTimeout = 5 * time.Minute
	// ProxyAuthRealm is the realm of the 407 challenge.
	ProxyAuthRealm = "Jentic egress proxy"
)

// ProxyFacadePrefix is the broker route a governed request is relayed to:
// `<broker>/_facade/proxy/https://<host><path>?<query>`.
const ProxyFacadePrefix = "/_facade/proxy/"

// Tokens yields the session invocation token for a vendor ("" when the
// session holds none).
type Tokens interface {
	Session(vendor string) string
}

// Config configures a Server.
type Config struct {
	Policy *Policy
	// CA signs the governed hosts' leaves; required when Policy governs any
	// vendor.
	CA     *CA
	Tokens Tokens
	// BrokerURL is the broker's base URL as the CLI's context names it
	// (http://127.0.0.1:8100); it is the Host the broker sees.
	BrokerURL *url.URL
	// BrokerTransport carries relayed requests to the broker (dialling the
	// session's relay socket); nil means http.DefaultTransport.
	BrokerTransport http.RoundTripper
	// TunnelDial dials a tunnel host; nil means a dialer that refuses
	// loopback, private, link-local and unspecified addresses.
	TunnelDial func(ctx context.Context, network, addr string) (net.Conn, error)
	Logger     *slog.Logger
}

// Server is the session's egress proxy: an HTTP CONNECT proxy that tunnels
// the agent's allowlisted hosts, terminates TLS for governed vendor hosts and
// relays their requests to the broker, and refuses everything else.
type Server struct {
	cfg Config
	log *slog.Logger

	mu       sync.Mutex
	conns    map[net.Conn]struct{}
	closed   bool
	listener net.Listener
}

// New validates cfg and returns a Server.
func New(cfg Config) (*Server, error) {
	if cfg.Policy == nil || cfg.Tokens == nil || cfg.BrokerURL == nil {
		return nil, errors.New("the egress proxy needs a policy, a token source and the broker URL")
	}
	if len(cfg.Policy.GovernedDomains()) > 0 {
		if cfg.CA == nil {
			return nil, errors.New("the egress proxy governs vendors but has no session CA")
		}
		for _, d := range cfg.Policy.GovernedDomains() {
			if !cfg.CA.Permits(d) {
				return nil, fmt.Errorf("the session CA does not permit governed domain %s", d)
			}
		}
	}
	if cfg.BrokerURL.Scheme != "http" && cfg.BrokerURL.Scheme != "https" || cfg.BrokerURL.Host == "" {
		return nil, fmt.Errorf("invalid broker URL %q", cfg.BrokerURL)
	}
	if cfg.BrokerTransport == nil {
		cfg.BrokerTransport = http.DefaultTransport
	}
	if cfg.TunnelDial == nil {
		cfg.TunnelDial = publicDialer().DialContext
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.New(slog.DiscardHandler)
	}
	return &Server{cfg: cfg, log: cfg.Logger, conns: map[net.Conn]struct{}{}}, nil
}

// publicDialer refuses to connect a tunnel to any address that is not a
// public unicast one, so a tunnel host name that resolves to the Docker host,
// another container or a link-local metadata endpoint reaches nothing.
func publicDialer() *net.Dialer {
	return &net.Dialer{
		Timeout: tunnelDialTimeout,
		Control: func(_, address string, _ syscall.RawConn) error {
			host, _, err := net.SplitHostPort(address)
			if err != nil {
				return err
			}
			ip := net.ParseIP(host)
			if ip == nil || !publicUnicast(ip) {
				return fmt.Errorf("refusing to tunnel to non-public address %s", host)
			}
			return nil
		},
	}
}

func publicUnicast(ip net.IP) bool {
	return ip.IsGlobalUnicast() && !ip.IsPrivate() && !ip.IsLoopback() && !ip.IsLinkLocalUnicast()
}

// Serve accepts connections on ln until Close.
func (s *Server) Serve(ln net.Listener) error {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return net.ErrClosed
	}
	s.listener = ln
	s.mu.Unlock()
	for {
		conn, err := ln.Accept()
		if err != nil {
			s.mu.Lock()
			closed := s.closed
			s.mu.Unlock()
			if closed {
				return nil
			}
			var ne net.Error
			if errors.As(err, &ne) && ne.Timeout() {
				time.Sleep(50 * time.Millisecond)
				continue
			}
			return err
		}
		if !s.track(conn) {
			_ = conn.Close()
			continue
		}
		go func() {
			defer s.untrack(conn)
			s.handle(conn)
		}()
	}
}

// Close stops accepting and closes every open connection.
func (s *Server) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closed = true
	var err error
	if s.listener != nil {
		err = s.listener.Close()
	}
	for c := range s.conns {
		_ = c.Close()
	}
	return err
}

func (s *Server) track(c net.Conn) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return false
	}
	s.conns[c] = struct{}{}
	return true
}

func (s *Server) untrack(c net.Conn) {
	_ = c.Close()
	s.mu.Lock()
	delete(s.conns, c)
	s.mu.Unlock()
}

// handle serves one client connection: CONNECT requests until one is
// accepted (a 407 keeps the connection, as proxy authentication requires),
// then the tunnel or the governed TLS session.
func (s *Server) handle(conn net.Conn) {
	br := bufio.NewReader(conn)
	for {
		_ = conn.SetReadDeadline(time.Now().Add(connectHeaderTimeout))
		req, err := http.ReadRequest(br)
		if err != nil {
			return
		}
		_ = conn.SetReadDeadline(time.Time{})
		if req.Method != http.MethodConnect {
			s.log.Warn("egress refused", "reason", "plain HTTP proxying is refused; only HTTPS (CONNECT) is allowed",
				"method", req.Method, "host", hostOnly(req.Host))
			writeProxyResponse(conn, http.StatusForbidden, nil,
				"Jentic egress proxy: plain HTTP is refused; only HTTPS to allowed hosts is proxied.\n")
			return
		}
		d := s.cfg.Policy.Decide(req.Host)
		switch d.Action {
		case Tunnel:
			s.tunnel(conn, br, d)
			return
		case Govern:
			token, source, authErr := s.tokenFor(d.Vendor, req.Header.Get("Proxy-Authorization"))
			if token == "" {
				s.log.Info("egress proxy authentication required", "host", d.Host, "vendor", d.Vendor, "reason", authErr)
				writeProxyResponse(conn, http.StatusProxyAuthRequired,
					http.Header{"Proxy-Authenticate": {`Basic realm="` + ProxyAuthRealm + `"`}}, "")
				if req.Close {
					return
				}
				continue
			}
			s.govern(conn, br, d, token, source)
			return
		default:
			s.log.Warn("egress refused", "host", d.Host, "target", req.Host, "reason", d.Reason)
			writeProxyResponse(conn, http.StatusForbidden, nil,
				"Jentic egress proxy: "+d.Host+" is not reachable from this session ("+d.Reason+").\n")
			return
		}
	}
}

// tokenFor picks the invocation token for a governed CONNECT: the per-command
// token in the proxy credentials when the client sent one, else the session
// token for the vendor. It returns "" (answer 407) with the reason when
// neither is usable.
func (s *Server) tokenFor(vendor, proxyAuth string) (token, source, reason string) {
	if t, present, err := proxyCredentialToken(proxyAuth); present {
		if err != nil {
			return "", "", err.Error()
		}
		return t, "proxy-credentials", ""
	}
	if t := s.cfg.Tokens.Session(vendor); t != "" {
		return t, "session", ""
	}
	return "", "", "the session holds no invocation token for " + vendor
}

// writeProxyResponse writes a proxy-level response on the raw connection.
func writeProxyResponse(w io.Writer, status int, header http.Header, body string) {
	var b strings.Builder
	fmt.Fprintf(&b, "HTTP/1.1 %d %s\r\n", status, http.StatusText(status))
	for k, vs := range header {
		for _, v := range vs {
			fmt.Fprintf(&b, "%s: %s\r\n", k, v)
		}
	}
	if body != "" {
		b.WriteString("Content-Type: text/plain; charset=utf-8\r\n")
	}
	fmt.Fprintf(&b, "Content-Length: %d\r\n\r\n%s", len(body), body)
	_, _ = io.WriteString(w, b.String())
}

// tunnel splices the client to the host without terminating TLS.
func (s *Server) tunnel(conn net.Conn, br *bufio.Reader, d Decision) {
	ctx, cancel := context.WithTimeout(context.Background(), tunnelDialTimeout)
	upstream, err := s.cfg.TunnelDial(ctx, "tcp", net.JoinHostPort(d.Host, "443"))
	cancel()
	if err != nil {
		s.log.Warn("egress tunnel dial failed", "host", d.Host, "error", err.Error())
		writeProxyResponse(conn, http.StatusBadGateway, nil, "Jentic egress proxy: could not reach "+d.Host+".\n")
		return
	}
	defer upstream.Close()
	if _, err := io.WriteString(conn, "HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
		return
	}
	s.log.Info("egress tunnel opened", "host", d.Host)
	var up, down int64
	done := make(chan struct{})
	go func() {
		defer close(done)
		down, _ = io.Copy(conn, upstream)
		closeWrite(conn)
	}()
	// Bytes the client sent right behind the CONNECT are still buffered.
	up, _ = io.Copy(upstream, br)
	closeWrite(upstream)
	<-done
	s.log.Info("egress tunnel closed", "host", d.Host, "bytes_up", up, "bytes_down", down)
}

func closeWrite(c net.Conn) {
	if cw, ok := c.(interface{ CloseWrite() error }); ok {
		_ = cw.CloseWrite()
		return
	}
	_ = c.Close()
}

// govern terminates the agent's TLS with a session-CA leaf for the host and
// serves its HTTP/1.1 requests, relaying each to the broker.
func (s *Server) govern(conn net.Conn, br *bufio.Reader, d Decision, token, source string) {
	leaf, err := s.cfg.CA.Leaf(d.Host)
	if err != nil {
		s.log.Error("egress refused", "host", d.Host, "reason", err.Error())
		writeProxyResponse(conn, http.StatusForbidden, nil, "Jentic egress proxy: "+d.Host+" cannot be governed.\n")
		return
	}
	if _, err := io.WriteString(conn, "HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
		return
	}
	tlsConn := tls.Server(&bufferedConn{Conn: conn, r: br}, &tls.Config{
		MinVersion: tls.VersionTLS12,
		NextProtos: []string{"http/1.1"},
		GetCertificate: func(hello *tls.ClientHelloInfo) (*tls.Certificate, error) {
			if hello.ServerName != "" {
				if sni, ok := NormalizeHost(hello.ServerName); !ok || sni != d.Host {
					return nil, fmt.Errorf("TLS server name %q does not match the CONNECT target %s", hello.ServerName, d.Host)
				}
			}
			return leaf, nil
		},
	})
	_ = conn.SetDeadline(time.Now().Add(tlsHandshakeTimeout))
	if err := tlsConn.Handshake(); err != nil {
		s.log.Warn("egress TLS handshake failed", "host", d.Host, "error", err.Error())
		_ = tlsConn.Close()
		return
	}
	_ = conn.SetDeadline(time.Time{})
	s.log.Info("egress governed connection", "host", d.Host, "vendor", d.Vendor, "token", source)

	srv := &http.Server{
		Handler:           s.relayHandler(d, token, source),
		ReadHeaderTimeout: connectHeaderTimeout,
		IdleTimeout:       governedIdleTimeout,
		ErrorLog:          slog.NewLogLogger(s.log.Handler(), slog.LevelDebug),
	}
	_ = srv.Serve(newOneConnListener(tlsConn))
}

// relayHandler relays a governed connection's requests to the broker's proxy
// route with the connection's invocation token. git smart HTTP to github.com
// takes the same route (the broker serves it through its governed git
// pipeline); a broker 401 to git is answered as 403 (gitResponse), so git
// never prompts for a credential the session does not hold.
func (s *Server) relayHandler(d Decision, token, source string) http.Handler {
	broker := s.cfg.BrokerURL
	base := strings.TrimRight(broker.Path, "/") + ProxyFacadePrefix + "https://" + d.Host
	// The request and response bodies stream both ways (FlushInterval -1, a
	// chunked request body stays chunked), so a clone's pack or a push of any
	// size passes through without being buffered here; the broker applies
	// its own limits.
	newRelay := func(git bool) *httputil.ReverseProxy {
		return &httputil.ReverseProxy{
			Transport:     s.cfg.BrokerTransport,
			FlushInterval: -1,
			Rewrite: func(pr *httputil.ProxyRequest) {
				path, query, _ := strings.Cut(pr.In.RequestURI, "?")
				pr.Out.URL = &url.URL{Scheme: broker.Scheme, Host: broker.Host, Opaque: base + path, RawQuery: query}
				pr.Out.Host = ""
				sanitizeUpstreamHeaders(pr.Out.Header)
				pr.Out.Header.Set("Authorization", "Bearer "+token)
			},
			ModifyResponse: func(resp *http.Response) error {
				s.log.Info("egress governed request", "host", d.Host, "vendor", d.Vendor, "method", resp.Request.Method,
					"path", strings.TrimPrefix(resp.Request.URL.Opaque, base), "status", resp.StatusCode, "token", source,
					"git", git)
				if git {
					gitResponse(resp)
				}
				return nil
			},
			ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
				s.log.Error("egress broker relay failed", "host", d.Host, "vendor", d.Vendor, "method", r.Method, "error", err.Error())
				if errors.Is(err, context.Canceled) {
					return
				}
				const msg = "the Jentic broker could not be reached from the session's egress proxy."
				if git {
					writeGitText(w, http.StatusBadGateway, "Jentic: "+msg+"\n")
					return
				}
				writeVendorError(w, d.Vendor, http.StatusBadGateway, msg)
			},
			ErrorLog: slog.NewLogLogger(s.log.Handler(), slog.LevelDebug),
		}
	}
	rp, gitRP := newRelay(false), newRelay(true)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if h, ok := NormalizeHost(hostOnly(r.Host)); !ok || h != d.Host || !defaultHTTPSPort(r.Host) {
			writeVendorError(w, d.Vendor, http.StatusMisdirectedRequest,
				"the request's Host does not match the host this connection was opened for.")
			return
		}
		if !strings.HasPrefix(r.RequestURI, "/") {
			writeVendorError(w, d.Vendor, http.StatusBadRequest, "only origin-form request targets are relayed.")
			return
		}
		if r.Header.Get("Upgrade") != "" {
			writeVendorError(w, d.Vendor, http.StatusNotImplemented, "protocol upgrades are not relayed.")
			return
		}
		switch classifyGit(d.Host, r.Method, r.URL) {
		case gitRelay:
			// A Go server may stop reading the request body once the
			// response has started; git's own exchanges are half duplex,
			// but the broker's answer must never cut a push short.
			_ = http.NewResponseController(w).EnableFullDuplex()
			gitRP.ServeHTTP(w, r)
		case gitRefuse:
			s.log.Warn("egress refused", "host", d.Host, "vendor", d.Vendor, "method", r.Method,
				"path", r.URL.Path, "reason", "git request Jentic does not serve")
			writeGitText(w, http.StatusForbidden, gitRefusedMessage)
		case webRefuse:
			s.log.Warn("egress refused", "host", d.Host, "vendor", d.Vendor, "method", r.Method,
				"path", r.URL.Path, "reason", "not git on "+gitHost)
			writeGitText(w, http.StatusForbidden, webRefusedMessage)
		default:
			rp.ServeHTTP(w, r)
		}
	})
}

// sanitizeUpstreamHeaders removes what a relayed request must not carry to
// the broker: hop-by-hop headers (including any the Connection header names),
// the client's own Authorization and Proxy-Authorization, cookies, client
// forwarding headers, and any header whose value holds an invocation token.
// The proxy's Authorization (the invocation token) is set after.
func sanitizeUpstreamHeaders(h http.Header) {
	for _, v := range h.Values("Connection") {
		for _, name := range strings.Split(v, ",") {
			if name = strings.TrimSpace(name); name != "" {
				h.Del(name)
			}
		}
	}
	for _, name := range []string{
		"Connection", "Proxy-Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization",
		"Te", "Trailer", "Transfer-Encoding", "Upgrade", "Authorization", "Cookie",
		"Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-Ip",
	} {
		h.Del(name)
	}
	for name, values := range h {
		for _, v := range values {
			if containsTokenRe.MatchString(v) {
				h.Del(name)
				break
			}
		}
	}
}

func hostOnly(hostport string) string {
	if h, _, err := net.SplitHostPort(hostport); err == nil {
		return h
	}
	return hostport
}

func defaultHTTPSPort(hostport string) bool {
	_, port, err := net.SplitHostPort(hostport)
	if err != nil {
		return true // no port
	}
	n, err := strconv.Atoi(port)
	return err == nil && n == 443
}

// bufferedConn reads through the bufio.Reader that parsed the CONNECT, so
// bytes the client sent right behind it are not lost.
type bufferedConn struct {
	net.Conn
	r *bufio.Reader
}

func (c *bufferedConn) Read(p []byte) (int, error) { return c.r.Read(p) }

// oneConnListener hands http.Server a single connection and then blocks
// Accept until that connection is closed, so Serve returns only once the
// connection is done.
type oneConnListener struct {
	conn net.Conn
	once sync.Once
	done chan struct{}
}

func newOneConnListener(c net.Conn) *oneConnListener {
	l := &oneConnListener{done: make(chan struct{})}
	l.conn = &closeNotifyConn{Conn: c, done: l.done}
	return l
}

func (l *oneConnListener) Accept() (net.Conn, error) {
	var c net.Conn
	l.once.Do(func() { c = l.conn })
	if c != nil {
		return c, nil
	}
	<-l.done
	return nil, net.ErrClosed
}

func (l *oneConnListener) Close() error   { return nil }
func (l *oneConnListener) Addr() net.Addr { return l.conn.LocalAddr() }

type closeNotifyConn struct {
	net.Conn
	once sync.Once
	done chan struct{}
}

func (c *closeNotifyConn) Close() error {
	err := c.Conn.Close()
	c.once.Do(func() { close(c.done) })
	return err
}
