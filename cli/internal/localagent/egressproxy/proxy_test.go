package egressproxy

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httptrace"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	sessionGitHubToken = "cit_session-github-0123456789abcdefghijklmnopqrstuv" // pragma: allowlist secret
	commandToken       = "cit_command-github-0123456789abcdefghijklmnopqrstuv" // pragma: allowlist secret
)

type fakeTokens map[string]string

func (f fakeTokens) Session(v string) string { return f[v] }

type recorded struct {
	Method, RequestURI, Host string
	Header                   http.Header
	BodySHA                  string
	BodyLen                  int64
	TransferEncoding         []string
}

type brokerRecorder struct {
	mu   sync.Mutex
	reqs []recorded
}

func (b *brokerRecorder) all() []recorded {
	b.mu.Lock()
	defer b.mu.Unlock()
	return append([]recorded(nil), b.reqs...)
}

type harness struct {
	proxyURL *url.URL
	ca       *CA
	broker   *brokerRecorder
	srv      *Server
}

type harnessOpts struct {
	vendors []string
	tunnel  []string
	tokens  fakeTokens
	broker  http.HandlerFunc
	// rawBroker, when set, serves the broker without the recorder reading
	// the request body first (for streaming tests).
	rawBroker  http.HandlerFunc
	tunnelDial func(ctx context.Context, network, addr string) (net.Conn, error)
	brokerDown bool
}

func newHarness(t *testing.T, o harnessOpts) *harness {
	t.Helper()
	if o.vendors == nil {
		o.vendors = []string{"github", "cloudflare"}
	}
	if o.tokens == nil {
		o.tokens = fakeTokens{"github": sessionGitHubToken}
	}
	rec := &brokerRecorder{}
	handler := o.broker
	if handler == nil {
		handler = func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]string{"login": "octocat"})
		}
	}
	broker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if o.rawBroker != nil {
			o.rawBroker(w, r)
			return
		}
		h := sha256.New()
		n, _ := io.Copy(h, r.Body)
		rec.mu.Lock()
		rec.reqs = append(rec.reqs, recorded{
			Method: r.Method, RequestURI: r.RequestURI, Host: r.Host, Header: r.Header.Clone(),
			BodySHA: hex.EncodeToString(h.Sum(nil)), BodyLen: n, TransferEncoding: r.TransferEncoding,
		})
		rec.mu.Unlock()
		handler(w, r)
	}))
	t.Cleanup(broker.Close)
	brokerURL, _ := url.Parse(broker.URL)
	if o.brokerDown {
		broker.Close()
	}
	policy, err := NewPolicy(o.vendors, o.tunnel)
	if err != nil {
		t.Fatal(err)
	}
	ca, err := NewCA("test", policy.GovernedDomains())
	if err != nil {
		t.Fatal(err)
	}
	srv, err := New(Config{Policy: policy, CA: ca, Tokens: o.tokens, BrokerURL: brokerURL, TunnelDial: o.tunnelDial})
	if err != nil {
		t.Fatal(err)
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go func() { _ = srv.Serve(ln) }()
	t.Cleanup(func() { _ = srv.Close() })
	return &harness{proxyURL: &url.URL{Scheme: "http", Host: ln.Addr().String()}, ca: ca, broker: rec, srv: srv}
}

func (h *harness) client(t *testing.T, proxyUser string, extraRoots ...*x509.Certificate) *http.Client {
	t.Helper()
	pool := x509.NewCertPool()
	pool.AddCert(h.ca.Certificate())
	for _, c := range extraRoots {
		pool.AddCert(c)
	}
	pu := *h.proxyURL
	if proxyUser != "" {
		pu.User = url.UserPassword(proxyUser, "x")
	}
	return &http.Client{
		Timeout:   20 * time.Second,
		Transport: &http.Transport{Proxy: http.ProxyURL(&pu), TLSClientConfig: &tls.Config{RootCAs: pool}},
	}
}

func TestGovernedRequestReachesTheBrokerProxyRoute(t *testing.T) {
	h := newHarness(t, harnessOpts{})
	req, _ := http.NewRequest(http.MethodGet, "https://api.github.com/user?per_page=5&q=a%2Fb", nil)
	req.Header.Set("Authorization", "token gh-dummy")
	resp, err := h.client(t, "").Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || !strings.Contains(string(body), "octocat") {
		t.Fatalf("status %d body %s", resp.StatusCode, body)
	}
	reqs := h.broker.all()
	if len(reqs) != 1 {
		t.Fatalf("broker saw %d requests", len(reqs))
	}
	got := reqs[0]
	if got.RequestURI != "/_facade/proxy/https://api.github.com/user?per_page=5&q=a%2Fb" {
		t.Errorf("broker RequestURI = %q", got.RequestURI)
	}
	if got.Header.Get("Authorization") != "Bearer "+sessionGitHubToken {
		t.Errorf("broker Authorization = %q, want the session token", got.Header.Get("Authorization"))
	}
	if got.Host != h.proxyURL.Host && !strings.HasPrefix(got.Host, "127.0.0.1:") {
		t.Errorf("broker Host = %q, want the broker URL's host", got.Host)
	}
}

