//go:build !windows

package container

// End to end with the "proxy" egress against a real Docker engine, opt-in
// like integration_test.go (JENTIC_CONTAINER_IT=1, all three images built). A
// stand-in broker on the host's loopback records what reaches it; the agent
// container runs gh, curl, Node and GnuTLS git through the proxy, probes what
// it can see of the proxy's secrets, and a python:3.13 container in the same
// network namespace checks the CA under Python's strict X.509 mode. The test
// then checks what the stand-in broker received, and that teardown removes
// every object and revokes every session token.

import (
	"bytes"
	"context"
	"fmt"
	"net"
	"net/http"
	"os"
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
}

// standInBroker answers the proxy route like the broker would for a GitHub
// user call and records every request.
func standInBroker(t *testing.T) (int, func() []brokerCall) {
	t.Helper()
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
			calls = append(calls, brokerCall{r.RequestURI, r.Header.Get("Authorization"), r.Host, r.UserAgent()})
			mu.Unlock()
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

const proxyProbeScript = `
set -u
echo "gh=$(gh api /user 2>&1 | tr -d '\n')"
echo "curl=$(curl -sS -m 15 https://api.github.com/user 2>&1 | tr -d '\n')"
echo "node=$(node -e 'fetch("https://api.github.com/user").then(r=>r.text()).then(t=>console.log(t)).catch(e=>console.log("ERR "+(e.cause||e)))' 2>&1 | tr -d '\n')"
if curl -sS -m 10 -o /dev/null https://example.com 2>/tmp/err; then echo example=reached; else echo "example=refused $(tr -d '\n' </tmp/err)"; fi
echo "plainhttp=$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://example.com/)"
echo "tunnel_issuer=$(curl -sS -m 20 -o /dev/null -v https://api.anthropic.com/ 2>&1 | grep -i 'issuer:' | head -1 | tr -d '\n')"
cd /tmp && git init -q -b main r && cd r && git -c user.email=a@b.c -c user.name=a commit -q --allow-empty -m x
echo "gitpush=$(git push https://github.com/o/r.git HEAD:main 2>&1 | tr '\n' ' ')"
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
	if _, err := Preflight(ctx, d, DefaultProxyImage); err != nil {
		t.Skipf("the proxy image is not available: %v", err)
	}
	port, brokerCalls := standInBroker(t)
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
		Image:      DefaultProxyImage,
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
	if !strings.Contains(got["gitpush"], "not supported yet") {
		t.Errorf("git push must get the clear refusal: %q", got["gitpush"])
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
	for _, c := range calls {
		if c.Authorization != "Bearer "+ghToken {
			t.Errorf("broker call %s carried %q, want the session's github token", c.RequestURI, c.Authorization)
		}
		if c.Host != "127.0.0.1:"+strconv.Itoa(port) {
			t.Errorf("broker call Host = %q, want the broker URL's host", c.Host)
		}
		if c.RequestURI == "/_facade/proxy/https://api.github.com/user" {
			user++
		}
		if strings.Contains(c.RequestURI, "example.com") || strings.Contains(c.RequestURI, "anthropic") ||
			strings.Contains(c.RequestURI, ".git/") {
			t.Errorf("a refused, tunnelled or git request reached the broker: %s", c.RequestURI)
		}
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
