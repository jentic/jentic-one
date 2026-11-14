package api

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
)

func seedEnvironmentRegistration(t *testing.T, identity, environment, clientID string) {
	t.Helper()
	if err := sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
		ident := c.Identities[identity]
		if ident.Type == "" {
			ident.Type = "agent"
		}
		if ident.Environments == nil {
			ident.Environments = make(map[string]sdkconfig.EnvRegState)
		}
		ident.Environments[environment] = sdkconfig.EnvRegState{ClientID: clientID, Status: "approved"}
		c.Identities[identity] = ident
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

func removeAPIKeyOriginSidecar(t *testing.T, ref auth.IdentityRef) {
	t.Helper()
	stem, err := ref.Stem()
	if err != nil {
		t.Fatal(err)
	}
	stateDir, err := sdkconfig.StateDir()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(stateDir, stem+".apikey.origin")); err != nil {
		t.Fatal(err)
	}
}

func TestEnvAddForce_OriginChangePurgesCredentialsWhileSameOriginPreservesThem(t *testing.T) {
	withXDG(t)

	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com/v1", "--broker-url", "https://broker-a.example.com"); err != nil {
		t.Fatalf("env add initial: %v", err)
	}

	agentRef := auth.IdentityRef{Identity: "agent1", Environment: "prod"}
	ciRef := auth.IdentityRef{Identity: "ci1", Environment: "prod"}

	if err := sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
		c.Identities["agent1"] = sdkconfig.Identity{
			Type: "agent",
			Environments: map[string]sdkconfig.EnvRegState{
				"prod":  {ClientID: "cid_prod_a", Status: "approved"},
				"other": {ClientID: "cid_other", Status: "approved"},
			},
		}
		return nil
	}); err != nil {
		t.Fatalf("seed identity registration: %v", err)
	}
	if err := auth.SaveTokens(agentRef, &auth.TokenSet{
		AccessToken: "tok_origin_a",
		ExpiresAt:   time.Now().Add(time.Hour),
	}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}
	if err := auth.SaveAPIKey(ciRef, "jak_origin_a_secret"); err != nil {
		t.Fatalf("SaveAPIKey: %v", err)
	}

	// 1. Equivalent base and broker origins preserve registration, cached token, and API key.
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com:443/v2", "--broker-url", "https://broker-a.example.com:443/v2", "--force"); err != nil {
		t.Fatalf("env add same-origin --force: %v", err)
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Identities["agent1"].Environments["prod"].ClientID != "cid_prod_a" {
		t.Fatalf("same-origin update should preserve registration, got %+v", cfg.Identities["agent1"].Environments)
	}
	if tok, err := auth.ReadTokens(agentRef); err != nil || tok.AccessToken != "tok_origin_a" {
		t.Fatalf("same-origin update should preserve cached token, got %+v, err=%v", tok, err)
	}
	if key, err := auth.ReadAPIKey(ciRef); err != nil || key != "jak_origin_a_secret" {
		t.Fatalf("same-origin update should preserve API key, got %q, err=%v", key, err)
	}

	// 2. Repointing to a new origin purges prod's registration, cached token, and API key, while keeping other environments untouched.
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-b.example.com", "--force"); err != nil {
		t.Fatalf("env add new-origin --force: %v", err)
	}
	cfg, err = sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if _, exists := cfg.Identities["agent1"].Environments["prod"]; exists {
		t.Fatalf("cross-origin env add --force must clear prod registration, got %+v", cfg.Identities["agent1"].Environments)
	}
	if cfg.Identities["agent1"].Environments["other"].ClientID != "cid_other" {
		t.Fatalf("cross-origin env add --force must preserve other environment registrations, got %+v", cfg.Identities["agent1"].Environments)
	}
	if _, err := auth.ReadTokens(agentRef); err == nil {
		t.Fatal("cross-origin env add --force must purge cached tokens for prod")
	}
	if _, err := auth.ReadAPIKey(ciRef); err == nil {
		t.Fatal("cross-origin env add --force must purge stored API keys for prod")
	}
}

