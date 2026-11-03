package api

// access_test.go covers `jentic access show` and the show_access MCP tool
// against an httptest control plane: both read the calling agent's own
// binding (the agent id comes from /me, never from arguments) and map a
// missing binding to RESOLVE_FAILED pointing at whoami.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

const accessMeBody = `{"type":"agent","id":"agnt_1","name":"test-agent","status":"active",
	"permissions":["owner:credentials:read"],"token_permissions":["owner:credentials:read"],
	"credential_bindings":[]}`

const accessBody = `{"data":[{"id":"apr_1","effect":"allow","methods":["GET"],"path":"/repos",
	"match_mode":"prefix","operations":null,"_system":false,"_comment":null}],
	"rule_set":null,"granted_scopes":["repo"],"suspended":false}`

// accessControlPlane serves /me and the binding read for cred_1; any other
// credential answers 404, as the route does for a binding the agent lacks.
func accessControlPlane(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/me":
			_, _ = w.Write([]byte(accessMeBody))
		case "/credentials/cred_1/agents/agnt_1/permissions":
			_, _ = w.Write([]byte(accessBody))
		default:
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"type":"credential_not_found","status":404}`))
		}
	}))
}

func TestAccessShow_RendersTheCallersBinding(t *testing.T) {
	withXDG(t)
	srv := accessControlPlane(t)
	defer srv.Close()

	out, err := runConnectTree(t, srv.URL, "access", "show", "cred_1")
	if err != nil {
		t.Fatalf("access show: %v\n%s", err, out)
	}
	for _, want := range []string{"apr_1", "/repos", "granted_scopes", "repo", "suspended"} {
		if !strings.Contains(out, want) {
			t.Errorf("access show output missing %q\n---\n%s", want, out)
		}
	}
}

func TestMCPShowAccess_ReturnsTheBinding(t *testing.T) {
	srv := accessControlPlane(t)
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleShowAccess(activeCtx(srv.URL), callToolRequest("show_access", `{"credential_id":"cred_1"}`))
	if err != nil {
		t.Fatalf("handleShowAccess: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %s", toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	rules, _ := payload["data"].([]any)
	if len(rules) != 1 || rules[0].(map[string]any)["id"] != "apr_1" {
		t.Errorf("data = %v, want the one rule with its id", payload["data"])
	}
	if payload["suspended"] != false || payload["schema_version"] != mcpSchemaVersion {
		t.Errorf("payload = %v, want suspended false and the schema version", payload)
	}
}

func TestMCPShowAccess_UnboundCredentialPointsAtWhoami(t *testing.T) {
	srv := accessControlPlane(t)
	defer srv.Close()

	s := stampedTestMCPServer(t)
	res, err := s.handleShowAccess(activeCtx(srv.URL), callToolRequest("show_access", `{"credential_id":"cred_other"}`))
	if err != nil {
		t.Fatalf("handleShowAccess: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want a soft error, got %s", toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeResolveFailed || payload["next_tool"] != "whoami" {
		t.Errorf("payload = %v, want RESOLVE_FAILED with next_tool whoami", payload)
	}
}

func TestMCPShowAccess_MissingCredentialIDIsInvalidParams(t *testing.T) {
	s := stampedTestMCPServer(t)
	if _, err := s.handleShowAccess(activeCtx("http://127.0.0.1:0"), callToolRequest("show_access", `{}`)); err == nil {
		t.Fatal("want an invalid-params error for a missing credential_id")
	}
}
