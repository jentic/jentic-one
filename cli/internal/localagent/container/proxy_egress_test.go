package container

import (
	"context"
	"encoding/json"
	"errors"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

const testNonce = "0123456789abcdef0123456789abcdef"

func testProxyEgress() *ProxyEgress {
	return &ProxyEgress{
		Image:      DefaultProxyImage,
		Vendors:    []string{"github", "cloudflare"},
		Tunnel:     []string{"api.anthropic.com", "bedrock-runtime.*.amazonaws.com"},
		BrokerURL:  "http://127.0.0.1:8100",
		BrokerPort: 8100,
		Nonce:      testNonce,
		Tokens: func() map[string]string {
			return map[string]string{"github": "cit_session-github-0123456789abcdefghijklmnop"}
		},
	}
}

func proxyPlan(t *testing.T) *Plan {
	t.Helper()
	o := testOptions()
	o.Egress = testProxyEgress()
	return mustPlan(t, o)
}

func TestProxyEgressAgentSide(t *testing.T) {
	p := proxyPlan(t)
	proxy := "http://jentic-run-" + testSession + "-proxy:3128"
	for _, want := range []string{
		"HTTPS_PROXY=" + proxy, "https_proxy=" + proxy, "HTTP_PROXY=" + proxy, "http_proxy=" + proxy,
		"NO_PROXY=127.0.0.1,localhost", "no_proxy=127.0.0.1,localhost", "NODE_USE_ENV_PROXY=1",
		"SSL_CERT_FILE=/run/jentic-ca/bundle.pem", "REQUESTS_CA_BUNDLE=/run/jentic-ca/bundle.pem",
		"NODE_EXTRA_CA_CERTS=/run/jentic-ca/bundle.pem", "GIT_SSL_CAINFO=/run/jentic-ca/bundle.pem",
		"AWS_CA_BUNDLE=/run/jentic-ca/bundle.pem", "CURL_CA_BUNDLE=/run/jentic-ca/bundle.pem",
		"GH_TOKEN=" + ghPlaceholderToken, "GIT_TERMINAL_PROMPT=0", "GIT_HTTP_PROXY_AUTHMETHOD=basic",
	} {
		if !slices.Contains(p.Agent.Env, want) {
			t.Errorf("agent env lacks %s", want)
		}
	}
	for _, e := range p.Agent.Env {
		if strings.Contains(e, "cit_") {
			t.Errorf("an invocation token reached the agent's environment: %s", e)
		}
	}
	ca := Mount{Type: "volume", Source: "jentic-run-" + testSession + "-ca", Target: ProxyCADir, ReadOnly: true}
	if !slices.Contains(p.Agent.Mounts, ca) {
		t.Errorf("agent must mount the CA volume read-only: %v", p.Agent.Mounts)
	}
	for _, m := range p.Agent.Mounts {
		if m.Target == proxyTokenDir || m.Source == p.SocketVolume {
			t.Errorf("the agent must not mount %v", m)
		}
	}
	for _, tmpfs := range p.Agent.Tmpfs {
		if strings.HasPrefix(tmpfs, proxyTokenDir) {
			t.Error("the agent must not have the proxy's token tmpfs")
		}
	}
	if p.Agent.Network != "container:"+p.Loopback.Name || len(p.Agent.ExtraNetworks) != 0 {
		t.Errorf("the agent joins only the loopback's namespace: %q %v", p.Agent.Network, p.Agent.ExtraNetworks)
	}
}

func TestProxyEgressSidecar(t *testing.T) {
	p := proxyPlan(t)
	sidecars := p.Egress.Sidecars(p)
	if len(sidecars) != 1 {
		t.Fatalf("sidecars = %v", sidecars)
	}
	s := sidecars[0]
	if s.User != "65534:65534" || !s.ReadOnlyRoot || !s.Detach {
		t.Errorf("proxy must run unprivileged, read-only, detached: %+v", s)
	}
	if !slices.Equal(s.ExtraNetworks, []string{"jentic-run-" + testSession + "-egress"}) {
		t.Errorf("proxy extra networks = %v", s.ExtraNetworks)
	}
	if len(s.Tmpfs) != 1 || !strings.HasPrefix(s.Tmpfs[0], proxyTokenDir+":") || !strings.Contains(s.Tmpfs[0], "mode=0700") {
		t.Errorf("proxy token tmpfs = %v", s.Tmpfs)
	}
	args, err := s.CreateArgs()
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range [][2]string{
		{"--broker-url", "http://127.0.0.1:8100"},
		{"--broker-socket", socketDir + "/8100.sock"},
		{"--nonce", testNonce},
		{"--govern", "cloudflare,github"},
		{"--tunnel", "api.anthropic.com,bedrock-runtime.*.amazonaws.com"},
		{"--token-file", proxyTokenFile},
	} {
		if v := argValues(args, want[0]); len(v) != 1 || v[0] != want[1] {
			t.Errorf("%s = %v, want %s", want[0], v, want[1])
		}
	}
	if !slices.Contains(args, "--cap-drop") || !slices.Contains(args, "no-new-privileges") {
		t.Errorf("proxy must carry the shared hardening: %v", args)
	}
	for _, a := range args {
		if strings.Contains(a, "cit_") {
			t.Errorf("an invocation token is on the proxy's command line: %s", a)
		}
	}
}

func TestProxyEgressValidation(t *testing.T) {
	for name, mutate := range map[string]func(e *ProxyEgress){
		"broker port not forwarded": func(e *ProxyEgress) { e.BrokerPort = 9999 },
		"short nonce":               func(e *ProxyEgress) { e.Nonce = "abc" },
		"no image":                  func(e *ProxyEgress) { e.Image = "" },
		"comma in tunnel":           func(e *ProxyEgress) { e.Tunnel = []string{"a.com,b.com"} },
	} {
		e := testProxyEgress()
		mutate(e)
		o := testOptions()
		o.Egress = e
		if _, err := NewPlan(o); err == nil {
			t.Errorf("%s: plan accepted", name)
		}
	}
	bad := Spec{Name: "x", Image: "i", User: "1:1", ExtraNetworks: []string{"bridge"}}
	if err := bad.Validate(); err == nil {
		t.Error("a sidecar must never join the default bridge network")
	}
}

func TestSessionUpWithTheProxyEgress(t *testing.T) {
	p := proxyPlan(t)
	d := newFake()
	if err := (&Session{Plan: p, Docker: d}).Up(context.Background()); err != nil {
		t.Fatal(err)
	}
	base := "jentic-run-" + testSession
	var order []string
	for _, c := range d.calls {
		switch {
		case c[0] == "network" && c[1] == "create":
			kind := "network"
			if slices.Contains(c, "--internal") {
				kind = "internal-network"
			}
			order = append(order, kind+":"+c[len(c)-1])
		case c[0] == "network" && c[1] == "connect":
			order = append(order, "connect:"+c[2]+">"+c[3])
		case c[0] == "volume":
			order = append(order, "volume:"+c[len(c)-1])
		case c[0] == "run" || c[0] == "create":
			order = append(order, c[0]+":"+argValues(c, "--name")[0])
		case c[0] == "start":
			order = append(order, "start:"+c[1])
		case c[0] == "exec" && c[1] == "-i":
			order = append(order, "exec:"+c[4])
		case c[0] == "exec":
			order = append(order, "exec:"+c[3])
		}
	}
	want := []string{
		"internal-network:" + base, "network:" + base + "-egress",
		"volume:" + base + "-home", "volume:" + base + "-sockets", "volume:" + base + "-ca",
		"run:" + base + "-relay", "exec:ready-relay", "run:" + base + "-loopback", "exec:ready-loopback",
		"create:" + base + "-proxy", "connect:" + base + "-egress>" + base + "-proxy", "start:" + base + "-proxy",
		"exec:set-tokens", "exec:ready", "create:" + base + "-agent",
	}
	if !slices.Equal(order, want) {
		t.Errorf("Up order =\n%v\nwant\n%v", order, want)
	}
	var pushed map[string]map[string]string
	for args, data := range d.inputs {
		if strings.Contains(args, "set-tokens") {
			_ = json.Unmarshal(data, &pushed)
		}
	}
	if pushed["tokens"]["github"] == "" {
		t.Errorf("the session tokens must be handed to the proxy over stdin: %v", d.inputs)
	}
	for _, c := range d.calls {
		if c[0] == "network" && c[len(c)-1] == base+"-egress" && !slices.Contains(c, "--label") {
			t.Error("the egress network must carry the session labels")
		}
	}
}

func TestSessionUpFailsClosedWhenTheProxyIsNotReady(t *testing.T) {
	o := testOptions()
	e := testProxyEgress()
	e.ReadyTimeout = time.Millisecond
	o.Egress = e
	p := mustPlan(t, o)
	d := newFake()
	d.fail["exec jentic-run-"+testSession+"-proxy "+proxyBinary+" ready"] = errors.New("nonce mismatch")
	err := (&Session{Plan: p, Docker: d}).Up(context.Background())
	if err == nil || !strings.Contains(err.Error(), "nonce mismatch") {
		t.Fatalf("Up = %v, want the proxy's readiness failure", err)
	}
	if d.called("create --rm --name jentic-run-" + testSession + "-agent") {
		t.Error("the agent must not be created while the proxy is not ready")
	}
}

// fakeIssuer is a control plane for invocation tokens.
type fakeIssuer struct {
	mu        sync.Mutex
	ttl, hard time.Duration
	n         int
	live      map[string]bool
	minted    []string
	revoked   []string
	renewErr  error
	mintErrOn string
}

func newIssuer(ttl, hard time.Duration) *fakeIssuer {
	return &fakeIssuer{ttl: ttl, hard: hard, live: map[string]bool{}}
}

func (f *fakeIssuer) Mint(_ context.Context, vendor string) (InvocationToken, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if vendor == f.mintErrOn {
		return InvocationToken{}, errors.New("mint refused")
	}
	f.n++
	id := "cli_" + vendor + "_" + strings.Repeat("x", f.n)
	f.live[id] = true
	f.minted = append(f.minted, id)
	now := time.Now()
	return InvocationToken{ID: id, Token: tokenFor(id), ExpiresAt: now.Add(f.ttl), HardExpiresAt: now.Add(f.hard)}, nil
}

// tokenFor is the fake token of invocation id: a well-formed `cit_` token.
func tokenFor(id string) string { return "cit_" + id + "_" + strings.Repeat("0", 40) }

func (f *fakeIssuer) Renew(_ context.Context, id string) (time.Time, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.renewErr != nil {
		return time.Time{}, f.renewErr
	}
	if !f.live[id] {
		return time.Time{}, ErrTokenGone
	}
	return time.Now().Add(f.ttl), nil
}

func (f *fakeIssuer) Revoke(_ context.Context, id string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.live, id)
	f.revoked = append(f.revoked, id)
	return nil
}