func TestEnvAddForce_BrokerOriginChangePurgesCredentials(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com", "--broker-url", "https://broker-a.example.com"); err != nil {
		t.Fatal(err)
	}
	seedEnvironmentRegistration(t, "agent1", "prod", "cid_prod_a")
	ref := auth.IdentityRef{Identity: "agent1", Environment: "prod"}
	if err := auth.SaveTokens(ref, &auth.TokenSet{AccessToken: "tok_broker_repoint", ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatal(err)
	}
	if err := auth.SaveAPIKey(ref, "jak_broker_repoint_test"); err != nil {
		t.Fatal(err)
	}

	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com/v2", "--broker-url", "https://broker-b.example.com", "--force"); err != nil {
		t.Fatalf("env add broker-origin change: %v", err)
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := cfg.Identities["agent1"].Environments["prod"]; ok {
		t.Fatal("broker-origin change preserved the environment registration")
	}
	if _, err := auth.ReadTokens(ref); err == nil {
		t.Fatal("broker-origin change preserved the cached token")
	}
	if _, err := auth.ReadAPIKey(ref); err == nil {
		t.Fatal("broker-origin change preserved the stored API key")
	}
}

func TestEnvAddForce_SameBrokerOriginPreserves(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com", "--broker-url", "https://broker-a.example.com/v1"); err != nil {
		t.Fatal(err)
	}
	seedEnvironmentRegistration(t, "agent1", "prod", "cid_prod_a")
	ref := auth.IdentityRef{Identity: "agent1", Environment: "prod"}
	if err := auth.SaveAPIKey(ref, "jak_same_broker_test"); err != nil {
		t.Fatal(err)
	}

	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com:443/v2", "--broker-url", "https://broker-a.example.com:443/v2", "--force"); err != nil {
		t.Fatalf("env add equivalent broker origin: %v", err)
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Identities["agent1"].Environments["prod"].ClientID != "cid_prod_a" {
		t.Fatal("equivalent broker origin removed the environment registration")
	}
	if _, err := auth.ReadAPIKey(ref); err != nil {
		t.Fatalf("equivalent broker origin removed the stored API key: %v", err)
	}
}

