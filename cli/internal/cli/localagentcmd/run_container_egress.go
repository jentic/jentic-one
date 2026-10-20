package localagentcmd

// run_container_egress.go wires the "proxy" egress of `jentic run --container`:
// which vendors the session governs (the CLI facades the deployment serves),
// which hosts the agent tunnels (its descriptor's allowlist plus the
// operator's provider), and the session's invocation tokens, minted, renewed
// and revoked here with the agent's own credentials.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/localagent/container"
)

// sessionCommandTitle is the command_title of a container session's tokens:
// every governed call the session makes without per-command proxy
// credentials is recorded under it.
const sessionCommandTitle = "session"

// governedVendors are the vendors whose CLI facade the deployment serves and
// the proxy can govern, sorted, and what each one's tools reach through
// Jentic (GET /cli-facades `protocols`: rest, graphql, git).
func governedVendors(ctx context.Context, client *control.ClientWithResponses) ([]string, map[string][]string, error) {
	callCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	resp, err := client.ListCliFacadesWithResponse(callCtx)
	if err != nil {
		return nil, nil, fmt.Errorf("list the CLI facades: %w", err)
	}
	if resp.JSON200 == nil {
		return nil, nil, fmt.Errorf("list the CLI facades: %s", problemSummary(resp.StatusCode(), resp.Body))
	}
	var vendors []string
	protocols := map[string][]string{}
	for _, f := range resp.JSON200.Data {
		if slices.Contains(container.GovernableVendors(), f.Vendor) && !slices.Contains(vendors, f.Vendor) {
			vendors = append(vendors, f.Vendor)
			for _, p := range f.Protocols {
				protocols[f.Vendor] = append(protocols[f.Vendor], string(p))
			}
		}
	}
	sort.Strings(vendors)
	return vendors, protocols, nil
}

// protocolLabels are how the egress summary names what a vendor's tools
// reach through Jentic.
var protocolLabels = map[string]string{"rest": "REST", "graphql": "GraphQL", "git": "git"}

// governedSummary names each governed vendor with what its tools reach
// through Jentic: "github (REST, GraphQL, git), cloudflare (REST)". A vendor
// the deployment lists no protocols for is named alone.
func governedSummary(vendors []string, protocols map[string][]string) string {
	parts := make([]string, 0, len(vendors))
	for _, v := range vendors {
		labels := make([]string, 0, len(protocols[v]))
		for _, p := range protocols[v] {
			if label, ok := protocolLabels[p]; ok {
				labels = append(labels, label)
			}
		}
		if len(labels) == 0 {
			parts = append(parts, v)
			continue
		}
		parts = append(parts, v+" ("+strings.Join(labels, ", ")+")")
	}
	return strings.Join(parts, ", ")
}

// containerTunnelHosts is the agent's tunnel allowlist for a session: its
// descriptor's hosts plus those of every provider the operator's environment
// selects.
func containerTunnelHosts(desc localagent.Descriptor, getenv func(string) string) []string {
	hosts := append([]string(nil), desc.ContainerTunnel...)
	for _, p := range desc.ContainerProviderTunnels {
		if !envTruthy(getenv(p.Env)) {
			continue
		}
		hosts = append(hosts, p.Hosts...)
		if p.RegionEnv != "" && p.RegionHost != "" {
			if region := strings.ToLower(strings.TrimSpace(getenv(p.RegionEnv))); region != "" && region != "global" {
				hosts = append(hosts, strings.ReplaceAll(p.RegionHost, "{region}", region))
			}
		}
	}
	out := hosts[:0]
	for _, h := range hosts {
		if !slices.Contains(out, h) {
			out = append(out, h)
		}
	}
	return out
}

func envTruthy(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}

// controlIssuer mints, renews and revokes the session's invocation tokens on
// the control plane with the agent's own access token.
type controlIssuer struct {
	client *control.ClientWithResponses
	// refresh re-exchanges the agent's access token before a mint, so a token
	// is never cut short by an access token about to expire.
	refresh func() error
}

func newControlIssuer(ctx context.Context, st *clictx.ActiveState) (*controlIssuer, error) {
	client, err := clictx.GetControlClient(ctx)
	if err != nil {
		return nil, err
	}
	issuer := &controlIssuer{client: client, refresh: func() error { return nil }}
	if st != nil && st.ResolvedState != nil {
		hc, err := clictx.AuthHTTPClient(ctx, st.CACertPath)
		if err != nil {
			return nil, err
		}
		creds := auth.Credentials{
			BaseURL: st.BaseURL, IdentityName: st.IdentityName, EnvironmentName: st.EnvironmentName,
			InjectedBearerToken: st.InjectedBearerToken, HTTPClient: hc,
		}
		if auth.CanReExchange(creds) {
			issuer.refresh = func() error {
				_, err := auth.RefreshBearerToken(creds)
				return err
			}
		}
	}
	return issuer, nil
}

