package container

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Egress is a session's route to anything beyond Jentic. The agent shares the
// loopback container's network namespace, whose only interface besides lo is
// the session's internal network, so whatever an Egress adds to that network
// is all the agent can reach besides Jentic's loopback ports.
//
// A session asks its Egress for the networks and volumes it needs, for
// sidecars to start on the internal network before the agent, for the agent's
// extra environment and mounts, and waits for Ready before it creates the
// agent. An implementation must fail closed: if it cannot set up its route,
// Ready returns an error and the session does not start.
type Egress interface {
	// Name is the egress mode the operator chose, e.g. "proxy".
	Name() string
	// Networks are extra (non-internal) networks the session creates for this
	// egress, labelled with the session; a sidecar joins them through
	// Spec.ExtraNetworks. Only sidecars ever join them, never the agent.
	Networks(p *Plan) []string
	// Volumes are extra volumes the session creates for this egress,
	// labelled with the session.
	Volumes(p *Plan) []string
	// Sidecars are the containers this egress runs for the session; each is
	// attached to the session's internal network (Plan.Network) and labelled
	// with the session.
	Sidecars(p *Plan) []Spec
	// AgentEnv is extra KEY=VALUE environment for the agent container.
	AgentEnv(p *Plan) []string
	// AgentMounts are extra mounts for the agent container; they must be
	// read-only.
	AgentMounts(p *Plan) []Mount
	// Ready blocks until the sidecars accept traffic, or fails.
	Ready(ctx context.Context, d Docker, p *Plan) error
}

// Egress modes.
const (
	// EgressNone is the egress mode with no route beyond Jentic.
	EgressNone = "none"
	// EgressProxy routes the agent through the session's egress proxy
	// sidecar (ProxyEgress). It is the default.
	EgressProxy = "proxy"
)

// NoEgress gives the agent no route beyond Jentic: no sidecar, no extra
// environment. The agent reaches Jentic at its loopback URLs and nothing
// else, so calls to an LLM provider or a vendor API fail inside the
// container.
type NoEgress struct{}

// Name implements Egress.
func (NoEgress) Name() string { return EgressNone }

// Networks implements Egress.
func (NoEgress) Networks(*Plan) []string { return nil }

// Volumes implements Egress.
func (NoEgress) Volumes(*Plan) []string { return nil }

// Sidecars implements Egress.
func (NoEgress) Sidecars(*Plan) []Spec { return nil }

// AgentEnv implements Egress.
func (NoEgress) AgentEnv(*Plan) []string { return nil }

// AgentMounts implements Egress.
func (NoEgress) AgentMounts(*Plan) []Mount { return nil }

// Ready implements Egress.
func (NoEgress) Ready(context.Context, Docker, *Plan) error { return nil }

// EgressModes lists the egress modes ParseEgressMode accepts, default first.
func EgressModes() []string { return []string{EgressProxy, EgressNone} }

// ParseEgressMode validates an operator-chosen egress mode; "" is the
// default, EgressProxy.
func ParseEgressMode(mode string) (string, error) {
	switch mode {
	case "", EgressProxy:
		return EgressProxy, nil
	case EgressNone:
		return EgressNone, nil
	default:
		return "", fmt.Errorf("unknown container egress mode %q (supported: %s)", mode, strings.Join(EgressModes(), ", "))
	}
}

// Paths and identities of the egress proxy sidecar.
const (
	// proxyPort is the proxy's listening port on the session network.
	proxyPort = 3128
	// proxyUser runs the proxy; it matches bridgeUser so the proxy can dial
	// the relay's sockets (mode 0600, owned by that uid).
	proxyUser = bridgeUser
	// proxyBinary is the proxy's executable in the proxy image.
	proxyBinary = "/usr/local/bin/jentic-egress-proxy"
	// ProxyCADir is where the session's CA volume is mounted: read-write in
	// the proxy, read-only in the agent.
	ProxyCADir = "/run/jentic-ca"
	// proxyTokenDir is the proxy's private tmpfs for the session tokens; the
	// agent container has no such mount.
	proxyTokenDir  = "/run/jentic-proxy" //nolint:gosec // G101: a directory path, not a credential
	proxyTokenFile = proxyTokenDir + "/tokens.json"
	// ProxyCABundle is the bundle (system roots + session CA) the agent's
	// TLS clients trust.
	ProxyCABundle = ProxyCADir + "/bundle.pem"
	// ghPlaceholderToken lets `gh` make API calls at all (it refuses to run
	// unauthenticated). It is not a credential: the proxy drops the client's
	// Authorization header, and the broker injects the real one.
	ghPlaceholderToken = "jentic-egress-proxy-placeholder" //nolint:gosec // G101: a fixed non-secret placeholder
)

