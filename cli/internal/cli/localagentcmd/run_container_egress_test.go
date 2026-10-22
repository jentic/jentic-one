package localagentcmd

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/localagent/container"
)

// fakeControl serves the CLI facade list and the invocation-token lifecycle.
type fakeControl struct {
	mu      sync.Mutex
	minted  []map[string]any
	revoked []string
	renewed []string
	renewSt int
	// facades overrides the GET /cli-facades body (a server without CLI
	// profile wiring when empty).
	facades string
}

func (f *fakeControl) handler(t *testing.T) http.Handler {
	t.Helper()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/cli-facades" && f.facades != "":
			_, _ = w.Write([]byte(f.facades))
		case r.Method == http.MethodGet && r.URL.Path == "/cli-facades":
			_, _ = w.Write([]byte(`{"data":[{"vendor":"github","cli":"gh","mode":"host","protocols":["rest","graphql","git"]},` +
				`{"vendor":"cloudflare","cli":"cf","mode":"path","protocols":["rest"]},` +
				`{"vendor":"gitlab","cli":"glab","mode":"path","protocols":["rest"]}]}`))
		case r.Method == http.MethodPost && r.URL.Path == "/cli-invocations":
			var body map[string]any
			_ = json.NewDecoder(r.Body).Decode(&body)
			f.minted = append(f.minted, body)
			now := time.Now().UTC()
			w.WriteHeader(http.StatusCreated)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id": "cli_" + body["vendor"].(string), "token": "cit_" + strings.Repeat("a", 43), "vendor": body["vendor"],
				"trace_id": strings.Repeat("0", 32), "expires_at": now.Add(10 * time.Minute), "hard_expires_at": now.Add(time.Hour),
			})
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, ":renew"):
			f.renewed = append(f.renewed, r.URL.Path)
			if f.renewSt != 0 {
				w.Header().Set("Content-Type", "application/problem+json")
				w.WriteHeader(f.renewSt)
				_, _ = w.Write([]byte(`{"title":"Conflict","detail":"past its hard cap","status":409}`))
				return
			}
			now := time.Now().UTC()
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id": "cli_x", "vendor": "github", "trace_id": strings.Repeat("0", 32),
				"expires_at": now.Add(10 * time.Minute), "hard_expires_at": now.Add(time.Hour),
			})
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, ":revoke"):
			f.revoked = append(f.revoked, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"title":"Not Found","status":404}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	})
}

