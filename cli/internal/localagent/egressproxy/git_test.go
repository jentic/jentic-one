package egressproxy

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestClassifyGit(t *testing.T) {
	cases := []struct {
		host, method, target string
		want                 gitRoute
	}{
		{"github.com", "GET", "/o/r.git/info/refs?service=git-upload-pack", gitRelay},
		{"github.com", "GET", "/o/r/info/refs?service=git-receive-pack", gitRelay},
		{"github.com", "GET", "/Octo-Org/my.repo_1.git/info/refs?service=git-upload-pack", gitRelay},
		{"github.com", "POST", "/o/r.git/git-upload-pack", gitRelay},
		{"github.com", "POST", "/o/r/git-receive-pack", gitRelay},
		// Dumb HTTP, archives, LFS, a wrong method or query, an odd shape.
		{"github.com", "GET", "/o/r.git/info/refs", gitRefuse},
		{"github.com", "GET", "/o/r.git/info/refs?service=git-upload-archive", gitRefuse},
		{"github.com", "GET", "/o/r.git/info/refs?service=git-upload-pack&service=git-receive-pack", gitRefuse},
		{"github.com", "POST", "/o/r.git/info/refs?service=git-upload-pack", gitRefuse},
		{"github.com", "GET", "/o/r.git/git-upload-pack", gitRefuse},
		{"github.com", "POST", "/o/r.git/git-upload-pack?x=1", gitRefuse},
		{"github.com", "POST", "/o/r.git/git-upload-archive", gitRefuse},
		{"github.com", "GET", "/o/r.git/HEAD", gitRefuse},
		{"github.com", "GET", "/o/r.git/objects/info/packs", gitRefuse},
		{"github.com", "POST", "/o/r.git/info/lfs/objects/batch", gitRefuse},
		{"github.com", "POST", "/o/sub/r.git/git-upload-pack", gitRefuse},
		{"github.com", "POST", "/o/.git/git-upload-pack", gitRefuse},
		{"github.com", "POST", "/../r/git-upload-pack", gitRefuse},
		{"github.com", "POST", "/o/r%2Fx/git-upload-pack", gitRefuse},
		// Not git on github.com: web pages and the OAuth endpoints.
		{"github.com", "GET", "/", webRefuse},
		{"github.com", "GET", "/o/r", webRefuse},
		{"github.com", "GET", "/o/r/blob/main/README.md", webRefuse},
		{"github.com", "POST", "/login/oauth/access_token", webRefuse},
		// Other governed hosts: the API is relayed, git-shaped paths are not.
		{"api.github.com", "GET", "/repos/o/r/git/refs", notGit},
		{"api.github.com", "GET", "/user", notGit},
		{"api.github.com", "GET", "/o/r.git/info/refs?service=git-upload-pack", gitRefuse},
		{"uploads.github.com", "POST", "/o/r/git-receive-pack", gitRefuse},
	}
	for _, c := range cases {
		u, err := url.ParseRequestURI(c.target)
		if err != nil {
			t.Fatal(err)
		}
		if got := classifyGit(c.host, c.method, u); got != c.want {
			t.Errorf("%s %s%s = %d, want %d", c.method, c.host, c.target, got, c.want)
		}
	}
}