func TestEnvAddForce_EmptyExistingOriginPurgesCredentials(t *testing.T) {
	withXDG(t)
	if err := sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
		c.Environments["prod"] = sdkconfig.Env{}
		c.Identities["agent1"] = sdkconfig.Identity{
			Type:         "agent",
			Environments: map[string]sdkconfig.EnvRegState{"prod": {ClientID: "cid"}},
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	ref := auth.IdentityRef{Identity: "agent1", Environment: "prod"}
	if err := auth.SaveAPIKeyForOrigin(ref, "jak_empty_origin_secret", "https://origin-a.example.com"); err != nil {
		t.Fatal(err)
	}
	if err := runJentic(t, "env", "add", "prod", "--url", "https://new.example.com", "--force"); err != nil {
		t.Fatal(err)
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := cfg.Identities["agent1"].Environments["prod"]; ok {
		t.Fatalf("origin-less repoint must remove the old registration, got %+v", cfg.Identities)
	}
	if _, err := auth.ReadAPIKey(ref); err == nil {
		t.Fatal("origin-less repoint must purge stored credentials")
	}
}

func TestEnvAddForce_PurgeFailureLeavesOldOriginAndLegacyCredentialIntact(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com"); err != nil {
		t.Fatal(err)
	}
	if err := sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
		c.Identities["agent1"] = sdkconfig.Identity{
			Type:         "agent",
			Environments: map[string]sdkconfig.EnvRegState{"prod": {ClientID: "cid_prod_a", Status: "approved"}},
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}

	const legacyKey = "jak_legacy_origin_a_test" // pragma: allowlist secret
	ref := auth.IdentityRef{Identity: "agent1", Environment: "prod"}
	if err := auth.SaveAPIKey(ref, legacyKey); err != nil {
		t.Fatal(err)
	}
	stem, err := ref.Stem()
	if err != nil {
		t.Fatal(err)
	}
	stateDir, err := sdkconfig.StateDir()
	if err != nil {
		t.Fatal(err)
	}
	legacyPath := filepath.Join(stateDir, stem+".apikey")
	if err := os.WriteFile(legacyPath, []byte(legacyKey), 0o600); err != nil {
		t.Fatal(err)
	}

	originalPurge := purgeEnvironmentCredentials
	t.Cleanup(func() { purgeEnvironmentCredentials = originalPurge })
	purgeEnvironmentCredentials = func(string) error {
		return errors.New("injected credential removal failure")
	}
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-b.example.com", "--force"); err == nil {
		t.Fatal("env add must fail when the pre-commit credential purge fails")
	}

	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if got := cfg.Environments["prod"].BaseURL; got != "https://origin-a.example.com" {
		t.Fatalf("purge failure changed base_url to %q", got)
	}
	if cfg.Identities["agent1"].Environments["prod"].ClientID != "cid_prod_a" {
		t.Fatal("purge failure removed the old origin's registration")
	}
	if _, err := auth.ReadAPIKey(ref); err != nil {
		t.Fatalf("purge failure must leave the legacy credential at the old origin: %v", err)
	}

	purgeEnvironmentCredentials = originalPurge
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-b.example.com", "--force"); err != nil {
		t.Fatalf("retry after restoring removal: %v", err)
	}
	if _, err := auth.ReadAPIKey(ref); err == nil {
		t.Fatal("successful retry must purge the legacy credential")
	}
}

func TestEnvAddForce_RevalidatesOldOriginAfterPurge(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com"); err != nil {
		t.Fatal(err)
	}

	originalPurge := purgeEnvironmentCredentials
	t.Cleanup(func() { purgeEnvironmentCredentials = originalPurge })
	purgeEnvironmentCredentials = func(name string) error {
		if err := originalPurge(name); err != nil {
			return err
		}
		return sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
			c.Environments[name] = sdkconfig.Env{BaseURL: "https://concurrent-origin.example.com"}
			return nil
		})
	}

	err := runJentic(t, "env", "add", "prod", "--url", "https://origin-b.example.com", "--force")
	if err == nil || !strings.Contains(err.Error(), "changed while") {
		t.Fatalf("concurrent origin change must abort the update, got %v", err)
	}
	cfg, loadErr := sdkconfig.Load()
	if loadErr != nil {
		t.Fatal(loadErr)
	}
	if got := cfg.Environments["prod"].BaseURL; got != "https://concurrent-origin.example.com" {
		t.Fatalf("stale prepared update overwrote concurrent origin: %q", got)
	}
}

func TestEnvAddForce_RevalidatesOldBrokerOriginAfterPurge(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com", "--broker-url", "https://broker-a.example.com"); err != nil {
		t.Fatal(err)
	}

	originalPurge := purgeEnvironmentCredentials
	t.Cleanup(func() { purgeEnvironmentCredentials = originalPurge })
	purgeEnvironmentCredentials = func(name string) error {
		if err := originalPurge(name); err != nil {
			return err
		}
		return sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
			env := c.Environments[name]
			env.BrokerURL = "https://concurrent-broker.example.com"
			c.Environments[name] = env
			return nil
		})
	}

	err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com", "--broker-url", "https://broker-b.example.com", "--force")
	if err == nil || !strings.Contains(err.Error(), "changed while") {
		t.Fatalf("concurrent broker-origin change must abort the update, got %v", err)
	}
	cfg, loadErr := sdkconfig.Load()
	if loadErr != nil {
		t.Fatal(loadErr)
	}
	if got := cfg.Environments["prod"].BrokerURL; got != "https://concurrent-broker.example.com" {
		t.Fatalf("stale prepared update overwrote concurrent broker origin: %q", got)
	}
}