func (f *fakeIssuer) snapshot() (minted, revoked []string, live int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.minted...), append([]string(nil), f.revoked...), len(f.live)
}

func TestMintSessionTokensFailsClosed(t *testing.T) {
	iss := newIssuer(time.Minute, time.Hour)
	iss.mintErrOn = "cloudflare"
	if _, err := MintSessionTokens(context.Background(), iss, []string{"github", "cloudflare"}); err == nil {
		t.Fatal("a failed mint must fail the session")
	}
	if _, revoked, live := iss.snapshot(); live != 0 || len(revoked) != 1 {
		t.Errorf("the tokens minted before the failure must be revoked: revoked %v, live %d", revoked, live)
	}
}

func TestTokenKeeperRenewsRotatesAndRevokes(t *testing.T) {
	iss := newIssuer(3*time.Second, 7*time.Second)
	k, err := MintSessionTokens(context.Background(), iss, []string{"github"})
	if err != nil {
		t.Fatal(err)
	}
	first := k.Tokens()["github"]
	var mu sync.Mutex
	var pushes []map[string]string
	k.Run(context.Background(), func(_ context.Context, tokens map[string]string) error {
		mu.Lock()
		pushes = append(pushes, tokens)
		mu.Unlock()
		return nil
	})
	// Heartbeat every second: renewals until the hard cap is two beats away,
	// then a replacement is pushed before the old token is revoked.
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if k.Tokens()["github"] != first {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if k.Tokens()["github"] == first {
		t.Fatal("the token was never replaced before its hard cap")
	}
	time.Sleep(200 * time.Millisecond)
	mu.Lock()
	if len(pushes) == 0 || pushes[0]["github"] == first {
		t.Errorf("the replacement must be pushed to the proxy: %v", pushes)
	}
	mu.Unlock()
	minted, revoked, _ := iss.snapshot()
	if !slices.ContainsFunc(minted, func(id string) bool { return tokenFor(id) == first && slices.Contains(revoked, id) }) {
		t.Errorf("the replaced token must be revoked: %v", revoked)
	}
	if err := k.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, _, live := iss.snapshot(); live != 0 {
		t.Errorf("Close must revoke every token, %d live", live)
	}
	if err := k.Close(context.Background()); err != nil {
		t.Error("Close is idempotent")
	}
}

func TestTokenKeeperReplacesATokenTheServerWillNotRenew(t *testing.T) {
	iss := newIssuer(3*time.Second, time.Hour)
	k, err := MintSessionTokens(context.Background(), iss, []string{"cloudflare"})
	if err != nil {
		t.Fatal(err)
	}
	defer k.Close(context.Background())
	first := k.Tokens()["cloudflare"]
	iss.mu.Lock()
	iss.renewErr = ErrTokenGone
	iss.mu.Unlock()
	k.Run(context.Background(), func(context.Context, map[string]string) error { return nil })
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) && k.Tokens()["cloudflare"] == first {
		time.Sleep(100 * time.Millisecond)
	}
	if k.Tokens()["cloudflare"] == first {
		t.Error("a token the server will not renew must be replaced")
	}
}