func TestHeaderHygiene(t *testing.T) {
	h := newHarness(t, harnessOpts{})
	req, _ := http.NewRequest(http.MethodPost, "https://api.github.com/repos/o/r/issues", strings.NewReader(`{"title":"x"}`))
	req.Header.Set("Authorization", "Bearer agent-own-token")
	req.Header.Set("Cookie", "session=abc")
	req.Header.Set("X-Leak", "token=cit_leaked-0123456789abcdefghijklmnop") // pragma: allowlist secret
	req.Header.Set("X-Forwarded-For", "10.0.0.1")
	req.Header.Set("Connection", "X-Hop")
	req.Header.Set("X-Hop", "1")
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("Proxy-Authorization", "Basic Zm9vOmJhcg==")
	resp, err := h.client(t, "").Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	got := h.broker.all()[0].Header
	for _, name := range []string{"Cookie", "X-Leak", "X-Forwarded-For", "X-Hop", "Proxy-Authorization", "Proxy-Connection", "Keep-Alive"} {
		if v := got.Get(name); v != "" {
			t.Errorf("%s reached the broker: %q", name, v)
		}
	}
	if got.Get("Accept") != "application/vnd.github+json" {
		t.Errorf("an ordinary header must be relayed, got Accept=%q", got.Get("Accept"))
	}
	if auth := got.Values("Authorization"); len(auth) != 1 || auth[0] != "Bearer "+sessionGitHubToken {
		t.Errorf("Authorization = %v, want only the proxy's invocation token", auth)
	}
	for name, vs := range got {
		for _, v := range vs {
			if name != "Authorization" && strings.Contains(v, "cit_") {
				t.Errorf("header %s carries an invocation token upstream", name)
			}
		}
	}
}

func TestPerCommandProxyCredentialsOverrideTheSessionToken(t *testing.T) {
	h := newHarness(t, harnessOpts{})
	resp, err := h.client(t, commandToken).Get("https://api.github.com/user")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if got := h.broker.all()[0].Header.Get("Authorization"); got != "Bearer "+commandToken {
		t.Errorf("Authorization = %q, want the per-command token", got)
	}
}

// rawConnect sends one CONNECT on conn and returns the parsed response.
func rawConnect(t *testing.T, conn net.Conn, br *bufio.Reader, target, proxyAuth string) *http.Response {
	t.Helper()
	req := "CONNECT " + target + " HTTP/1.1\r\nHost: " + target + "\r\n"
	if proxyAuth != "" {
		req += "Proxy-Authorization: " + proxyAuth + "\r\n"
	}
	if _, err := io.WriteString(conn, req+"\r\n"); err != nil {
		t.Fatal(err)
	}
	resp, err := http.ReadResponse(br, &http.Request{Method: http.MethodConnect})
	if err != nil {
		t.Fatal(err)
	}
	return resp
}

func TestProxyAuthenticationChallengeOnTheSameConnection(t *testing.T) {
	h := newHarness(t, harnessOpts{tokens: fakeTokens{}})
	conn, err := net.Dial("tcp", h.proxyURL.Host)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	br := bufio.NewReader(conn)

	resp := rawConnect(t, conn, br, "api.github.com:443", "")
	_, _ = io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusProxyAuthRequired {
		t.Fatalf("CONNECT without credentials and no session token = %d, want 407", resp.StatusCode)
	}
	if got := resp.Header.Get("Proxy-Authenticate"); !strings.HasPrefix(got, "Basic realm=") {
		t.Errorf("Proxy-Authenticate = %q", got)
	}
	resp = rawConnect(t, conn, br, "api.github.com:443", "Bearer "+commandToken)
	_, _ = io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusProxyAuthRequired {
		t.Fatalf("non-Basic proxy credentials = %d, want 407", resp.StatusCode)
	}
	bad := base64.StdEncoding.EncodeToString([]byte("alice:secret"))
	resp = rawConnect(t, conn, br, "api.github.com:443", "Basic "+bad)
	_, _ = io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusProxyAuthRequired {
		t.Fatalf("a proxy user that is not an invocation token = %d, want 407", resp.StatusCode)
	}
	good := base64.StdEncoding.EncodeToString([]byte(commandToken + ":x"))
	resp = rawConnect(t, conn, br, "api.github.com:443", "Basic "+good)
	resp.Body.Close() // a 2xx CONNECT response has no body
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("CONNECT with credentials on the same connection = %d, want 200", resp.StatusCode)
	}

	pool := x509.NewCertPool()
	pool.AddCert(h.ca.Certificate())
	tc := tls.Client(&bufferedConn{Conn: conn, r: br}, &tls.Config{ServerName: "api.github.com", RootCAs: pool, NextProtos: []string{"http/1.1"}})
	if err := tc.Handshake(); err != nil {
		t.Fatal(err)
	}
	if tc.ConnectionState().NegotiatedProtocol != "http/1.1" {
		t.Errorf("ALPN = %q, want http/1.1", tc.ConnectionState().NegotiatedProtocol)
	}
	_, _ = io.WriteString(tc, "GET /user HTTP/1.1\r\nHost: api.github.com\r\n\r\n")
	r2, err := http.ReadResponse(bufio.NewReader(tc), nil)
	if err != nil {
		t.Fatal(err)
	}
	r2.Body.Close()
	if got := h.broker.all()[0].Header.Get("Authorization"); got != "Bearer "+commandToken {
		t.Errorf("Authorization = %q, want the token from the proxy credentials", got)
	}
}