// Mint implements container.TokenIssuer.
func (c *controlIssuer) Mint(ctx context.Context, vendor string) (container.InvocationToken, error) {
	if err := c.refresh(); err != nil {
		return container.InvocationToken{}, fmt.Errorf("refresh the agent's access token: %w", err)
	}
	title := sessionCommandTitle
	resp, err := c.client.CreateCliInvocationWithResponse(ctx, control.CreateCliInvocationJSONRequestBody{
		Vendor: vendor, CommandTitle: &title,
	})
	if err != nil {
		return container.InvocationToken{}, err
	}
	if resp.JSON201 == nil {
		reason := problemSummary(resp.StatusCode(), resp.Body)
		switch resp.StatusCode() {
		case http.StatusForbidden:
			reason += " (only an agent holding capabilities:execute, signed in with its own access token rather than an API key, can mint invocation tokens)"
		case http.StatusNotFound:
			reason += " (the vendor's CLI facade is switched off)"
		}
		return container.InvocationToken{}, errors.New(reason)
	}
	t := resp.JSON201
	return container.InvocationToken{ID: t.Id, Token: t.Token, ExpiresAt: t.ExpiresAt, HardExpiresAt: t.HardExpiresAt}, nil
}

// Renew implements container.TokenIssuer.
func (c *controlIssuer) Renew(ctx context.Context, id string) (time.Time, error) {
	resp, err := c.client.RenewCliInvocationWithResponse(ctx, id)
	if err != nil {
		return time.Time{}, err
	}
	switch {
	case resp.JSON200 != nil:
		return resp.JSON200.ExpiresAt, nil
	case resp.StatusCode() == http.StatusConflict || resp.StatusCode() == http.StatusNotFound:
		return time.Time{}, container.ErrTokenGone
	default:
		return time.Time{}, errors.New(problemSummary(resp.StatusCode(), resp.Body))
	}
}

// Revoke implements container.TokenIssuer.
func (c *controlIssuer) Revoke(ctx context.Context, id string) error {
	resp, err := c.client.RevokeCliInvocationWithResponse(ctx, id)
	if err != nil {
		return err
	}
	if code := resp.StatusCode(); code < 200 || code > 299 {
		if code == http.StatusNotFound || code == http.StatusConflict {
			return nil // already gone
		}
		return errors.New(problemSummary(code, resp.Body))
	}
	return nil
}

// problemSummary is "HTTP <status>: <detail>" for a control-plane error
// response, the detail read from its Problem Details body when there is one.
func problemSummary(status int, body []byte) string {
	var p struct {
		Detail string `json:"detail"`
		Title  string `json:"title"`
	}
	msg := "HTTP " + strconv.Itoa(status)
	if json.Unmarshal(body, &p) == nil {
		if p.Detail != "" {
			return msg + ": " + p.Detail
		}
		if p.Title != "" {
			return msg + ": " + p.Title
		}
	}
	return msg
}

// proxyEgressSetup is the proxy egress for a session plus the token keeper
// behind it; Close revokes the tokens.
type proxyEgressSetup struct {
	egress *container.ProxyEgress
	keeper *container.TokenKeeper
	// protocols is what each governed vendor's tools reach through Jentic.
	protocols map[string][]string
}

// Close revokes the session's tokens.
func (s *proxyEgressSetup) Close(ctx context.Context) error {
	if s.keeper == nil {
		return nil
	}
	return s.keeper.Close(ctx)
}

// setUpProxyEgress decides what the session governs and tunnels, and mints
// its tokens. getenv answers the provider selection the tunnel allowlist
// reads (containerTunnelHosts). It fails closed: any step that cannot
// complete refuses the session, with every token minted so far revoked.
func setUpProxyEgress(ctx context.Context, st *clictx.ActiveState, desc localagent.Descriptor, image string, getenv func(string) string) (*proxyEgressSetup, error) {
	brokerURL := st.BrokerURL
	if brokerURL == "" {
		brokerURL = "http://127.0.0.1:" + strconv.Itoa(defaultBrokerPort)
	}
	if !strings.HasPrefix(brokerURL, "http://") {
		return nil, containerUnavailableErr(
			"the egress proxy reaches the broker over plain HTTP on the host's loopback; the context's broker URL is "+brokerURL,
			"Use a context on a local Jentic served over http://127.0.0.1, or run with --container-egress none.")
	}
	brokerPort, err := loopbackPort(brokerURL)
	if err != nil {
		return nil, containerUnavailableErr(fmt.Sprintf("the context's broker URL %q: %v", brokerURL, err), "")
	}
	issuer, err := newControlIssuer(ctx, st)
	if err != nil {
		return nil, err
	}
	vendors, protocols, err := governedVendors(ctx, issuer.client)
	if err != nil {
		return nil, containerUnavailableErr(
			fmt.Sprintf("could not learn which vendor CLIs this Jentic governs: %v", err),
			"Check that Jentic is reachable (jentic doctor), or run with --container-egress none.")
	}
	setup := &proxyEgressSetup{protocols: protocols}
	tokens := func() map[string]string { return map[string]string{} }
	if len(vendors) > 0 {
		keeper, err := container.MintSessionTokens(ctx, issuer, vendors)
		if err != nil {
			return nil, containerUnavailableErr(fmt.Sprintf("could not mint the session's invocation tokens: %v", err),
				"Run as an agent identity that holds capabilities:execute, or run with --container-egress none.")
		}
		setup.keeper = keeper
		tokens = keeper.Tokens
	}
	nonce, err := container.NewNonce()
	if err != nil {
		_ = setup.Close(ctx)
		return nil, err
	}
	setup.egress = &container.ProxyEgress{
		Image:      image,
		Vendors:    vendors,
		Tunnel:     containerTunnelHosts(desc, getenv),
		BrokerURL:  strings.TrimRight(brokerURL, "/"),
		BrokerPort: brokerPort,
		Nonce:      nonce,
		Tokens:     tokens,
	}
	return setup, nil
}
