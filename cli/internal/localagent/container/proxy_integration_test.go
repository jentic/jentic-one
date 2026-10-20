//go:build !windows

package container

// End to end with the "proxy" egress against a real Docker engine, opt-in
// like integration_test.go (JENTIC_CONTAINER_IT=1, all three images built;
// JENTIC_CONTAINER_IT_PROXY_IMAGE names another proxy image). A stand-in
// broker on the host's loopback records what reaches it and serves git smart
// HTTP from a local `git http-backend`; the agent container runs gh, curl,
// Node and GnuTLS git (a clone and a push) through the proxy, probes what it
// can see of the proxy's secrets, and a python:3.13 container in the same
// network namespace checks the CA under Python's strict X.509 mode. The test
// then checks what the stand-in broker received, and that teardown removes
// every object and revokes every session token.

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/cgi"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

const pythonImage = "python:3.13-slim"

type brokerCall struct {
	RequestURI    string
	Authorization string
	Host          string
	UserAgent     string
	Chunked       bool
}

// itProxyImage is the proxy image the end-to-end test runs.
func itProxyImage() string {
	if img := os.Getenv("JENTIC_CONTAINER_IT_PROXY_IMAGE"); img != "" {
		return img
	}
	return DefaultProxyImage
}

// gitProxyPrefix is the proxy route's prefix for git to github.com.
const gitProxyPrefix = "/_facade/proxy/https://github.com"