func TestTunnelHostIsNotDecrypted(t *testing.T) {
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, "from the real upstream")
	}))
	defer upstream.Close()
	var dialled string
	h := newHarness(t, harnessOpts{
		tunnel: []string{"api.anthropic.com"},
		tunnelDial: func(ctx context.Context, network, addr string) (net.Conn, error) {
			dialled = addr
			return (&net.Dialer{}).DialContext(ctx, network, upstream.Listener.Addr().String())
		},
	})
	upCert := upstream.Certificate()
	pool := x509.NewCertPool()
	pool.AddCert(upCert)
	client := &http.Client{Transport: &http.Transport{
		Proxy: http.ProxyURL(h.proxyURL),
		// Trust only the upstream's own certificate (issued for example.com):
		// a TLS-terminating proxy could not present it.
		TLSClientConfig: &tls.Config{RootCAs: pool, ServerName: "example.com"},
	}}
	resp, err := client.Get("https://api.anthropic.com/v1/messages")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if string(body) != "from the real upstream" || dialled != "api.anthropic.com:443" {
		t.Errorf("tunnel: body %q dialled %q", body, dialled)
	}
	if !resp.TLS.PeerCertificates[0].Equal(upCert) {
		t.Error("the tunnel presented a certificate other than the upstream's")
	}
	if len(h.broker.all()) != 0 {
		t.Error("a tunnelled request must never reach the broker")
	}
}

func TestRefusals(t *testing.T) {
	h := newHarness(t, harnessOpts{tunnel: []string{"api.anthropic.com"}})
	client := h.client(t, "")
	for _, target := range []string{"https://example.com/", "https://140.82.112.3/", "https://api.github.com:8443/"} {
		resp, err := client.Get(target)
		if err == nil {
			resp.Body.Close()
			t.Errorf("GET %s through the proxy must fail", target)
			continue
		}
		if !strings.Contains(err.Error(), "Forbidden") {
			t.Errorf("GET %s: %v, want a 403 CONNECT refusal", target, err)
		}
	}
	// Plain HTTP proxying (absolute-form GET).
	conn, err := net.Dial("tcp", h.proxyURL.Host)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	_, _ = io.WriteString(conn, "GET http://api.github.com/user HTTP/1.1\r\nHost: api.github.com\r\n\r\n")
	resp, err := http.ReadResponse(bufio.NewReader(conn), nil)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Errorf("plain HTTP proxying = %d, want 403", resp.StatusCode)
	}
	if len(h.broker.all()) != 0 {
		t.Error("a refused request must never reach the broker")
	}
}

func TestStreamingKeepAliveAndChunkedBodies(t *testing.T) {
	const size = 8 << 20
	h := newHarness(t, harnessOpts{broker: func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			w.Header().Set("Content-Type", "application/octet-stream")
			chunk := bytes.Repeat([]byte("z"), 64<<10)
			for written := 0; written < size; written += len(chunk) {
				_, _ = w.Write(chunk)
			}
			return
		}
		w.WriteHeader(http.StatusCreated)
	}})
	client := h.client(t, "")

	// A chunked upload of unknown length.
	pr, pw := io.Pipe()
	payload := bytes.Repeat([]byte("upload!"), size/7)
	go func() {
		for off := 0; off < len(payload); off += 100_000 {
			end := min(off+100_000, len(payload))
			_, _ = pw.Write(payload[off:end])
		}
		_ = pw.Close()
	}()
	req, _ := http.NewRequest(http.MethodPost, "https://uploads.github.com/repos/o/r/releases/1/assets?name=a.bin", pr)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	sum := sha256.Sum256(payload)
	if got := h.broker.all()[0]; got.BodySHA != hex.EncodeToString(sum[:]) || got.BodyLen != int64(len(payload)) {
		t.Errorf("upload reached the broker as %d bytes (sha %s)", got.BodyLen, got.BodySHA)
	}

	// A large streamed download, then a second request on the same connection.
	reused := false
	for i := range 2 {
		ctx := httptrace.WithClientTrace(context.Background(), &httptrace.ClientTrace{
			GotConn: func(info httptrace.GotConnInfo) { reused = reused || info.Reused },
		})
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, "https://uploads.github.com/blob", nil)
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		n, _ := io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		if n != size {
			t.Errorf("download %d: %d bytes, want %d", i, n, size)
		}
	}
	if !reused {
		t.Error("a governed connection must be kept alive across requests")
	}
}