func testIssuer(t *testing.T) (*controlIssuer, *fakeControl) {
	t.Helper()
	fc := &fakeControl{}
	srv := httptest.NewServer(fc.handler(t))
	t.Cleanup(srv.Close)
	client, err := control.NewClientWithResponses(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	refreshed := 0
	return &controlIssuer{client: client, refresh: func() error { refreshed++; return nil }}, fc
}

func TestGovernedVendorsAreTheServedFacadesTheProxyCanGovern(t *testing.T) {
	issuer, _ := testIssuer(t)
	g, err := governedVendors(context.Background(), issuer.client)
	if err != nil {
		t.Fatal(err)
	}
	got, protocols := g.vendors, g.protocols
	if !slices.Equal(got, []string{"cloudflare", "github"}) {
		t.Errorf("governed vendors = %v", got)
	}
	if summary := governedSummary(got, protocols); summary != "cloudflare (REST), github (REST, GraphQL, git)" {
		t.Errorf("summary = %q", summary)
	}
}

func TestGovernedSummaryNamesAVendorWithoutProtocolsAlone(t *testing.T) {
	got := governedSummary([]string{"aws", "github"}, map[string][]string{"github": {"rest", "graphql", "future"}})
	if got != "aws, github (REST, GraphQL)" {
		t.Errorf("summary = %q", got)
	}
}

func TestControlIssuerLifecycle(t *testing.T) {
	issuer, fc := testIssuer(t)
	ctx := context.Background()
	tok, err := issuer.Mint(ctx, "github")
	if err != nil {
		t.Fatal(err)
	}
	if tok.ID != "cli_github" || !strings.HasPrefix(tok.Token, "cit_") || tok.HardExpiresAt.IsZero() {
		t.Errorf("minted %+v", tok)
	}
	if body := fc.minted[0]; body["vendor"] != "github" || body["command_title"] != sessionCommandTitle {
		t.Errorf("mint body = %v, want vendor github and command_title %q", body, sessionCommandTitle)
	}
	if _, err := issuer.Renew(ctx, tok.ID); err != nil {
		t.Errorf("renew: %v", err)
	}
	fc.renewSt = http.StatusConflict
	if _, err := issuer.Renew(ctx, tok.ID); !errors.Is(err, container.ErrTokenGone) {
		t.Errorf("a 409 renewal must read as ErrTokenGone, got %v", err)
	}
	if err := issuer.Revoke(ctx, tok.ID); err != nil {
		t.Errorf("revoking a token that is already gone is not an error: %v", err)
	}
	if len(fc.revoked) != 1 || fc.revoked[0] != "/cli-invocations/cli_github:revoke" {
		t.Errorf("revoked = %v", fc.revoked)
	}
}

func TestContainerTunnelHosts(t *testing.T) {
	desc, _ := localagent.Lookup("claude")
	env := map[string]string{}
	getenv := func(k string) string { return env[k] }
	base := containerTunnelHosts(desc, getenv)
	if !slices.Equal(base, []string{"api.anthropic.com", "platform.claude.com", "statsig.anthropic.com", "console.anthropic.com"}) {
		t.Errorf("claude's tunnel hosts = %v", base)
	}
	env["CLAUDE_CODE_USE_BEDROCK"] = "1"
	if got := containerTunnelHosts(desc, getenv); !slices.Contains(got, "bedrock-runtime.*.amazonaws.com") ||
		!slices.Contains(got, "portal.sso.*.amazonaws.com") || slices.Contains(got, "sts.amazonaws.com") {
		t.Errorf("bedrock tunnel hosts = %v", got)
	}
	env = map[string]string{"CLAUDE_CODE_USE_VERTEX": "true", "CLOUD_ML_REGION": "us-east5"}
	if got := containerTunnelHosts(desc, getenv); !slices.Contains(got, "us-east5-aiplatform.googleapis.com") ||
		!slices.Contains(got, "oauth2.googleapis.com") {
		t.Errorf("vertex tunnel hosts = %v", got)
	}
}

func TestGovernedVendorsCarryTheServedContainerWiring(t *testing.T) {
	issuer, fc := testIssuer(t)
	fc.facades = `{"data":[{"vendor":"github","cli":"gh","mode":"host","protocols":["rest","git"],
		"governed_domains":["github.com"],
		"container":{"env_set":{"GH_TOKEN":"{placeholder}","GIT_TERMINAL_PROMPT":"0"},
		"env_strip":["GITHUB_TOKEN"],"tunnel_hosts":["objects.githubusercontent.com"]}}]}`
	g, err := governedVendors(context.Background(), issuer.client)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(g.vendors, []string{"github"}) || !slices.Equal(g.tunnel, []string{"objects.githubusercontent.com"}) {
		t.Errorf("governed = %+v", g)
	}
	env := g.env["github"]
	if env.Set["GH_TOKEN"] != "{placeholder}" || !slices.Equal(env.Strip, []string{"GITHUB_TOKEN"}) {
		t.Errorf("github wiring = %+v", env)
	}
}

func TestGovernedVendorsRefuseADomainTheProxyDoesNotGovern(t *testing.T) {
	issuer, fc := testIssuer(t)
	fc.facades = `{"data":[{"vendor":"github","cli":"gh","mode":"host","protocols":["rest"],
		"governed_domains":["ghe.example.com"]}]}`
	if _, err := governedVendors(context.Background(), issuer.client); err == nil ||
		!strings.Contains(err.Error(), "matching Jentic and jentic CLI releases") {
		t.Errorf("err = %v, want a release-mismatch refusal", err)
	}
}