func TestGitIsRelayedToTheBrokerWithTheInvocationTokenOnly(t *testing.T) {
	h := newHarness(t, harnessOpts{broker: func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/x-git-upload-pack-advertisement")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Jentic-Execution-Id", "exec_1")
		_, _ = io.WriteString(w, "001e# service=git-upload-pack\n0000")
	}})
	req, _ := http.NewRequest(http.MethodGet, "https://github.com/o/r.git/info/refs?service=git-upload-pack", nil)
	// What git would send after a 401: it must never reach the broker.
	req.SetBasicAuth("x-access-token", "ghp_clientsideSecret")
	req.Header.Set("Git-Protocol", "version=2")
	req.Header.Set("Accept", "*/*")
	req.Header.Set("User-Agent", "git/2.47.0")
	resp, err := h.client(t, "").Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || resp.Header.Get("Content-Type") != "application/x-git-upload-pack-advertisement" ||
		resp.Header.Get("Jentic-Execution-Id") != "exec_1" || !strings.HasPrefix(string(body), "001e# service=git-upload-pack") {
		t.Fatalf("ref advertisement not passed through: %d %v %q", resp.StatusCode, resp.Header, body)
	}
	got := h.broker.all()
	if len(got) != 1 {
		t.Fatalf("broker saw %d requests", len(got))
	}
	r := got[0]
	if r.RequestURI != "/_facade/proxy/https://github.com/o/r.git/info/refs?service=git-upload-pack" {
		t.Errorf("relayed to %q", r.RequestURI)
	}
	if auth := r.Header.Values("Authorization"); len(auth) != 1 || auth[0] != "Bearer "+sessionGitHubToken {
		t.Errorf("Authorization at the broker = %q, want only the session token", auth)
	}
	if r.Header.Get("Git-Protocol") != "version=2" || r.Header.Get("User-Agent") != "git/2.47.0" || r.Header.Get("Accept") != "*/*" {
		t.Errorf("git's headers not passed: %v", r.Header)
	}
	for name, values := range r.Header {
		for _, v := range values {
			if strings.Contains(v, "ghp_clientsideSecret") {
				t.Errorf("the client's credential reached the broker in %s", name)
			}
		}
	}
}

func TestGitUsesThePerCommandToken(t *testing.T) {
	h := newHarness(t, harnessOpts{})
	resp, err := h.client(t, commandToken).Post("https://github.com/o/r.git/git-upload-pack",
		"application/x-git-upload-pack-request", strings.NewReader("0000"))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if got := h.broker.all(); len(got) != 1 || got[0].Header.Get("Authorization") != "Bearer "+commandToken {
		t.Errorf("broker saw %v, want the per-command token", got)
	}
}

func TestGitChunkedGzipBodiesPassThroughUntouched(t *testing.T) {
	var gz bytes.Buffer
	zw := gzip.NewWriter(&gz)
	_, _ = zw.Write(bytes.Repeat([]byte("0032want 0123456789abcdef0123456789abcdef01234567\n"), 4000))
	_ = zw.Close()
	compressed := gz.Bytes()
	h := newHarness(t, harnessOpts{broker: func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/x-git-upload-pack-result")
		w.Header().Set("Content-Encoding", "gzip")
		_, _ = w.Write(compressed)
	}})
	pr, pw := io.Pipe()
	go func() {
		for off := 0; off < len(compressed); off += 1000 {
			_, _ = pw.Write(compressed[off:min(off+1000, len(compressed))])
		}
		_ = pw.Close()
	}()
	req, _ := http.NewRequest(http.MethodPost, "https://github.com/o/r/git-upload-pack", pr)
	req.Header.Set("Content-Type", "application/x-git-upload-pack-request")
	req.Header.Set("Content-Encoding", "gzip")
	req.Header.Set("Accept", "application/x-git-upload-pack-result")
	req.Header.Set("Accept-Encoding", "deflate, gzip")
	client := h.client(t, "")
	client.Transport.(*http.Transport).DisableCompression = true
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.Header.Get("Content-Encoding") != "gzip" || !bytes.Equal(body, compressed) {
		t.Errorf("gzip response altered: encoding %q, %d bytes (want %d)", resp.Header.Get("Content-Encoding"), len(body), len(compressed))
	}
	r := h.broker.all()[0]
	sum := sha256.Sum256(compressed)
	if r.BodySHA != hex.EncodeToString(sum[:]) || r.Header.Get("Content-Encoding") != "gzip" ||
		r.Header.Get("Content-Type") != "application/x-git-upload-pack-request" || r.Header.Get("Accept-Encoding") != "deflate, gzip" {
		t.Errorf("request reached the broker altered: %+v", r)
	}
	if len(r.TransferEncoding) != 1 || r.TransferEncoding[0] != "chunked" {
		t.Errorf("a chunked body must stay chunked, got %v", r.TransferEncoding)
	}
}