func TestEnvDelete_PurgesCredentialsAndRegistrations(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com"); err != nil {
		t.Fatal(err)
	}
	if err := runJentic(t, "env", "add", "other", "--url", "https://other.example.com"); err != nil {
		t.Fatal(err)
	}
	seedEnvironmentRegistration(t, "agent1", "prod", "cid_prod")
	seedEnvironmentRegistration(t, "agent1", "other", "cid_other")
	ref := auth.IdentityRef{Identity: "agent1", Environment: "prod"}
	if err := auth.SaveTokens(ref, &auth.TokenSet{AccessToken: "tok_env_delete", ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatal(err)
	}
	if err := auth.SaveAPIKey(ref, "jak_env_delete_test"); err != nil {
		t.Fatal(err)
	}

	if err := runJentic(t, "env", "delete", "prod", "--yes"); err != nil {
		t.Fatalf("env delete: %v", err)
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := cfg.Environments["prod"]; ok {
		t.Fatal("environment remains after deletion")
	}
	if _, ok := cfg.Identities["agent1"].Environments["prod"]; ok {
		t.Fatal("environment deletion left its identity registration")
	}
	if cfg.Identities["agent1"].Environments["other"].ClientID != "cid_other" {
		t.Fatal("environment deletion removed another environment's registration")
	}
	if _, err := auth.ReadTokens(ref); err == nil {
		t.Fatal("environment deletion left its cached token")
	}
	if _, err := auth.ReadAPIKey(ref); err == nil {
		t.Fatal("environment deletion left its stored API key")
	}
}

func TestEnvDelete_PurgeFailurePreservesEnvironmentAndRegistrations(t *testing.T) {
	withXDG(t)
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com"); err != nil {
		t.Fatal(err)
	}
	seedEnvironmentRegistration(t, "agent1", "prod", "cid_prod")
	ref := auth.IdentityRef{Identity: "agent1", Environment: "prod"}
	if err := auth.SaveAPIKey(ref, "jak_env_delete_failure_test"); err != nil {
		t.Fatal(err)
	}
	originalPurge := purgeEnvironmentCredentials
	t.Cleanup(func() { purgeEnvironmentCredentials = originalPurge })
	purgeEnvironmentCredentials = func(string) error {
		return errors.New("injected environment credential removal failure")
	}

	if err := runJentic(t, "env", "delete", "prod", "--yes"); err == nil {
		t.Fatal("env delete must fail when its pre-commit credential purge fails")
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := cfg.Environments["prod"]; !ok {
		t.Fatal("failed credential purge deleted the environment")
	}
	if cfg.Identities["agent1"].Environments["prod"].ClientID != "cid_prod" {
		t.Fatal("failed credential purge removed the environment registration")
	}
	if _, err := auth.ReadAPIKey(ref); err != nil {
		t.Fatalf("failed credential purge removed the old environment's API key: %v", err)
	}
}

func TestRedactedErr_SecretBearingHTTPErrorBodiesNeverLeak(t *testing.T) {
	cases := []struct {
		name   string
		body   string
		secret string
	}{
		{
			name:   "json access_token in unstructured body",
			body:   `{"access_token":"at_super_secret_token_123","token_type":"bearer"}`,
			secret: "at_super_secret_token_123",
		},
		{
			name:   "json access_token in structured detail",
			body:   `{"detail":"upstream rejected payload {\"access_token\":\"at_detail_secret_456\"}","access_token":"at_detail_secret_456"}`,
			secret: "at_detail_secret_456",
		},
		{
			name:   "structured detail with unkeyed secret",
			body:   `{"detail":"upstream diagnostic marker at_unkeyed_detail_secret_999"}`,
			secret: "at_unkeyed_detail_secret_999",
		},
		{
			name:   "form-encoded client_secret",
			body:   `error=invalid_client&client_secret=cs_form_secret_789&grant_type=client_credentials`, // pragma: allowlist secret
			secret: "cs_form_secret_789",
		},
		{
			name:   "bearer authorization header in error text",
			body:   `{"detail":"upstream returned 401 for Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secretpayload"}`,
			secret: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secretpayload",
		},
		{
			name:   "basic authorization header in error text",
			body:   `{"detail":"proxy auth failed: Authorization: Basic dXNlcjpzdXBlcnNlY3JldHBhc3M="}`,
			secret: "dXNlcjpzdXBlcnNlY3JldHBhc3M=",
		},
		{
			name:   "raw non-json body with secret",
			body:   `upstream gateway error: token=raw_secret_value_xyz987`,
			secret: "raw_secret_value_xyz987",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			he := &HTTPError{StatusCode: http.StatusBadGateway, Body: tc.body}
			wrapped := fmt.Errorf("control call failed: %w", he)
			got := redactedErr(wrapped)
			if strings.Contains(got, tc.secret) {
				t.Errorf("redactedErr leaked secret %q in %q", tc.secret, got)
			}
			if !strings.Contains(got, "[REDACTED]") {
				t.Errorf("redactedErr output %q did not contain [REDACTED]", got)
			}
		})
	}
}

