package api

// catalog_gate_test.go pins the server-version gate in front of the catalog:
// a release CLI refuses a server that predates entry_id addressing with a
// coded upgrade hint, both on the cobra commands and the stdio MCP tools.

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"runtime/debug"
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

const (
	currentCatalogPage = `{"data":[{"entry_id":"public/stripe.com","source_slug":"public","catalog_api_id":"stripe.com","vendor":"stripe.com","registered":false,"_links":{}}],"catalog_total":1,"registered_count":0,"has_more":false}`
	// legacyCatalogPage is what a pre-entry_id server answers: api_id entries.
	legacyCatalogPage = `{"data":[{"api_id":"stripe.com","vendor":"stripe.com","spec_url":"https://example.com/s.json","registered":false,"_links":{}}],"catalog_total":1,"registered_count":0,"has_more":false}`
)

// gatedCatalogServer answers /health with the given status and body and
// /catalog with a one-entry entry_id page; it counts /health and /catalog hits.
func gatedCatalogServer(t *testing.T, healthStatus int, healthBody string) (*httptest.Server, *atomic.Int32, *atomic.Int32) {
	t.Helper()
	return gatedCatalogServerWith(t, healthStatus, healthBody, currentCatalogPage)
}

func gatedCatalogServerWith(t *testing.T, healthStatus int, healthBody, catalogBody string) (*httptest.Server, *atomic.Int32, *atomic.Int32) {
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
			_, _ = w.Write([]byte(catalogBody))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, &health, &catalog
}

func requireServerIncompatible(t *testing.T, err error, wantMsg string) *ux.CodedError {
	t.Helper()
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeServerIncompatible {
		t.Fatalf("want a SERVER_INCOMPATIBLE coded error, got %v", err)
	}
	if !strings.Contains(coded.Msg, wantMsg) || !strings.Contains(coded.Msg, "v"+minCatalogServerVersion) {
		t.Errorf("message %q must name %q and the minimum version", coded.Msg, wantMsg)
	}
	if coded.Actionable != serverIncompatibleRemedy {
		t.Errorf("actionable %q must be the shared release-match remedy", coded.Actionable)
	}
	if coded.Details["min_server_version"] != minCatalogServerVersion {
		t.Errorf("details = %v, want min_server_version", coded.Details)
	}
	return coded
}

func TestCatalogGate_RefusesAServerReportingAnOlderRelease(t *testing.T) {
	for _, version := range []string{"0.43.0", "0.43.9rc1", "v0.9.0"} {
		t.Run(version, func(t *testing.T) {
			enableCatalogGate(t)
			srv, _, catalog := gatedCatalogServer(t, http.StatusOK, `{"status":"ok","version":"`+version+`"}`)

			err := testApp(t).catalogList(activeCtx(srv.URL), &catalogListOptions{limit: 10, json: true}, "")
			requireServerIncompatible(t, err, "reports v"+strings.TrimPrefix(version, "v"))
			if catalog.Load() != 0 {
				t.Errorf("an old server must be refused before any catalog call, saw %d", catalog.Load())
			}
		})
	}
}

func TestCatalogGate_UnavailableHealthIsRetryableTransport(t *testing.T) {
	for _, status := range []int{http.StatusServiceUnavailable, http.StatusBadGateway, http.StatusTooManyRequests} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			enableCatalogGate(t)
			srv, _, catalog := gatedCatalogServer(t, status, `{}`)

			err := testApp(t).catalogList(activeCtx(srv.URL), &catalogListOptions{limit: 10, json: true}, "")
			var coded *ux.CodedError
			if !errors.As(err, &coded) || coded.Code != ux.CodeTransportError {
				t.Fatalf("want a TRANSPORT_ERROR coded error, got %v", err)
			}
			if coded.Details["retryable"] != true || coded.Details["http_status"] != status {
				t.Errorf("details = %v, want retryable with http_status %d", coded.Details, status)
			}
			if catalog.Load() != 0 {
				t.Errorf("no catalog call while the server is unavailable, saw %d", catalog.Load())
			}
		})
	}
}

// When /health doesn't say what the server runs (no version, an unparseable
// one, or a proxy answering 401/404), the gate falls back to the catalog
// response: entries without entry_id mean a pre-entry_id server.
func TestCatalogGate_UnknownVersionFallsBackToTheResponseShape(t *testing.T) {
	cases := []struct {
		name, body string
		status     int
	}{
		{"no version", `{"status":"ok"}`, http.StatusOK},
		{"unparseable version", `{"status":"ok","version":"main"}`, http.StatusOK},
		{"proxy 401", `{}`, http.StatusUnauthorized},
		{"proxy 404", `{}`, http.StatusNotFound},
	}
	for _, tc := range cases {
		t.Run(tc.name+"/legacy shape refused", func(t *testing.T) {
			enableCatalogGate(t)
			srv, _, catalog := gatedCatalogServerWith(t, tc.status, tc.body, legacyCatalogPage)

			err := testApp(t).catalogList(activeCtx(srv.URL), &catalogListOptions{limit: 10, json: true}, "")
			coded := requireServerIncompatible(t, err, "without an entry_id")
			if coded.Details["server_version"] != "" {
				t.Errorf("server_version = %v, want empty (the server didn't report one)", coded.Details["server_version"])
			}
			if catalog.Load() != 1 {
				t.Errorf("the shape check reads the catalog once, saw %d", catalog.Load())
			}
		})
		t.Run(tc.name+"/entry_id shape admitted and cached", func(t *testing.T) {
			enableCatalogGate(t)
			srv, health, _ := gatedCatalogServer(t, tc.status, tc.body)

			app := testApp(t)
			for range 2 {
				if err := app.catalogList(activeCtx(srv.URL), &catalogListOptions{limit: 10, json: true}, ""); err != nil {
					t.Fatalf("catalogList: %v", err)
				}
			}
			if health.Load() != 1 {
				t.Errorf("/health probed %d times; an entry_id page proves the server current", health.Load())
			}
		})
	}
}

func TestCatalogGate_AdmitsACurrentServerAndCachesThePass(t *testing.T) {
	for _, version := range []string{minCatalogServerVersion, "0.44.0rc1", "0.44.0-rc.1", "v0.44.0", "0.45.2", "1.0.0"} {
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

func TestCLIReleaseVersion_FallsBackToModuleBuildInfo(t *testing.T) {
	orig := readBuildInfo
	t.Cleanup(func() { readBuildInfo = orig })
	for _, tc := range []struct{ module, want string }{
		{"v0.44.0", "v0.44.0"},
		{"v0.44.0-rc.1", "v0.44.0-rc.1"},
		{"(devel)", ""},
		{"", ""},
		{"v0.0.0-20261017120000-abcdef123456", ""},
	} {
		readBuildInfo = func() (*debug.BuildInfo, bool) {
			return &debug.BuildInfo{Main: debug.Module{Version: tc.module}}, true
		}
		if got := cliReleaseVersion(); got != tc.want {
			t.Errorf("module version %q: cliReleaseVersion() = %q, want %q", tc.module, got, tc.want)
		}
	}
	readBuildInfo = func() (*debug.BuildInfo, bool) { return nil, false }
	if got := cliReleaseVersion(); got != "" {
		t.Errorf("no build info: cliReleaseVersion() = %q, want a source build", got)
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