// standInBroker answers the proxy route like the broker would for a GitHub
// user call, serves git smart HTTP to github.com/o/r.git from a bare
// repository on the host through `git http-backend` (gitRoot), and records
// every request.
func standInBroker(t *testing.T, gitRoot string) (int, func() []brokerCall) {
	t.Helper()
	gitBin, err := exec.LookPath("git")
	if err != nil {
		t.Skip("git is not installed on the host")
	}
	backend := &cgi.Handler{
		Path: gitBin, Args: []string{"http-backend"}, Root: gitProxyPrefix,
		Env: []string{"GIT_PROJECT_ROOT=" + gitRoot, "GIT_HTTP_EXPORT_ALL=1"},
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var calls []brokerCall
	srv := &http.Server{
		ReadHeaderTimeout: 5 * time.Second,
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			mu.Lock()
			calls = append(calls, brokerCall{
				RequestURI: r.RequestURI, Authorization: r.Header.Get("Authorization"), Host: r.Host,
				UserAgent: r.UserAgent(), Chunked: slices.Contains(r.TransferEncoding, "chunked"),
			})
			mu.Unlock()
			if strings.HasPrefix(r.URL.Path, gitProxyPrefix+"/") {
				// CGI cannot take a chunked body (git sends a large push
				// chunked), so the stand-in reads it first, as the real
				// broker spools a push.
				if r.ContentLength < 0 {
					body, err := io.ReadAll(r.Body)
					if err != nil {
						http.Error(w, err.Error(), http.StatusBadRequest)
						return
					}
					r.Body, r.ContentLength, r.TransferEncoding = io.NopCloser(bytes.NewReader(body)), int64(len(body)), nil
				}
				backend.ServeHTTP(w, r)
				return
			}
			w.Header().Set("Content-Type", "application/json")
			if r.RequestURI == "/_facade/proxy/https://api.github.com/user" {
				_, _ = w.Write([]byte(`{"login":"standin-octocat","id":1}`))
				return
			}
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"message":"Jentic: not found","status":"404"}`))
		}),
	}
	go func() { _ = srv.Serve(ln) }()
	t.Cleanup(func() { _ = srv.Close() })
	return ln.Addr().(*net.TCPAddr).Port, func() []brokerCall {
		mu.Lock()
		defer mu.Unlock()
		return append([]brokerCall(nil), calls...)
	}
}

// bareRepo creates <root>/o/r.git with one commit on main (a 2 MB file, so
// the clone's pack is not trivially small) that accepts pushes over HTTP.
func bareRepo(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	src := filepath.Join(t.TempDir(), "src")
	bare := filepath.Join(root, "o", "r.git")
	run := func(dir string, args ...string) {
		t.Helper()
		cmd := exec.Command("git", args...)
		cmd.Dir = dir
		cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_SYSTEM=/dev/null",
			"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@t", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@t")
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}
	if err := os.MkdirAll(src, 0o755); err != nil {
		t.Fatal(err)
	}
	run(src, "init", "-q", "-b", "main")
	blob := bytes.Repeat([]byte("jentic governed git "), 100_000)
	if err := os.WriteFile(filepath.Join(src, "big.txt"), blob, 0o644); err != nil {
		t.Fatal(err)
	}
	run(src, "add", "big.txt")
	run(src, "commit", "-q", "-m", "seed commit")
	run(root, "clone", "-q", "--bare", src, bare)
	run(bare, "config", "http.receivepack", "true")
	return bare
}

const proxyProbeScript = `
set -u
echo "gh=$(gh api /user 2>&1 | tr -d '\n')"
echo "curl=$(curl -sS -m 15 https://api.github.com/user 2>&1 | tr -d '\n')"
echo "node=$(node -e 'fetch("https://api.github.com/user").then(r=>r.text()).then(t=>console.log(t)).catch(e=>console.log("ERR "+(e.cause||e)))' 2>&1 | tr -d '\n')"
if curl -sS -m 10 -o /dev/null https://example.com 2>/tmp/err; then echo example=reached; else echo "example=refused $(tr -d '\n' </tmp/err)"; fi
echo "plainhttp=$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://example.com/)"
echo "tunnel_issuer=$(curl -sS -m 20 -o /dev/null -v https://api.anthropic.com/ 2>&1 | grep -i 'issuer:' | head -1 | tr -d '\n')"
cd /tmp
if git clone -q https://github.com/o/r.git c </dev/null >/tmp/clone 2>&1; then echo "gitclone=ok $(git -C c log -1 --format=%s)"; else echo "gitclone=failed $(tr '\n' ' ' </tmp/clone)"; fi
cd /tmp/c 2>/dev/null && head -c 3000000 /dev/urandom >blob.bin && git add blob.bin && git -c user.email=a@b.c -c user.name=a commit -q -m agentcommit
if git push origin HEAD:refs/heads/jentic-e2e/agent </dev/null >/tmp/push 2>&1; then echo gitpush=ok; else echo "gitpush=failed $(tr '\n' ' ' </tmp/push)"; fi
echo "githead=$(git rev-parse HEAD 2>&1)"
echo "gitweb=$(curl -sS -m 10 https://github.com/o/r 2>&1 | tr -d '\n')"
echo "gitdumb=$(curl -s -m 10 -o /dev/null -w '%{http_code}' https://github.com/o/r.git/info/refs)"
echo "gnutls=$(ldd /usr/lib/git-core/git-remote-https | grep -c gnutls)"
echo "cadir=$(ls -A /run/jentic-ca | tr '\n' ' ')"
if touch /run/jentic-ca/x 2>/dev/null; then echo cawrite=yes; else echo cawrite=no; fi
if [ -e /run/jentic-proxy ]; then echo tokendir=yes; else echo tokendir=no; fi
echo "keys=$(grep -rl 'PRIVATE KEY' /run /tmp /home 2>/dev/null | wc -l)"
echo "envtokens=$(env | grep -c cit_)"
echo "proxyproc=$(ps -eo comm | grep -c jentic-egress)"
echo "dns=$(getent hosts example.com >/dev/null 2>&1 && echo yes || echo no)"
`

const pythonProbe = `
import json, os, ssl, urllib.request
ctx = ssl.create_default_context()
print("strict=" + str(bool(ctx.verify_flags & ssl.VERIFY_X509_STRICT)))
try:
    with urllib.request.urlopen("https://api.github.com/user", context=ctx, timeout=15) as r:
        print("python=" + r.read().decode())
except Exception as e:
    print("python=ERR " + repr(e))
`

func TestProxyEgressEndToEnd(t *testing.T) {
	d, engine := requireDocker(t)
	ctx := context.Background()
	if _, err := Preflight(ctx, d, itProxyImage()); err != nil {
		t.Skipf("the proxy image is not available: %v", err)
	}
	bare := bareRepo(t)
	port, brokerCalls := standInBroker(t, filepath.Dir(filepath.Dir(bare)))
	work := itWorkDir(t)

	iss := newIssuer(10*time.Minute, time.Hour)
	keeper, err := MintSessionTokens(ctx, iss, []string{"cloudflare", "github"})
	if err != nil {
		t.Fatal(err)
	}
	ghToken := keeper.Tokens()["github"]
	nonce, err := NewNonce()
	if err != nil {
		t.Fatal(err)
	}
	egress := &ProxyEgress{
		Image:      itProxyImage(),
		Vendors:    keeper.Vendors(),
		Tunnel:     []string{"api.anthropic.com"},
		BrokerURL:  "http://127.0.0.1:" + strconv.Itoa(port),
		BrokerPort: port,
		Nonce:      nonce,
		Tokens:     keeper.Tokens,
	}
	id, err := NewSessionID()
	if err != nil {
		t.Fatal(err)
	}
	uid := itUID(engine)
	p, err := NewPlan(Options{
		SessionID: id, Owner: OwnerLabel(os.Getuid(), "container-proxy-it"),
		AgentImage: DefaultAgentImage, BridgeImage: DefaultBridgeImage,
		WorkDir: work, JenticPorts: []int{port}, Route: RouteFor(engine, runtime.GOOS),
		AgentUser: fmt.Sprintf("%d:%d", uid, uid), Egress: egress,
		Command: []string{"bash", "-c", proxyProbeScript},
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = Teardown(context.Background(), d, p.SessionID)
		_ = keeper.Close(context.Background())
	})
	sess := &Session{Plan: p, Docker: d, ReadyTimeout: 30 * time.Second}
	if err := sess.Up(ctx); err != nil {
		t.Fatalf("Up: %v", err)
	}
	keeper.Run(ctx, func(ctx context.Context, tokens map[string]string) error {
		return egress.PushTokens(ctx, d, p, tokens)
	})
	archive, err := HomeArchive(uid, uid, []HomeFile{{Rel: ".config/jentic/config.yaml", Data: []byte("x: 1\n")}})
	if err != nil {
		t.Fatal(err)
	}
	if err := sess.Seed(ctx, bytes.NewReader(archive)); err != nil {
		t.Fatal(err)
	}
	out, err := sess.AttachCmd(ctx).CombinedOutput()
	if err != nil {
		t.Fatalf("agent: %v\n%s", err, out)
	}
	t.Logf("agent probe output:\n%s", out)
	got := parseProbe(string(out))

	for _, key := range []string{"gh", "curl", "node"} {
		if !strings.Contains(got[key], "standin-octocat") {
			t.Errorf("%s did not reach the stand-in broker through the proxy: %q", key, got[key])
		}
	}
	if !strings.HasPrefix(got["example"], "refused") {
		t.Errorf("example.com must be refused: %q", got["example"])
	}
	if got["plainhttp"] != "403" {
		t.Errorf("plain HTTP through the proxy = %q, want 403", got["plainhttp"])
	}
	switch issuer := got["tunnel_issuer"]; {
	case issuer == "":
		t.Log("api.anthropic.com was not reachable from this engine (no internet?); the tunnel check is skipped")
	case strings.Contains(issuer, "Jentic"):
		t.Errorf("a tunnel host was decrypted: %q", issuer)
	}
	if got["gitclone"] != "ok seed commit" {
		t.Errorf("git clone through the proxy: %q", got["gitclone"])
	}
	if got["gitpush"] != "ok" {
		t.Errorf("git push through the proxy: %q", got["gitpush"])
	}
	if pushed, err := exec.Command("git", "--git-dir", bare, "rev-parse", "refs/heads/jentic-e2e/agent").Output(); err != nil ||
		strings.TrimSpace(string(pushed)) != got["githead"] {
		t.Errorf("the pushed ref on the stand-in = %q (%v), want the agent's HEAD %q", pushed, err, got["githead"])
	}
	if !strings.Contains(got["gitweb"], "for git (clone, fetch, push) only") {
		t.Errorf("a github.com web page must be refused: %q", got["gitweb"])
	}
	if got["gitdumb"] != "403" {
		t.Errorf("dumb HTTP to github.com = %q, want 403", got["gitdumb"])
	}
	if got["gnutls"] == "0" {
		t.Errorf("the agent image's git is expected to use GnuTLS")
	}
	for key, want := range map[string]string{
		"cadir": "bundle.pem ca.pem ready ", "cawrite": "no", "tokendir": "no", "keys": "0",
		"envtokens": "0", "proxyproc": "0", "dns": "no",
	} {
		if got[key] != want {
			t.Errorf("%s = %q, want %q", key, got[key], want)
		}
	}

	checkPython313(t, d, p)

	calls := brokerCalls()
	user := 0
	gitCalls := map[string]int{}
	for _, c := range calls {
		for _, suffix := range []string{
			"/info/refs?service=git-upload-pack", "/git-upload-pack",
			"/info/refs?service=git-receive-pack", "/git-receive-pack",
		} {
			if strings.HasPrefix(c.RequestURI, gitProxyPrefix+"/o/r.git/") && strings.HasSuffix(c.RequestURI, suffix) {
				gitCalls[suffix]++
			}
		}
		if strings.HasPrefix(c.RequestURI, gitProxyPrefix) && !strings.HasPrefix(c.UserAgent, "git/") {
			t.Errorf("a non-git request to github.com reached the broker: %+v", c)
		}
		if c.Authorization != "Bearer "+ghToken {
			t.Errorf("broker call %s carried %q, want the session's github token", c.RequestURI, c.Authorization)
		}
		if c.Host != "127.0.0.1:"+strconv.Itoa(port) {
			t.Errorf("broker call Host = %q, want the broker URL's host", c.Host)
		}
		if c.RequestURI == "/_facade/proxy/https://api.github.com/user" {
			user++
		}
		if strings.Contains(c.RequestURI, "example.com") || strings.Contains(c.RequestURI, "anthropic") {
			t.Errorf("a refused or tunnelled request reached the broker: %s", c.RequestURI)
		}
	}
	chunkedPush := false
	for _, c := range calls {
		chunkedPush = chunkedPush || (c.Chunked && strings.HasSuffix(c.RequestURI, "/git-receive-pack"))
	}
	if !chunkedPush {
		t.Error("git's chunked push body must reach the broker chunked")
	}
	if len(gitCalls) != 4 {
		t.Errorf("the broker saw git requests %v, want ref discovery and an RPC for both a fetch and a push", gitCalls)
	}
	if user < 3 {
		t.Errorf("the broker saw %d GitHub user calls, want gh, curl, node (and python): %+v", user, calls)
	}

	// The proxy logs to stderr, which `docker logs` replays on its own stderr.
	logs, _ := d.Command(ctx, "logs", ProxyName(p)).CombinedOutput()
	if !bytes.Contains(logs, []byte("egress refused")) || !bytes.Contains(logs, []byte(`"host":"example.com"`)) {
		t.Errorf("the proxy must log the refused host:\n%s", logs)
	}
	if leaked, _ := d.Output(ctx, "exec", ProxyName(p), "sh", "-c",
		"grep -l 'PRIVATE KEY' /run/jentic-ca/* /run/jentic-proxy/* 2>/dev/null | wc -l"); strings.TrimSpace(leaked) != "0" {
		t.Errorf("private key material on the proxy's disk: %s", leaked)
	}

	if err := Teardown(ctx, d, p.SessionID); err != nil {
		t.Fatalf("Teardown: %v", err)
	}
	if left := leftovers(t, d, p.SessionID); len(left) > 0 {
		t.Errorf("teardown left %v", left)
	}
	if err := keeper.Close(ctx); err != nil {
		t.Fatal(err)
	}
	minted, revoked, live := iss.snapshot()
	if live != 0 || len(revoked) != len(minted) {
		t.Errorf("teardown must revoke every session token: minted %v revoked %v", minted, revoked)
	}
}

// checkPython313 runs Python 3.13's urllib, in strict X.509 mode, from the
// session's network namespace with the agent's proxy and CA environment.
func checkPython313(t *testing.T, d CLI, p *Plan) {
	t.Helper()
	ctx := context.Background()
	if _, err := d.Output(ctx, "image", "inspect", pythonImage); err != nil {
		if _, err := d.Output(ctx, "pull", "--quiet", pythonImage); err != nil {
			t.Logf("cannot pull %s (%v); the Python strict-mode check is skipped", pythonImage, err)
			return
		}
		t.Cleanup(func() { _, _ = d.Output(context.Background(), "image", "rm", pythonImage) })
	}
	env := slices.DeleteFunc(slices.Clone(p.Agent.Env), func(e string) bool { return strings.HasPrefix(e, "HOME=") })
	spec := Spec{
		Name: "jentic-run-" + p.SessionID + "-python", Image: pythonImage, Labels: p.labels(RoleAgent),
		Network: p.Agent.Network, User: p.AgentUser, ReadOnlyRoot: true, Tmpfs: []string{"/tmp"},
		Mounts: p.Egress.AgentMounts(p), Env: append(env, "HOME=/tmp"),
		Cmd: []string{"python", "-I", "-c", pythonProbe},
	}
	args, err := spec.RunArgs()
	if err != nil {
		t.Fatal(err)
	}
	out, err := d.Output(ctx, args...)
	if err != nil {
		t.Fatalf("python: %v\n%s", err, out)
	}
	got := parseProbe(out)
	if got["strict"] != "True" || !strings.Contains(got["python"], "standin-octocat") {
		t.Errorf("Python 3.13 strict mode through the proxy: %s", out)
	}
}

func parseProbe(out string) map[string]string {
	got := map[string]string{}
	for _, line := range strings.Split(out, "\n") {
		if k, v, ok := strings.Cut(strings.TrimRight(line, "\r"), "="); ok {
			got[k] = v
		}
	}
	return got
}