func TestRedactedErr_HTTPErrorOmitsUpstreamDetail(t *testing.T) {
	got := redactedErr(fmt.Errorf("local resolve context: %w", &HTTPError{
		StatusCode: http.StatusBadGateway,
		Body:       `{"detail":"upstream-only diagnostic marker"}`,
	}))
	if strings.Contains(got, "upstream-only diagnostic marker") {
		t.Fatalf("HTTP detail reached MCP error output: %q", got)
	}
	if !strings.Contains(got, "502") {
		t.Fatalf("HTTP status missing from MCP error output: %q", got)
	}
}

func TestMCPExecuteAndPoll_LogsAndSoftErrorsNeverEmitHTTPResponseDetails(t *testing.T) {
	const (
		resolveSecret       = "at_resolve_leak_secret_111"    // pragma: allowlist secret
		pollSecret          = "cs_poll_leak_secret_222"       // pragma: allowlist secret
		bearerSecret        = "Bearer_token_leak_333"         // pragma: allowlist secret
		resolveDetailSecret = "at_resolve_unkeyed_detail_444" // pragma: allowlist secret
		pollDetailSecret    = "cs_poll_unkeyed_detail_555"    // pragma: allowlist secret
	)

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/inspect":
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = fmt.Fprintf(w, `{"detail":"execute upstream diagnostic %s Authorization: Bearer %s","access_token":%q}`,
				resolveDetailSecret, bearerSecret, resolveSecret)
		case strings.HasPrefix(r.URL.Path, "/jobs/"):
			w.WriteHeader(http.StatusBadGateway)
			_, _ = fmt.Fprintf(w, `{"detail":"poll upstream diagnostic client_secret=%s rejected %s Authorization=Basic dXNlcjpwYXNz"}`,
				pollSecret, pollDetailSecret)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	var logBuf bytes.Buffer
	s := stampedTestMCPServer(t)
	s.logger = slog.New(slog.NewJSONHandler(&logBuf, nil))

	ctx := activeCtxWithBroker(srv.URL, srv.URL)

	// 1. Execute resolve failure logging via redactedErr(err).
	res, err := s.handleExecute(ctx, callToolRequest("execute", `{"operation_id":"op_secret_test"}`))
	if err != nil {
		t.Fatalf("handleExecute returned protocol error: %v", err)
	}
	if !res.IsError {
		t.Fatal("expected soft error from handleExecute when inspect 500s")
	}

	// 2. Poll failure logging via redactedErr(err) in get_execution_result.
	resPoll, err := s.handleGetExecutionResult(ctx, callToolRequest("get_execution_result", `{"job_id":"job_123","timeout":0}`))
	if err != nil {
		t.Fatalf("handleGetExecutionResult returned protocol error: %v", err)
	}
	if !resPoll.IsError {
		t.Fatal("expected soft error from handleGetExecutionResult when job poll 502s")
	}

	logged := logBuf.String()
	outputs := logged + toolResultText(res) + toolResultText(resPoll)
	for _, secret := range []string{ // pragma: allowlist secret
		resolveSecret, pollSecret, bearerSecret, resolveDetailSecret, pollDetailSecret, "dXNlcjpwYXNz", // pragma: allowlist secret
	} {
		if strings.Contains(outputs, secret) {
			t.Errorf("MCP output leaked secret %q: %s", secret, outputs)
		}
	}
	for _, upstreamDetail := range []string{"execute upstream diagnostic", "poll upstream diagnostic"} {
		if strings.Contains(outputs, upstreamDetail) {
			t.Errorf("MCP output exposed upstream detail %q: %s", upstreamDetail, outputs)
		}
	}
	if !strings.Contains(logged, "[REDACTED]") {
		t.Errorf("expected [REDACTED] in MCP log, got: %s", logged)
	}
	for _, status := range []string{"500", "502"} {
		if !strings.Contains(outputs, status) {
			t.Errorf("MCP output omitted HTTP status %s: %s", status, outputs)
		}
	}
}

