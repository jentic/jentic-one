package api

import (
	"bytes"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
)

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
				"prod":   {ClientID: "cid_prod_a", Status: "approved"},
				"other":  {ClientID: "cid_other", Status: "approved"},
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

	// 1. Same-origin path or broker-only update preserves registration, cached token, and API key.
	if err := runJentic(t, "env", "add", "prod", "--url", "https://origin-a.example.com:443/v2", "--broker-url", "https://broker-b.example.com", "--force"); err != nil {
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
			name:   "form-encoded client_secret",
			body:   `error=invalid_client&client_secret=cs_form_secret_789&grant_type=client_credentials`,
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

func TestMCPExecuteAndPoll_LogsNeverEmitRawSecretsFromHTTPError(t *testing.T) {
	const (
		resolveSecret = "at_resolve_leak_secret_111"
		pollSecret    = "cs_poll_leak_secret_222"
		bearerSecret  = "Bearer_token_leak_333"
	)

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/inspect":
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = w.Write([]byte(`{"access_token":"` + resolveSecret + `","Authorization":"Bearer ` + bearerSecret + `"}`))
		case strings.HasPrefix(r.URL.Path, "/jobs/"):
			w.WriteHeader(http.StatusBadGateway)
			_, _ = w.Write([]byte(`client_secret=` + pollSecret + `&Authorization=Basic dXNlcjpwYXNz`))
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
	for _, secret := range []string{resolveSecret, pollSecret, bearerSecret, "dXNlcjpwYXNz"} {
		if strings.Contains(logged, secret) {
			t.Errorf("MCP log leaked secret %q: %s", secret, logged)
		}
	}
	if !strings.Contains(logged, "[REDACTED]") {
		t.Errorf("expected [REDACTED] in MCP log, got: %s", logged)
	}
	if errors.Is(nil, auth.ErrOriginMismatch) {
		t.Fatal("unreachable")
	}
}
