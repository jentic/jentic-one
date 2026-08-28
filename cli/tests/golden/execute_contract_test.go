package golden

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestGolden_ExecuteContract freezes the observable execute contract — the
// stdout envelope, the stderr error/recovery surface, and the exit code — for
// the resolve × broker outcome matrix (phase-0 §0.2 of the local-MCP plan):
//
//	resolve ok   × broker 2xx                  → envelope, exit 0
//	resolve ok   × upstream 4xx pass-through   → envelope, exit 0 (origin: upstream)
//	resolve ok   × broker denial + directive   → envelope + recovery stderr, exit 2
//	resolve ok   × broker denial, no directive → envelope + synthesized stderr, exit 2
//	resolve ok   × transport failure           → coded TRANSPORT_ERROR, exit 1
//	resolve fail (404 / backend error)         → coded RESOLVE_FAILED, exit 2
//
// These goldens are recorded BEFORE the agentops extraction so the refactor can
// prove byte-identical behavior. The transport case uses the SEC-1 secure-URL
// refusal (plaintext http to a non-loopback broker) rather than a real dial
// failure: a refused dial's error string embeds an ephemeral port and
// OS-specific text, which can never be golden-stable. Dial-level transport
// failures stay covered by the api unit tests, which assert fields, not bytes.
func TestGolden_ExecuteContract(t *testing.T) {
	// Handlers keyed by role. Each case runs one httptest server that plays
	// both the control plane (/inspect) and the broker catch-all — the same
	// dual-role shape the api unit tests use. Every handler suppresses the
	// auto Date header so the recorded envelope headers are byte-stable.
	inspectOK := func(w http.ResponseWriter, _ *http.Request) {
		w.Header()["Date"] = nil
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"method":"GET","url":"https://upstream.example/v1/pets"}`))
	}
	inspectNotFound := func(w http.ResponseWriter, _ *http.Request) {
		w.Header()["Date"] = nil
		w.Header().Set("Content-Type", "application/problem+json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"operation not found"}`))
	}
	inspectBackendError := func(w http.ResponseWriter, _ *http.Request) {
		w.Header()["Date"] = nil
		w.Header().Set("Content-Type", "application/problem+json")
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"detail":"registry exploded"}`))
	}

	broker200 := func(w http.ResponseWriter, _ *http.Request) {
		w.Header()["Date"] = nil
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Jentic-Execution-Id", "exec-123")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`[{"id":1,"name":"Fido"}]`))
	}
	// A denial-class status stamped origin UPSTREAM: the broker successfully
	// proxied the call and mirrored the upstream's own 403 — NOT a denial.
	brokerUpstream403 := func(w http.ResponseWriter, _ *http.Request) {
		w.Header()["Date"] = nil
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Jentic-Error-Origin", "upstream")
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"error":"upstream said no"}`))
	}
	// A broker denial carrying a rich agent_directive: every rendering branch
	// (instruction, run:, open:, candidates, retry-after, stuck?) is frozen.
	brokerDenial403Directive := func(w http.ResponseWriter, _ *http.Request) {
		w.Header()["Date"] = nil
		w.Header().Set("Content-Type", "application/problem+json")
		w.Header().Set("Jentic-Error-Origin", "broker")
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{
			"type": "no_toolkit_binding",
			"title": "No toolkit binding for this API",
			"status": 403,
			"error_origin": "broker",
			"agent_directive": {
				"strategy": "wait",
				"parameters": {
					"suggested_command": "jentic access request --toolkit acme/pets --wait",
					"provisioning_url": "https://console.example/connect/acme",
					"candidates": ["acme/pets", "acme/pets-admin"],
					"retry_after_seconds": 30
				},
				"human_readable_instruction": "You are not bound to a toolkit for this API."
			}
		}`))
	}
	// A broker denial with NO agent_directive (e.g. action_denied): exit 2 with
	// the synthesized status-keyed recovery, never a dead end.
	brokerDenial424NoDirective := func(w http.ResponseWriter, _ *http.Request) {
		w.Header()["Date"] = nil
		w.Header().Set("Content-Type", "application/problem+json")
		w.Header().Set("Jentic-Error-Origin", "broker")
		w.WriteHeader(http.StatusFailedDependency)
		_, _ = w.Write([]byte(`{
			"type": "credential_not_provisioned",
			"title": "No credential provisioned",
			"status": 424,
			"error_origin": "broker"
		}`))
	}

	cases := []struct {
		name    string
		target  string
		flags   []string // appended after the broker flags
		inspect http.HandlerFunc
		broker  http.HandlerFunc
		// fixedBroker overrides the mock's address with a static broker target
		// (the SEC-1 transport case must not depend on an ephemeral port).
		fixedBroker []string
	}{
		{
			name:    "execute_ok_json",
			target:  "listPets", // opaque id → resolved via /inspect
			inspect: inspectOK,
			broker:  broker200,
		},
		{
			name:    "execute_ok_raw",
			target:  "listPets",
			flags:   []string{"--raw"},
			inspect: inspectOK,
			broker:  broker200,
		},
		{
			name:   "execute_upstream_4xx_passthrough_json",
			target: "GET:/v1/pets", // broker-relative → no inspect round-trip
			broker: brokerUpstream403,
		},
		{
			name:   "execute_broker_denial_directive_json",
			target: "GET:/v1/pets",
			broker: brokerDenial403Directive,
		},
		{
			name:   "execute_broker_denial_no_directive_json",
			target: "GET:/v1/pets",
			broker: brokerDenial424NoDirective,
		},
		{
			name:   "execute_transport_insecure_broker",
			target: "GET:/v1/pets",
			// Plaintext http to a non-loopback broker → SEC-1 refusal before
			// any dial (deterministic TRANSPORT_ERROR, exit 1).
			fixedBroker: []string{"--broker-scheme", "http", "--broker-host", "203.0.113.9:8100"},
		},
		{
			name:    "execute_resolve_not_found_json",
			target:  "nonexistentOp",
			inspect: inspectNotFound,
		},
		{
			name:    "execute_resolve_backend_error_json",
			target:  "listPets",
			inspect: inspectBackendError,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/inspect" {
					if tc.inspect == nil {
						t.Errorf("unexpected /inspect call in case %s", tc.name)
						w.WriteHeader(http.StatusNotFound)
						return
					}
					tc.inspect(w, r)
					return
				}
				if tc.broker == nil {
					t.Errorf("unexpected broker call in case %s", tc.name)
					w.WriteHeader(http.StatusNotFound)
					return
				}
				tc.broker(w, r)
			}))
			t.Cleanup(srv.Close)

			seedSession(t, srv.URL)
			args := []string{"execute", tc.target}
			if tc.fixedBroker != nil {
				args = append(args, tc.fixedBroker...)
			} else {
				args = append(args, "--broker-scheme", "http", "--broker-host", srv.Listener.Addr().String())
			}
			args = append(args, tc.flags...)

			got := runAPI(t, t.TempDir(), agentEnv, args...)
			assertGolden(t, tc.name, got)
		})
	}
}