// caBundleEnv are the variables that point the agent's TLS clients (OpenSSL,
// Python requests, Node, git, the AWS SDKs, curl) at the session bundle. They
// are set on the agent container only; nothing is installed into any trust
// store.
var caBundleEnv = []string{
	"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "GIT_SSL_CAINFO", "AWS_CA_BUNDLE", "CURL_CA_BUNDLE",
}

// ProxyEgress is the "proxy" egress: a sidecar running jentic-egress-proxy on
// the session's internal network and on a per-session egress network of its
// own (the agent joins only the internal one). It tunnels the agent's
// allowlisted hosts, TLS-terminates governed vendor hosts with a per-session,
// name-constrained CA and relays their calls to the broker with the session's
// invocation tokens, and refuses everything else.
type ProxyEgress struct {
	// Image is the proxy image.
	Image string
	// Vendors are the governed vendors (github, cloudflare, aws).
	Vendors []string
	// Tunnel are the agent's tunnel host patterns.
	Tunnel []string
	// BrokerURL is the broker's base URL as the context names it.
	BrokerURL string
	// BrokerPort is the broker URL's port; it must be one of the session's
	// Jentic ports, whose relay socket the proxy dials.
	BrokerPort int
	// Nonce is the session's readiness nonce (NewNonce).
	Nonce string
	// Tokens yields the session tokens to hand the proxy at Ready.
	Tokens func() map[string]string
	// ReadyTimeout bounds the wait for the proxy; zero means 15s.
	ReadyTimeout time.Duration
}

// GovernableVendors are the vendors the egress proxy can govern, sorted (the
// proxy binary keeps the domain each one covers).
func GovernableVendors() []string { return []string{"aws", "cloudflare", "github"} }

// NewNonce returns a random readiness nonce (32 hex characters).
func NewNonce() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("generate a readiness nonce: %w", err)
	}
	return hex.EncodeToString(b), nil
}

// Validate checks the proxy's configuration against the plan.
func (e *ProxyEgress) Validate(p *Plan) error {
	if e.Image == "" || e.BrokerURL == "" {
		return errors.New("the egress proxy needs an image and the broker URL")
	}
	if len(e.Nonce) != 32 || strings.Trim(e.Nonce, "0123456789abcdef") != "" {
		return errors.New("the egress proxy needs a 32-hex-character readiness nonce")
	}
	found := false
	for _, port := range p.JenticPorts {
		found = found || port == e.BrokerPort
	}
	if !found {
		return fmt.Errorf("the broker port %d is not one of the session's Jentic ports", e.BrokerPort)
	}
	for _, v := range append(append([]string{}, e.Vendors...), e.Tunnel...) {
		if v == "" || strings.ContainsAny(v, ", \t\n") {
			return fmt.Errorf("invalid egress proxy vendor or tunnel host %q", v)
		}
	}
	return nil
}

// Name implements Egress.
func (*ProxyEgress) Name() string { return EgressProxy }

// ProxyName is the proxy container's name, which the agent resolves on the
// session network.
func ProxyName(p *Plan) string { return "jentic-run-" + p.SessionID + "-proxy" }

func egressNetwork(p *Plan) string { return "jentic-run-" + p.SessionID + "-egress" }
func caVolume(p *Plan) string      { return "jentic-run-" + p.SessionID + "-ca" }

// Networks implements Egress: the proxy's own route out.
func (*ProxyEgress) Networks(p *Plan) []string { return []string{egressNetwork(p)} }

// Volumes implements Egress: the CA volume (public certificates only).
func (*ProxyEgress) Volumes(p *Plan) []string { return []string{caVolume(p)} }