func diskBackedActiveCtx(baseURL, brokerURL, caCertPath string) context.Context {
	return clictx.WithActiveState(context.Background(), &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:    "test-agent",
			EnvironmentName: "test",
			BaseURL:         baseURL,
			BrokerURL:       brokerURL,
			CACertPath:      caCertPath,
		},
		Mode: clictx.ModeHuman,
	})
}

func TestEnvDeleteThenAdd_DoesNotSendOldKeyToNewOrigin(t *testing.T) {
	withXDG(t)
	serverAAuth := make(chan string, 1)
	serverA := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		serverAAuth <- r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"origin":"a"}`))
	}))
	defer serverA.Close()
	serverBAuth := make(chan string, 1)
	serverB := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		serverBAuth <- r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"origin":"b"}`))
	}))
	defer serverB.Close()

	if err := runJentic(t, "env", "add", "prod", "--url", serverA.URL); err != nil {
		t.Fatal(err)
	}
	ref := auth.IdentityRef{Identity: "ci", Environment: "prod"}
	if err := auth.SaveAPIKey(ref, "jak_delete_readd_test"); err != nil {
		t.Fatal(err)
	}
	removeAPIKeyOriginSidecar(t, ref)
	if err := runJentic(t, "env", "delete", "prod", "--yes"); err != nil {
		t.Fatalf("env delete: %v", err)
	}
	if err := runJentic(t, "env", "add", "prod", "--url", serverB.URL); err != nil {
		t.Fatalf("env re-add: %v", err)
	}

	req, err := http.NewRequest(http.MethodGet, serverB.URL+"/credentials", nil)
	if err != nil {
		t.Fatal(err)
	}
	attachErr := auth.AttachAuth(auth.Credentials{
		BaseURL:         serverB.URL,
		IdentityName:    ref.Identity,
		EnvironmentName: ref.Environment,
	}, req)
	if attachErr == nil {
		resp, err := serverB.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		_ = resp.Body.Close()
	}
	select {
	case got := <-serverBAuth:
		if got != "" {
			t.Fatal("new origin received the deleted environment's API key")
		}
		t.Fatal("authentication unexpectedly dispatched a request after environment deletion")
	default:
	}
	if attachErr == nil {
		t.Fatal("deleted environment credentials still authenticate after re-adding the environment")
	}
}

