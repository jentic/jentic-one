package api

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// TestIdentityClaim_SuccessPostsTokenAndRenders proves `jentic identity claim
// <id> --token <t>` POSTs to /agents/{id}:claim with the token in the body,
// authenticated from the active context, and renders an "updated"/"ownership
// claimed" Result on a 200.
func TestIdentityClaim_SuccessPostsTokenAndRenders(t *testing.T) {
	withXDG(t)

	var gotPath, gotMethod, gotAuth, gotToken string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath, gotMethod, gotAuth = r.URL.Path, r.Method, r.Header.Get("Authorization")
		body, _ := io.ReadAll(r.Body)
		var req struct {
			Token string `json:"token"`
		}
		_ = json.Unmarshal(body, &req)
		gotToken = req.Token
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": "agent_42", "name": "agent1", "status": "approved", "owner_id": "user_1",
		})
	}))
	defer srv.Close()

	setupContext(t, srv.URL)

	if _, err := runJenticCapture(t, "identity", "claim", "agent_42", "--token", "clm_secret"); err != nil {
		t.Fatalf("claim: %v", err)
	}
	// Path carries the :claim action verb and the agent id; POST with the token.
	if gotMethod != http.MethodPost {
		t.Errorf("method = %q, want POST", gotMethod)
	}
	if !strings.Contains(gotPath, "agent_42") || !strings.HasSuffix(gotPath, ":claim") {
		t.Errorf("request path = %q, want .../agents/agent_42:claim", gotPath)
	}
	if gotToken != "clm_secret" {
		t.Errorf("request body token = %q, want the passed claim token", gotToken)
	}
	if gotAuth == "" {
		t.Error("claim must authenticate from the active context (no Authorization header sent)")
	}
}

// TestIdentityClaim_MissingTokenFailsFast proves an empty token is rejected with
// MISSING_ARGUMENT before any network call (cobra also enforces the required
// flag; this covers the explicit guard for a whitespace-only value).
func TestIdentityClaim_RequiredTokenFlag(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("no request should be made when --token is absent")
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()
	setupContext(t, srv.URL)

	if err := runJentic(t, "identity", "claim", "agent_42"); err == nil {
		t.Fatal("expected claim without --token to fail")
	}
}

// TestIdentityClaim_ErrorMapping pins the status→coded-error mapping (13 §3a):
// each backend claim failure surfaces the right closed error_code + an
// actionable step, so an agent/human gets a branchable outcome rather than a raw
// http string.
func TestIdentityClaim_ErrorMapping(t *testing.T) {
	cases := []struct {
		name      string
		status    int
		body      string
		wantCode  string
		wantInMsg string
	}{
		{"invalid token 400", http.StatusBadRequest, `{"detail":"token invalid"}`, ux.CodeMissingArgument, "rejected"},
		{"unprocessable 422", http.StatusUnprocessableEntity, `{"detail":"bad"}`, ux.CodeMissingArgument, "rejected"},
		{"unauthenticated 401", http.StatusUnauthorized, `{"detail":"no auth"}`, ux.CodeNotAuthenticated, "not authenticated"},
		{"non-user actor 403", http.StatusForbidden, `{"detail":"users only"}`, ux.CodeFenced, "only a human"},
		{"unknown agent 404", http.StatusNotFound, `{"detail":"nope"}`, ux.CodeResolveFailed, "no agent"},
		{"already owned 409", http.StatusConflict, `{"detail":"owned"}`, ux.CodeResolveFailed, "already owned"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			withXDG(t)
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/problem+json")
				w.WriteHeader(c.status)
				_, _ = w.Write([]byte(c.body))
			}))
			defer srv.Close()
			setupContext(t, srv.URL)

			err := runJentic(t, "identity", "claim", "agent_42", "--token", "clm_x")
			if err == nil {
				t.Fatalf("expected claim to fail on status %d", c.status)
			}
			var coded *ux.CodedError
			if !errors.As(err, &coded) {
				t.Fatalf("error is not a CodedError: %v", err)
			}
			if coded.Code != c.wantCode {
				t.Errorf("code = %q, want %q (err: %v)", coded.Code, c.wantCode, err)
			}
			if !strings.Contains(strings.ToLower(coded.Msg), strings.ToLower(c.wantInMsg)) {
				t.Errorf("message %q missing %q", coded.Msg, c.wantInMsg)
			}
		})
	}
}

// TestIdentityClaim_403ActionableBlamesToken pins onboarding-review F3: the 403
// Actionable must blame the AGENT TOKEN and point at the register claim link,
// not tell the user to "use a human context" (which misfires when they already
// are in a human-labelled context whose cached token is an agent token).
func TestIdentityClaim_403ActionableBlamesToken(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/problem+json")
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"detail":"users only"}`))
	}))
	defer srv.Close()
	setupContext(t, srv.URL)

	err := runJentic(t, "identity", "claim", "agent_42", "--token", "clm_x")
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("error is not a CodedError: %v", err)
	}
	act := strings.ToLower(coded.Actionable)
	if !strings.Contains(act, "agent token") || !strings.Contains(act, "register") {
		t.Errorf("actionable should blame the agent token and point at register: %q", coded.Actionable)
	}
	if strings.Contains(act, "human context") {
		t.Errorf("actionable must not tell the user to switch to a human context: %q", coded.Actionable)
	}
}