// Sidecars implements Egress.
func (e *ProxyEgress) Sidecars(p *Plan) []Spec {
	vendors := append([]string(nil), e.Vendors...)
	sort.Strings(vendors)
	return []Spec{{
		Name:          ProxyName(p),
		Image:         e.Image,
		User:          proxyUser,
		ReadOnlyRoot:  true,
		Tmpfs:         []string{proxyTokenDir + ":rw,noexec,nosuid,nodev,size=1m,mode=0700,uid=65534,gid=65534"},
		ExtraNetworks: []string{egressNetwork(p)},
		Mounts: []Mount{
			{Type: "volume", Source: caVolume(p), Target: ProxyCADir},
			{Type: "volume", Source: p.SocketVolume, Target: socketDir},
		},
		Detach: true,
		Cmd: []string{
			"serve",
			"--listen", ":" + strconv.Itoa(proxyPort),
			"--broker-url", e.BrokerURL,
			"--broker-socket", socketDir + "/" + strconv.Itoa(e.BrokerPort) + ".sock",
			"--ca-dir", ProxyCADir,
			"--token-file", proxyTokenFile,
			"--nonce", e.Nonce,
			"--session", p.SessionID,
			"--govern", strings.Join(vendors, ","),
			"--tunnel", strings.Join(e.Tunnel, ","),
		},
	}}
}

// AgentEnv implements Egress: the proxy for every client, the session bundle
// for every TLS stack, and loopback (Jentic) kept direct.
func (e *ProxyEgress) AgentEnv(p *Plan) []string {
	proxy := "http://" + ProxyName(p) + ":" + strconv.Itoa(proxyPort)
	env := []string{
		"HTTPS_PROXY=" + proxy, "https_proxy=" + proxy,
		"HTTP_PROXY=" + proxy, "http_proxy=" + proxy,
		"NO_PROXY=127.0.0.1,localhost", "no_proxy=127.0.0.1,localhost",
		"NODE_USE_ENV_PROXY=1",
	}
	for _, k := range caBundleEnv {
		env = append(env, k+"="+ProxyCABundle)
	}
	for _, v := range e.Vendors {
		if v == "github" {
			env = append(env, "GH_TOKEN="+ghPlaceholderToken)
		}
	}
	return env
}

// AgentMounts implements Egress: the CA volume, read-only.
func (*ProxyEgress) AgentMounts(p *Plan) []Mount {
	return []Mount{{Type: "volume", Source: caVolume(p), Target: ProxyCADir, ReadOnly: true}}
}

// Ready implements Egress: it hands the proxy the session tokens, then waits
// until the proxy accepts connections and the CA volume holds this session's
// CA (the ready file names this session's nonce and matches the published
// certificates).
func (e *ProxyEgress) Ready(ctx context.Context, d Docker, p *Plan) error {
	if err := e.Validate(p); err != nil {
		return err
	}
	tokens := map[string]string{}
	if e.Tokens != nil {
		tokens = e.Tokens()
	}
	if err := e.PushTokens(ctx, d, p, tokens); err != nil {
		return err
	}
	timeout := e.ReadyTimeout
	if timeout == 0 {
		timeout = 15 * time.Second
	}
	deadline := time.Now().Add(timeout)
	args := []string{
		"exec", ProxyName(p), proxyBinary, "ready",
		"--ca-dir", ProxyCADir, "--nonce", e.Nonce, "--listen", "127.0.0.1:" + strconv.Itoa(proxyPort), "--token-file", proxyTokenFile,
	}
	var last error
	for {
		if _, last = d.Output(ctx, args...); last == nil {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("the egress proxy did not become ready within %s: %w", timeout, last)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(100 * time.Millisecond):
		}
	}
}

// PushTokens replaces the proxy's session tokens (vendor → `cit_` token). The
// file is written by the proxy binary itself, inside the proxy container, onto
// its private tmpfs.
func (e *ProxyEgress) PushTokens(ctx context.Context, d Docker, p *Plan, tokens map[string]string) error {
	data, err := json.Marshal(map[string]map[string]string{"tokens": tokens})
	if err != nil {
		return err
	}
	if err := d.Input(ctx, bytes.NewReader(data), "exec", "-i", ProxyName(p), proxyBinary, "set-tokens", "--token-file", proxyTokenFile); err != nil {
		return fmt.Errorf("hand the session tokens to the egress proxy: %w", err)
	}
	return nil
}