func TestBrokerErrorsPassThroughAndProxyErrorsAreVendorShaped(t *testing.T) {
	h := newHarness(t, harnessOpts{broker: func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		_, _ = io.WriteString(w, `{"message":"Jentic: denied by rule","status":"403"}`)
	}})
	resp, err := h.client(t, "").Get("https://api.github.com/user")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden || string(body) != `{"message":"Jentic: denied by rule","status":"403"}` {
		t.Errorf("broker error not passed through: %d %s", resp.StatusCode, body)
	}

	down := newHarness(t, harnessOpts{brokerDown: true, tokens: fakeTokens{"github": sessionGitHubToken, "cloudflare": sessionGitHubToken}})
	for host, check := range map[string]func(map[string]any) bool{
		"api.github.com": func(b map[string]any) bool {
			m, _ := b["message"].(string)
			return strings.HasPrefix(m, "Jentic: ") && b["status"] == "502"
		},
		"api.cloudflare.com": func(b map[string]any) bool {
			errs, _ := b["errors"].([]any)
			return b["success"] == false && len(errs) == 1
		},
	} {
		resp, err := down.client(t, "").Get("https://" + host + "/x")
		if err != nil {
			t.Fatal(err)
		}
		var b map[string]any
		_ = json.NewDecoder(resp.Body).Decode(&b)
		resp.Body.Close()
		if resp.StatusCode != http.StatusBadGateway || !check(b) {
			t.Errorf("%s with the broker down: %d %v", host, resp.StatusCode, b)
		}
	}
}

func TestMisdirectedHostAndSNI(t *testing.T) {
	h := newHarness(t, harnessOpts{})
	req, _ := http.NewRequest(http.MethodGet, "https://api.github.com/user", nil)
	req.Host = "uploads.github.com"
	resp, err := h.client(t, "").Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusMisdirectedRequest {
		t.Errorf("Host other than the CONNECT target = %d, want 421", resp.StatusCode)
	}

	pool := x509.NewCertPool()
	pool.AddCert(h.ca.Certificate())
	sniClient := &http.Client{Transport: &http.Transport{
		Proxy:           http.ProxyURL(h.proxyURL),
		TLSClientConfig: &tls.Config{RootCAs: pool, ServerName: "uploads.github.com"},
	}}
	if resp, err := sniClient.Get("https://api.github.com/user"); err == nil {
		resp.Body.Close()
		t.Error("an SNI other than the CONNECT target must fail the handshake")
	}
	if len(h.broker.all()) != 0 {
		t.Error("misdirected requests must never reach the broker")
	}
}

func TestNewRefusesAGovernedPolicyWithoutAMatchingCA(t *testing.T) {
	p, _ := NewPolicy([]string{"github", "aws"}, nil)
	ca, _ := NewCA("x", []string{"github.com"})
	u, _ := url.Parse("http://127.0.0.1:8100")
	if _, err := New(Config{Policy: p, Tokens: fakeTokens{}, BrokerURL: u}); err == nil {
		t.Error("governing without a CA must be refused")
	}
	if _, err := New(Config{Policy: p, CA: ca, Tokens: fakeTokens{}, BrokerURL: u}); err == nil {
		t.Error("a CA that does not permit every governed domain must be refused")
	}
}

func TestPublicDialerRefusesNonPublicAddresses(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	if c, err := publicDialer().Dial("tcp", ln.Addr().String()); err == nil {
		c.Close()
		t.Error("a tunnel to a loopback address must be refused")
	}
	for _, ip := range []string{"10.1.2.3", "172.17.0.1", "192.168.65.254", "169.254.169.254", "fd00::1", "::1", "0.0.0.0"} {
		if publicUnicast(net.ParseIP(ip)) {
			t.Errorf("%s must not count as public", ip)
		}
	}
	if !publicUnicast(net.ParseIP("160.79.104.10")) {
		t.Error("a public address must be allowed")
	}
}
