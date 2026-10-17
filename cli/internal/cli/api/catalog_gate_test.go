package api

// catalog_gate_test.go pins the server-version gate in front of the catalog:
// a release CLI refuses a server that predates entry_id addressing with a
// coded upgrade hint, both on the cobra commands and the stdio MCP tools.

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// enableCatalogGate turns the gate on for the test (it is off for source
// builds) and drops any cached pass from an earlier case.
func enableCatalogGate(t *testing.T) {
	t.Helper()
	orig := catalogGateEnabled
	catalogGateEnabled = func() bool { return true }
	catalogServerOK.Clear()
	t.Cleanup(func() {
		catalogGateEnabled = orig
		catalogServerOK.Clear()
	})
}

// gatedCatalogServer answers /health with the given body (status 200) and a
// one-entry catalog page; it counts /health and /catalog hits.
func gatedCatalogServer(t *testing.T, healthStatus int, healthBody string) (*httptest.Server, *atomic.Int32, *atomic.Int32) {
	t.Helper()
	var health, catalog atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/health":
			health.Add(1)
			w.WriteHeader(healthStatus)
			_, _ = w.Write([]byte(healthBody))
		case "/catalog":
			catalog.Add(1)
			_, _ = w.Write([]byte(`{"data":[{"entry_id":"public/stripe.com","source_slug":"public","catalog_api_id":"stripe.com","vendor":"stripe.com","registered":false,"_links":{}}],"catalog_total":1,"registered_count":0,"has_more":false}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, &health, &catalog
}

func TestCatalogGate_RefusesAnOldServer(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		status     int
		wantMsg    string
	}{
		{"older release", `{"status":"ok","version":"0.43.0"}`, http.StatusOK, "reports v0.43.0"},
		{"no version", `{"status":"ok"}`, http.StatusOK, "reports no version"},
		{"health unavailable", `{}`, http.StatusServiceUnavailable, "HTTP 503"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			enableCatalogGate(t)
			srv, _, catalog := gatedCatalogServer(t, tc.status, tc.body)

			app := testApp(t)
			err := app.catalogList(activeCtx(srv.URL), &catalogListOptions{limit: 10, json: true}, "")
			var coded *ux.CodedError
			if !errors.As(err, &coded) || coded.Code != ux.CodeServerIncompatible {
				t.Fatalf("want a SERVER_INCOMPATIBLE coded error, got %v", err)
			}
			if !strings.Contains(coded.Msg, tc.wantMsg) || !strings.Contains(coded.Msg, "v"+minCatalogServerVersion) {
				t.Errorf("message %q must name the reported (%q) and minimum versions", coded.Msg, tc.wantMsg)
			}
			if coded.Actionable != serverIncompatibleRemedy {
				t.Errorf("actionable %q must be the shared release-match remedy", coded.Actionable)
			}
			if coded.Details["min_server_version"] != minCatalogServerVersion {
				t.Errorf("details = %v, want min_server_version", coded.Details)
			}
			if catalog.Load() != 0 {
				t.Errorf("an old server must be refused before any catalog call, saw %d", catalog.Load())
			}
		})
	}
}

func TestCatalogGate_AdmitsACurrentServerAndCachesThePass(t *testing.T) {
	for _, version := range []string{minCatalogServerVersion, "0.44.0rc1", "0.45.2", "1.0.0"} {
		t.Run(version, func(t *testing.T) {
			enableCatalogGate(t)
			srv, health, catalog := gatedCatalogServer(t, http.StatusOK, `{"status":"ok","version":"`+version+`"}`)

			app := testApp(t)
			for range 2 {
				if err := app.catalogList(activeCtx(srv.URL), &catalogListOptions{limit: 10, json: true}, ""); err != nil {
					t.Fatalf("catalogList: %v", err)
				}
			}
			if health.Load() != 1 {
				t.Errorf("/health probed %d times, want once per instance", health.Load())
			}
			if catalog.Load() != 2 {
				t.Errorf("/catalog called %d times, want 2", catalog.Load())
			}
		})
	}
}

func TestCatalogGate_DisabledForSourceBuilds(t *testing.T) {
	srv, health, _ := gatedCatalogServer(t, http.StatusOK, `{"status":"ok","version":"0.1.0"}`)
	app := testApp(t)
	if err := app.catalogList(activeCtx(srv.URL), &catalogListOptions{limit: 10, json: true}, ""); err != nil {
		t.Fatalf("a dev build must not gate: %v", err)
	}
	if health.Load() != 0 {
		t.Errorf("a dev build probed /health %d times", health.Load())
	}
}

func TestCatalogGate_MCPToolIsSoftErrorWithUpgradeHint(t *testing.T) {
	enableCatalogGate(t)
	srv, _, catalog := gatedCatalogServer(t, http.StatusOK, `{"status":"ok","version":"0.43.0"}`)

	s := fastAccessServer(t)
	res, err := s.handleSearchCatalog(activeCtx(srv.URL), callToolRequest("search_catalog", `{"query":"stripe"}`))
	if err != nil {
		t.Fatalf("an old server is a deployment fact, not a protocol error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result, got %s", toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeServerIncompatible {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeServerIncompatible)
	}
	if step, _ := payload["actionable_step"].(string); step != serverIncompatibleRemedy {
		t.Errorf("actionable_step %q must be the shared release-match remedy", step)
	}
	if catalog.Load() != 0 {
		t.Errorf("an old server must be refused before any catalog call")
	}
}
