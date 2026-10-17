package golden

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// mockControl serves canned control-plane responses for the golden cases. Each
// case only needs a handful of routes; unknown routes 404 so a drifted request
// surfaces as an obvious golden change rather than a hang.
func mockControl(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/search":
			_, _ = w.Write([]byte(`{"data":[` +
				`{"type":"operation","api":{"vendor":"acme","name":"pets","version":"v1","host":"acme.com"},"operation_id":"op1","method":"GET","url":"/pets","name":"List Pets","relevance_score":0.9},` +
				`{"type":"operation","api":{"vendor":"acme","name":"pets","version":"v1","host":"acme.com"},"operation_id":"op2","method":"POST","url":"/pets","name":"Create Pet","relevance_score":0.8}` +
				`],"has_more":false,"next_cursor":""}`))
		case r.URL.Path == "/apis":
			_, _ = w.Write([]byte(`{"data":[` +
				`{"api":{"vendor":"stripe.com","name":"api","version":"v1"},"current_revision_id":"r1","operation_count":12}` +
				`],"has_more":false,"next_cursor":""}`))
		case r.URL.Path == "/catalog":
			_, _ = w.Write([]byte(`{"data":[` +
				`{"entry_id":"public/stripe.com","source_slug":"public","catalog_api_id":"stripe.com","vendor":"stripe.com",` +
				`"display_vendor":"stripe.com","title":"Stripe API","version":"2024-06-20","tags":["payments"],` +
				`"human_url":"https://github.com/APIs-guru/openapi-directory/tree/main/APIs/stripe.com","has_spec":true,` +
				`"registered":false,"update_available":false,"alternates":1,` +
				`"_links":{"self":"/catalog/public/stripe.com","operations":"/catalog/public/stripe.com/operations",` +
				`"import":"/catalog/public/stripe.com:import","human":"https://github.com/APIs-guru/openapi-directory/tree/main/APIs/stripe.com",` +
				`"alternates":["internal/stripe.com"]}}` +
				`],"catalog_total":1,"registered_count":0,"outdated_count":0,"oldest_source_sync_age_seconds":42,` +
				`"has_more":false,"next_cursor":""}`))
		case strings.HasPrefix(r.URL.Path, "/catalog/"):
			// An old-style (pre-entry_id) id: the server names the caller's
			// visible entry in the problem body.
			w.Header().Set("Content-Type", "application/problem+json")
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"type":"about:blank","title":"Not Found","status":404,` +
				`"detail":"catalog entry not found","did_you_mean":"public/stripe.com"}`))
		default:
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"detail":"not found"}`))
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

// agentEnv forces the agent audience so the recorded contract is the machine
// shape (pure JSON on stdout, no prompts), which is what agents depend on.
// agentEnv forces the agent audience so the recorded contract is the machine
// envelope an autonomous caller actually parses (JENTIC_MODE=agent), with ANSI
// suppressed for good measure.
var agentEnv = map[string]string{"JENTIC_MODE": "agent", "NO_COLOR": "1"}

// TestGolden_AgentContract freezes the observable contract of the core
// agent-facing commands (impl/0.0 §2a scope, post-activation). Human-mode ANSI
// is deliberately out of scope; we assert the structural JSON/exit/stderr
// contract that agents parse.
//
// New cases: add a row, run `go test ./tests/golden -update`, and commit the
// generated testdata. Changing an EXISTING golden requires citing the
// authorizing BC number (14_breaking_changes.md) in the PR.
func TestGolden_AgentContract(t *testing.T) {
	srv := mockControl(t)

	cases := []struct {
		name string
		args []string
		seed bool // seed the file-less env session pointed at the mock
	}{
		{
			name: "search_json",
			args: []string{"search", "pets", "--json"},
			seed: true,
		},
		{
			name: "apis_list_json",
			args: []string{"apis", "list", "--json"},
			seed: true,
		},
		{
			name: "catalog_list_json",
			args: []string{"catalog", "list", "--json"},
			seed: true,
		},
		{
			// An old-style id against the multi-source catalog: the coded
			// RESOLVE_FAILED envelope carries the server's did_you_mean.
			name: "catalog_show_did_you_mean_json",
			args: []string{"catalog", "show", "stripe.com", "--json"},
			seed: true,
		},
		{
			// Unknown command: stable usage-error contract (exit + stderr form).
			name: "unknown_command",
			args: []string{"definitely-not-a-command"},
			seed: false,
		},
		{
			// No session at all: the canonical no-context resolve error agents
			// must be able to parse and act on.
			name: "no_context_json",
			args: []string{"search", "pets", "--json"},
			seed: false,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			home := t.TempDir()
			if tc.seed {
				seedSession(t, srv.URL)
			} else {
				t.Setenv("JENTIC_BASE_URL", "")
				t.Setenv("JENTIC_BEARER_TOKEN", "")
			}
			got := runAPI(t, home, agentEnv, tc.args...)
			assertGolden(t, tc.name, got)
			// AGT-1/AGT-5: every data-plane --json SUCCESS must carry
			// schema_version so an agent can detect the envelope version. The
			// success cases are the seeded list commands; the error/usage cases
			// carry it via the AgentError envelope (asserted by the golden itself).
			if tc.seed && got.exitCode == 0 && strings.Contains(strings.Join(tc.args, " "), "--json") {
				if !strings.Contains(got.stdout, `"schema_version"`) {
					t.Errorf("%s: --json success output missing schema_version:\n%s", tc.name, got.stdout)
				}
				if !strings.Contains(got.stdout, `"data"`) {
					t.Errorf("%s: --json list output missing standard `data` key:\n%s", tc.name, got.stdout)
				}
			}
		})
	}
}
