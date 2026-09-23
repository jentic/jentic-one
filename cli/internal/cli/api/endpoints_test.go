package api

import (
	"errors"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

const sampleReference = `{
  "schema": "jentic.endpoint-permission-tree/v1",
  "total": 4,
  "endpoints": [
    {
      "method": "GET",
      "path": "/agents",
      "summary": "List Agents",
      "public": false,
      "actor_types": ["user"],
      "required_permissions": ["agents:read"],
      "typical_caller": "operator"
    },
    {
      "method": "POST",
      "path": "/capabilities:execute",
      "summary": "Execute Capability",
      "public": false,
      "actor_types": ["agent"],
      "required_permissions": [],
      "typical_caller": "agent"
    },
    {
      "method": "POST",
      "path": "/credentials",
      "summary": "Create Credential",
      "public": false,
      "actor_types": ["user", "agent"],
      "required_permissions": [],
      "typical_caller": "any"
    },
    {
      "method": "GET",
      "path": "/health",
      "summary": "Health",
      "public": true
    }
  ]
}`

func TestParseEndpoints(t *testing.T) {
	eps, err := parseEndpoints([]byte(sampleReference))
	if err != nil {
		t.Fatalf("parseEndpoints: %v", err)
	}
	if len(eps) != 4 {
		t.Fatalf("expected 4 endpoints, got %d", len(eps))
	}
	byPath := map[string]endpoint{}
	for _, ep := range eps {
		byPath[ep.Method+" "+ep.Path] = ep
	}

	if got := byPath["GET /agents"]; len(got.Permissions) != 1 || got.Permissions[0] != "agents:read" {
		t.Errorf("/agents permissions = %v, want [agents:read]", got.Permissions)
	}
	if got := byPath["GET /agents"]; got.TypicalCaller != "operator" {
		t.Errorf("/agents typical caller = %q, want operator", got.TypicalCaller)
	}
	if got := byPath["GET /health"]; !got.Public {
		t.Errorf("/health should be public")
	}
}

// A reference payload under another schema id (e.g. the pre-rename
// `jentic.endpoint-scope-tree/v1`, whose endpoints carry `required_scopes`)
// must be refused rather than decoded into endpoints that require nothing.
func TestParseEndpointsRejectsUnknownSchema(t *testing.T) {
	body := `{"schema":"jentic.endpoint-scope-tree/v1","endpoints":[` +
		`{"method":"GET","path":"/agents","required_scopes":["agents:read"]}]}`
	_, err := parseEndpoints([]byte(body))
	var ce *ux.CodedError
	if !errors.As(err, &ce) {
		t.Fatalf("parseEndpoints error = %v, want a *ux.CodedError", err)
	}
	if !strings.Contains(ce.Msg, "jentic.endpoint-scope-tree/v1") {
		t.Errorf("Msg = %q, want it to name the server's schema", ce.Msg)
	}
}

func TestEndpointGroup(t *testing.T) {
	cases := []struct {
		name string
		ep   endpoint
		want string
	}{
		{"operator", endpoint{TypicalCaller: "operator", Permissions: []string{"agents:read"}}, groupOperator},
		{"agent", endpoint{TypicalCaller: "agent"}, groupAgent},
		{"any", endpoint{TypicalCaller: "any"}, groupAny},
		{"unstamped-any", endpoint{ActorTypes: []string{"user"}}, groupAny},
		{"public", endpoint{Public: true}, groupPublic},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.ep.group(); got != tc.want {
				t.Errorf("group() = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestFilterEndpoints(t *testing.T) {
	eps := []endpoint{
		{Method: "GET", Path: "/a", ActorTypes: []string{"user"}, Permissions: []string{"agents:read"}},
		{Method: "GET", Path: "/b", ActorTypes: []string{"agent"}, Permissions: []string{"capabilities:execute"}},
	}
	if got := filterEndpoints(eps, "agents:read", ""); len(got) != 1 || got[0].Path != "/a" {
		t.Errorf("permission filter = %v, want only /a", got)
	}
	if got := filterEndpoints(eps, "", "agent"); len(got) != 1 || got[0].Path != "/b" {
		t.Errorf("actor filter = %v, want only /b", got)
	}
	if got := filterEndpoints(eps, "", ""); len(got) != 2 {
		t.Errorf("no filter should return all, got %d", len(got))
	}
}