func TestMCPExecute_BrokerRepointDoesNotLeakBearer(t *testing.T) {
	withXDG(t)
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{}`))
	}))
	defer control.Close()
	brokerA := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"broker":"a"}`))
	}))
	defer brokerA.Close()
	brokerBAuth := make(chan string, 1)
	brokerB := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		brokerBAuth <- r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"broker":"b"}`))
	}))
	defer brokerB.Close()

	if err := runJentic(t, "env", "add", "test", "--url", control.URL, "--broker-url", brokerA.URL); err != nil {
		t.Fatal(err)
	}
	seedEnvironmentRegistration(t, "test-agent", "test", "cid_test")
	ref := auth.IdentityRef{Identity: "test-agent", Environment: "test"}
	if err := auth.SaveAPIKey(ref, "jak_mcp_broker_repoint_test"); err != nil {
		t.Fatal(err)
	}
	removeAPIKeyOriginSidecar(t, ref)
	if err := runJentic(t, "env", "add", "test", "--url", control.URL, "--broker-url", brokerB.URL, "--force"); err != nil {
		t.Fatalf("broker repoint: %v", err)
	}

	s := stampedTestMCPServer(t)
	res, err := s.handleExecute(
		diskBackedActiveCtx(control.URL, brokerA.URL, ""),
		callToolRequest("execute", `{"operation_id":"GET:/v1/pets"}`),
	)
	if err != nil {
		t.Fatalf("handleExecute returned a protocol error: %v", err)
	}
	select {
	case got := <-brokerBAuth:
		if got != "" {
			t.Fatal("repointed broker received the old environment bearer")
		}
		t.Fatalf("repointed broker received an unexpected unauthenticated request: %s", toolResultText(res))
	default:
	}
}

func TestMCPGetExecutionResult_RefreshesOriginBetweenPolls(t *testing.T) {
	withXDG(t)
	const apiKey = "jak_poll_repoint_test" // pragma: allowlist secret
	ref := auth.IdentityRef{Identity: "test-agent", Environment: "test"}

	var pollsB int
	serverB := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/jobs/job_9":
			pollsB++
			_, _ = fmt.Fprint(w, `{"job_id":"job_9","kind":"execution","status":"completed","created_at":"2026-10-06T12:00:00Z"}`)
		case "/jobs/job_9/result":
			_, _ = fmt.Fprint(w, `{"origin":"b"}`)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer serverB.Close()

	var pollsA int
	serverA := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/jobs/job_9":
			pollsA++
			if pollsA == 1 {
				if err := sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
					c.Environments["test"] = sdkconfig.Env{
						BaseURL:   serverB.URL,
						BrokerURL: "https://broker-b.example.com",
					}
					return nil
				}); err != nil {
					t.Errorf("repoint environment: %v", err)
					w.WriteHeader(http.StatusInternalServerError)
					return
				}
				if err := auth.SaveAPIKeyForOrigin(ref, apiKey, serverB.URL); err != nil {
					t.Errorf("bind test credential to the new origin: %v", err)
					w.WriteHeader(http.StatusInternalServerError)
					return
				}
				_, _ = fmt.Fprint(w, `{"job_id":"job_9","kind":"execution","status":"running","created_at":"2026-10-06T12:00:00Z"}`)
				return
			}
			_, _ = fmt.Fprint(w, `{"job_id":"job_9","kind":"execution","status":"completed","created_at":"2026-10-06T12:00:00Z"}`)
		case "/jobs/job_9/result":
			_, _ = fmt.Fprint(w, `{"origin":"a"}`)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer serverA.Close()

	if err := sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
		c.Environments["test"] = sdkconfig.Env{
			BaseURL:   serverA.URL,
			BrokerURL: "https://broker-a.example.com",
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := auth.SaveAPIKey(ref, apiKey); err != nil {
		t.Fatal(err)
	}

	s := heldTestServer(t)
	res, err := s.handleGetExecutionResult(
		diskBackedActiveCtx(serverA.URL, "https://broker-a.example.com", ""),
		callToolRequest("get_execution_result", `{"job_id":"job_9","timeout":5}`),
	)
	if err != nil || res.IsError {
		t.Fatalf("get_execution_result after repoint: err=%v result=%s", err, toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	result, _ := payload["result"].(map[string]any)
	if payload["status"] != "completed" || result["origin"] != "b" || pollsA != 1 || pollsB != 1 {
		t.Fatalf("payload=%v polls_a=%d polls_b=%d, want the second poll to use origin B", payload, pollsA, pollsB)
	}
}

func TestRefreshActiveState_ReloadsAllEnvironmentRouting(t *testing.T) {
	withXDG(t)
	if err := sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
		c.Environments["test"] = sdkconfig.Env{
			BaseURL:    "https://origin-a.example.com",
			BrokerURL:  "https://broker-a.example.com",
			CACertPath: "/ca/a.pem",
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	ctx := diskBackedActiveCtx("https://origin-a.example.com", "https://broker-a.example.com", "/ca/a.pem")
	if err := sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
		c.Environments["test"] = sdkconfig.Env{
			BaseURL:    "https://origin-b.example.com",
			BrokerURL:  "https://broker-b.example.com",
			CACertPath: "/ca/b.pem",
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}

	refreshed := clictx.ActiveContext(refreshActiveState(ctx))
	if refreshed == nil || refreshed.BaseURL != "https://origin-b.example.com" ||
		refreshed.BrokerURL != "https://broker-b.example.com" || refreshed.CACertPath != "/ca/b.pem" {
		t.Fatalf("refreshed state = %+v, want origin B's complete routing", refreshed)
	}
	original := clictx.ActiveContext(ctx)
	if original.BaseURL != "https://origin-a.example.com" || original.BrokerURL != "https://broker-a.example.com" ||
		original.CACertPath != "/ca/a.pem" {
		t.Fatalf("refresh mutated the caller's state: %+v", original)
	}
}

func TestRefreshActiveState_InvalidDiskStateFailsClosed(t *testing.T) {
	for _, tc := range []struct {
		name       string
		invalidate func(*testing.T)
	}{
		{
			name: "config load error",
			invalidate: func(t *testing.T) {
				dir, err := sdkconfig.ConfigDir()
				if err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(dir, "config.yaml"), []byte("environments: ["), 0o600); err != nil {
					t.Fatal(err)
				}
			},
		},
		{
			name: "deleted environment",
			invalidate: func(t *testing.T) {
				if err := sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
					delete(c.Environments, "test")
					return nil
				}); err != nil {
					t.Fatal(err)
				}
			},
		},
		{
			name: "empty base URL",
			invalidate: func(t *testing.T) {
				if err := sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
					c.Environments["test"] = sdkconfig.Env{BrokerURL: "https://broker-a.example.com"}
					return nil
				}); err != nil {
					t.Fatal(err)
				}
			},
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			withXDG(t)
			if err := sdkconfig.MutateConfig(func(c *sdkconfig.Config) error {
				c.Environments["test"] = sdkconfig.Env{
					BaseURL:    "https://origin-a.example.com",
					BrokerURL:  "https://broker-a.example.com",
					CACertPath: "/ca/a.pem",
				}
				return nil
			}); err != nil {
				t.Fatal(err)
			}
			ctx := diskBackedActiveCtx("https://origin-a.example.com", "https://broker-a.example.com", "/ca/a.pem")
			tc.invalidate(t)

			refreshed := refreshActiveState(ctx)
			if got := clictx.ActiveContext(refreshed); got != nil {
				t.Fatalf("invalid disk state retained stale routing: %+v", got)
			}
			state := clictx.FromContext(refreshed)
			if state == nil || state.ResolvedState != nil || state.Mode != clictx.ModeHuman {
				t.Fatalf("refresh did not preserve a degraded fencing state: %+v", state)
			}
			if _, err := clictx.GetControlClient(refreshed); !errors.Is(err, clictx.ErrNoConfig) {
				t.Fatalf("degraded state constructed a client or returned the wrong error: %v", err)
			}
		})
	}
}