// TestGitBodiesStreamBothWays proves neither body is buffered: the broker
// answers the first half of a push before git has sent the second, and git
// reads the first part of the answer before the broker writes the rest.
func TestGitBodiesStreamBothWays(t *testing.T) {
	firstHalf := make(chan struct{})
	gotFirstPart := make(chan struct{})
	h := newHarness(t, harnessOpts{rawBroker: func(w http.ResponseWriter, r *http.Request) {
		_ = http.NewResponseController(w).EnableFullDuplex()
		buf := make([]byte, 4)
		if _, err := io.ReadFull(r.Body, buf); err != nil || string(buf) != "half" {
			http.Error(w, "bad first half", http.StatusBadRequest)
			return
		}
		close(firstHalf)
		w.Header().Set("Content-Type", "application/x-git-receive-pack-result")
		_, _ = io.WriteString(w, "part1")
		w.(http.Flusher).Flush()
		select {
		case <-gotFirstPart:
		case <-time.After(10 * time.Second):
			return
		}
		rest, _ := io.ReadAll(r.Body)
		_, _ = io.WriteString(w, "part2:"+string(rest))
	}})
	pr, pw := io.Pipe()
	go func() {
		_, _ = io.WriteString(pw, "half")
		select {
		case <-firstHalf:
		case <-time.After(10 * time.Second):
		}
		_, _ = io.WriteString(pw, "rest")
		_ = pw.Close()
	}()
	req, _ := http.NewRequest(http.MethodPost, "https://github.com/o/r.git/git-receive-pack", pr)
	req.Header.Set("Content-Type", "application/x-git-receive-pack-request")
	resp, err := h.client(t, "").Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	buf := make([]byte, 5)
	if _, err := io.ReadFull(resp.Body, buf); err != nil || string(buf) != "part1" {
		t.Fatalf("first part: %q %v", buf, err)
	}
	close(gotFirstPart)
	rest, _ := io.ReadAll(resp.Body)
	if string(rest) != "part2:rest" {
		t.Errorf("rest = %q", rest)
	}
}

func TestGitBroker401BecomesA403WithoutAChallenge(t *testing.T) {
	const msg = "Invalid or expired CLI invocation token\n"
	h := newHarness(t, harnessOpts{broker: func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("WWW-Authenticate", `Basic realm="Jentic"`)
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = io.WriteString(w, msg)
	}})
	client := h.client(t, "")
	resp, err := client.Get("https://github.com/o/r.git/info/refs?service=git-upload-pack")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden || resp.Header.Get("WWW-Authenticate") != "" || string(body) != msg {
		t.Errorf("git 401 = %d %q %q, want 403 with the message and no challenge", resp.StatusCode, resp.Header.Get("WWW-Authenticate"), body)
	}
	// A REST call keeps the broker's status: gh reads a 401 itself.
	resp, err = client.Get("https://api.github.com/user")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("REST 401 = %d", resp.StatusCode)
	}
}

func TestGitHubComRefusesWhatIsNotSmartHTTP(t *testing.T) {
	h := newHarness(t, harnessOpts{})
	client := h.client(t, "")
	for _, c := range []struct {
		method, url, want string
	}{
		{"GET", "https://github.com/o/r", "for git (clone, fetch, push) only"},
		{"GET", "https://github.com/", "for git (clone, fetch, push) only"},
		{"POST", "https://github.com/login/oauth/access_token", "for git (clone, fetch, push) only"},
		{"GET", "https://github.com/o/r.git/info/refs", "only git clone, fetch and push"},
		{"POST", "https://github.com/o/r.git/git-upload-archive", "only git clone, fetch and push"},
		{"POST", "https://github.com/o/r.git/info/lfs/objects/batch", "only git clone, fetch and push"},
		{"GET", "https://api.github.com/o/r.git/info/refs?service=git-upload-pack", "only git clone, fetch and push"},
	} {
		req, _ := http.NewRequest(c.method, c.url, strings.NewReader(""))
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != http.StatusForbidden || !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/plain") ||
			!strings.Contains(string(body), c.want) {
			t.Errorf("%s %s = %d %q %q", c.method, c.url, resp.StatusCode, resp.Header.Get("Content-Type"), body)
		}
	}
	if n := len(h.broker.all()); n != 0 {
		t.Errorf("%d refused requests reached the broker", n)
	}
}

func TestGitBrokerDownIsPlainText(t *testing.T) {
	h := newHarness(t, harnessOpts{brokerDown: true})
	client := h.client(t, "")
	resp, err := client.Get("https://github.com/o/r.git/info/refs?service=git-upload-pack")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadGateway || !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/plain") ||
		!strings.Contains(string(body), "could not be reached") {
		t.Errorf("broker down = %d %q %q", resp.StatusCode, resp.Header.Get("Content-Type"), body)
	}
}
