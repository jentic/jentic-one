package api

// mcp_catalog_test.go exercises the catalog tool handlers (search_catalog /
// import_api) against an httptest control plane, following the per-tool
// patterns of the 1-B/1-C suites: envelope passthrough with the sibling
// instance stamp, alias tolerance, and the coded soft-error mappings with
// their recovery pointers.

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// fastAccessServer is stampedTestMCPServer with the poll cadence and the
// catalog wait budgets shrunk so pending-path cases are near-instant.
func fastAccessServer(t *testing.T) *mcpServer {
	t.Helper()
	s := stampedTestMCPServer(t)
	s.app.SetPollCadence(time.Millisecond, 2*time.Millisecond, time.Millisecond)
	s.importWaitBudget = 100 * time.Millisecond
	return s
}

// --- search_catalog -----------------------------------------------------------

func TestMCPSearchCatalog_EnvelopePassthroughWithStamp(t *testing.T) {
	var gotQuery, gotCursor, gotLimit string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/catalog" || r.Method != http.MethodGet {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		gotQuery = r.URL.Query().Get("q")
		gotCursor = r.URL.Query().Get("cursor")
		gotLimit = r.URL.Query().Get("limit")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"data": [{"api_id":"googleapis.com/sheets","vendor":"googleapis.com","path":"sheets","spec_url":"https://x/spec.json","registered":false,"update_available":false,"_links":{"self":"/catalog/googleapis.com/sheets","operations":"/catalog/googleapis.com/sheets/operations","import":"/catalog/googleapis.com/sheets:import"}}],
			"catalog_total": 1200,
			"registered_count": 3,
			"has_more": true,
			"next_cursor": "page2"
		}`))
	}))
	defer srv.Close()

	s := fastAccessServer(t)
	// `q` alias and a numeric-string limit must normalize before the wire.
	res, err := s.handleSearchCatalog(activeCtx(srv.URL), callToolRequest("search_catalog", `{"q":"sheets","limit":"25","cursor":"c0"}`))
	if err != nil {
		t.Fatalf("handleSearchCatalog: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %s", toolResultText(res))
	}
	if gotQuery != "sheets" || gotCursor != "c0" || gotLimit != "25" {
		t.Errorf("wire params = q %q cursor %q limit %q, want the normalized arguments", gotQuery, gotCursor, gotLimit)
	}

	payload := decodeToolJSON(t, res)
	if payload["schema_version"] != mcpSchemaVersion {
		t.Errorf("schema_version = %v, want %q", payload["schema_version"], mcpSchemaVersion)
	}
	data, ok := payload["data"].([]any)
	if !ok || len(data) != 1 {
		t.Fatalf("data = %v, want one entry", payload["data"])
	}
	entry := data[0].(map[string]any)
	if entry["api_id"] != "googleapis.com/sheets" || entry["registered"] != false {
		t.Errorf("entry = %v, want the catalog projection (api_id, registered)", entry)
	}
	if payload["catalog_total"] != float64(1200) || payload["registered_count"] != float64(3) {
		t.Errorf("counters = %v/%v, want the CLI envelope extras mirrored", payload["catalog_total"], payload["registered_count"])
	}
	if payload["has_more"] != true || payload["next_cursor"] != "page2" {
		t.Errorf("pagination = %v/%v, want passthrough true/page2", payload["has_more"], payload["next_cursor"])
	}
	if stamp, ok := payload["instance"].(map[string]any); !ok || stamp["backend"] != "local" {
		t.Errorf("instance stamp = %v, want the fresh identity", payload["instance"])
	}
}

func TestMCPSearchCatalog_EmptyResultsEmitEmptyArray(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":[],"catalog_total":0,"registered_count":0,"has_more":false}`))
	}))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleSearchCatalog(activeCtx(srv.URL), callToolRequest("search_catalog", `{"query":"nothing"}`))
	if err != nil {
		t.Fatalf("handleSearchCatalog: %v", err)
	}
	text := res.Content[0].(*mcp.TextContent).Text
	if strings.Contains(text, `"data":null`) {
		t.Fatalf("data serialized as null, want []: %s", text)
	}
	payload := decodeToolJSON(t, res)
	if data, ok := payload["data"].([]any); !ok || len(data) != 0 {
		t.Errorf("data = %v, want an empty array", payload["data"])
	}
	if payload["has_more"] != false {
		t.Errorf("has_more = %v, want false", payload["has_more"])
	}
	if cursor, present := payload["next_cursor"]; present {
		t.Errorf("next_cursor = %v, want the empty cursor omitted (CLI envelope shape)", cursor)
	}
}

func TestMCPSearchCatalog_LimitOutOfRangeIsInvalidParams(t *testing.T) {
	s := fastAccessServer(t)
	res, err := s.handleSearchCatalog(activeCtx("http://127.0.0.1:0"), callToolRequest("search_catalog", `{"limit":500}`))
	if res != nil {
		t.Fatalf("want a protocol error, got a result: %v", res)
	}
	if err == nil || !strings.Contains(err.Error(), "between 1 and 200") {
		t.Fatalf("err = %v, want an invalid-params error naming the 1-200 bound", err)
	}
}

func TestMCPSearchCatalog_CatalogUnavailableIsSoftError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"no catalog here"}`))
	}))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleSearchCatalog(activeCtx(srv.URL), callToolRequest("search_catalog", `{"query":"x"}`))
	if err != nil {
		t.Fatalf("a deployment fact must be a soft error, not a protocol error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeInternalError {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeInternalError)
	}
	if msg, _ := payload["error"].(string); !strings.Contains(msg, "catalog not available") {
		t.Errorf("error = %q, want the catalogListErr wording", msg)
	}
}

// --- import_api ---------------------------------------------------------------

// importControlPlane fakes the whole import loop: POST :import → 202 job,
// GET /jobs/{id} answering per the script, GET /jobs/{id}/result, and the
// promote route. It records the paths it saw for wire assertions.
type importControlPlane struct {
	mu         sync.Mutex
	jobStatus  []string // successive GET /jobs answers; last repeats
	jobIdx     int
	paths      []string
	promoted   int
	importPath string
}

func (p *importControlPlane) handler(t *testing.T) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		p.mu.Lock()
		p.paths = append(p.paths, r.Method+" "+r.URL.Path)
		p.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, ":import"):
			p.mu.Lock()
			p.importPath = r.URL.Path
			p.mu.Unlock()
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"job_id":"job_9","status":"queued","_links":{"job":"/jobs/job_9"}}`))
		case r.Method == http.MethodGet && r.URL.Path == "/jobs/job_9":
			p.mu.Lock()
			status := p.jobStatus[p.jobIdx]
			if p.jobIdx < len(p.jobStatus)-1 {
				p.jobIdx++
			}
			p.mu.Unlock()
			_, _ = fmt.Fprintf(w, `{"job_id":"job_9","kind":"catalog_import","status":%q}`, status)
		case r.Method == http.MethodGet && r.URL.Path == "/jobs/job_9/result":
			_, _ = w.Write([]byte(`{"revisions":[{"api":{"vendor":"googleapis.com","name":"sheets","version":"v4"},"revision_id":"rev_1","state":"draft"}]}`))
		case r.Method == http.MethodPost && strings.Contains(r.URL.Path, ":promote"):
			p.mu.Lock()
			p.promoted++
			p.mu.Unlock()
			_, _ = w.Write([]byte(`{}`))
		default:
			t.Errorf("unexpected control-plane call: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}
}

func TestMCPImportAPI_CompletesAndPromotes(t *testing.T) {
	plane := &importControlPlane{jobStatus: []string{"running", "completed"}}
	srv := httptest.NewServer(plane.handler(t))
	defer srv.Close()

	s := fastAccessServer(t)
	// `id` alias for api_id; the umbrella api_id carries a literal "/".
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"id":"googleapis.com/sheets"}`))
	if err != nil {
		t.Fatalf("handleImportAPI: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %s", toolResultText(res))
	}

	// The rawPathEditor hazard: the umbrella api_id must reach the backend
	// with its literal slash, not %2F (the Starlette {api_id:path} route).
	if plane.importPath != "/catalog/googleapis.com/sheets:import" {
		t.Errorf("import path = %q, want the literal-slash {api_id:path} form", plane.importPath)
	}

	payload := decodeToolJSON(t, res)
	if payload["schema_version"] != mcpSchemaVersion || payload["job_id"] != "job_9" || payload["status"] != catJobCompleted {
		t.Errorf("envelope = %v, want schema_version/job_id/status mirrored", payload)
	}
	revs, ok := payload["revisions"].([]any)
	if !ok || len(revs) != 1 {
		t.Fatalf("revisions = %v, want the import job result passed through", payload["revisions"])
	}
	promoted, ok := payload["promoted"].(map[string]any)
	if !ok || promoted["rev_1"] != "live" {
		t.Errorf("promoted = %v, want the draft revision auto-promoted live (CLI default)", payload["promoted"])
	}
	if plane.promoted != 1 {
		t.Errorf("promote calls = %d, want 1", plane.promoted)
	}
	if stamp, ok := payload["instance"].(map[string]any); !ok || stamp["backend"] != "local" {
		t.Errorf("instance stamp = %v, want the fresh identity", payload["instance"])
	}
}

func TestMCPImportAPI_StillRunningReturnsJobForConvergence(t *testing.T) {
	plane := &importControlPlane{jobStatus: []string{"running"}}
	srv := httptest.NewServer(plane.handler(t))
	defer srv.Close()

	s := fastAccessServer(t)
	s.importWaitBudget = 5 * time.Millisecond
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"api_id":"googleapis.com/sheets"}`))
	if err != nil {
		t.Fatalf("handleImportAPI: %v", err)
	}
	if res.IsError {
		t.Fatalf("a slow import is not an error — the model converges by re-calling: %s", toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	if payload["job_id"] != "job_9" || payload["status"] != "running" {
		t.Errorf("payload = %v, want the in-flight job id + status", payload)
	}
	if _, ok := payload["promoted"]; ok {
		t.Errorf("nothing completed, nothing may claim promotion: %v", payload)
	}
}

func TestMCPImportAPI_FailedJobIsSoftError(t *testing.T) {
	plane := &importControlPlane{jobStatus: []string{"failed"}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && r.URL.Path == "/jobs/job_9" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"job_id":"job_9","kind":"catalog_import","status":"failed","error":"spec fetch failed"}`))
			return
		}
		plane.handler(t)(w, r)
	}))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"api_id":"googleapis.com/sheets"}`))
	if err != nil {
		t.Fatalf("handleImportAPI: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result for a failed import")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeInternalError {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeInternalError)
	}
	if msg, _ := payload["error"].(string); !strings.Contains(msg, "spec fetch failed") {
		t.Errorf("error %q must carry the job's failure detail", msg)
	}
	if payload["job_status"] != "failed" {
		t.Errorf("job_status = %v, want failed", payload["job_status"])
	}
}

func TestMCPImportAPI_404IsResolveFailedPointingAtSearchCatalog(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"unknown api"}`))
	}))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"api_id":"nope/nothing"}`))
	if err != nil {
		t.Fatalf("a resolve failure must be a soft error: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeResolveFailed {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeResolveFailed)
	}
	if payload["next_tool"] != "search_catalog" {
		t.Errorf("next_tool = %v, want search_catalog (the id is wrong, not the identity)", payload["next_tool"])
	}
}

func TestMCPImportAPI_403IsOperatorScopeGrant(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"detail":"requires one of: catalog:import"}`))
	}))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"api_id":"googleapis.com/sheets"}`))
	if err != nil {
		t.Fatalf("handleImportAPI: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeBrokerDenied {
		t.Errorf("error_code = %v, want %q (a missing scope is an access gap, not a revoked identity)", payload["error_code"], ux.CodeBrokerDenied)
	}
	if _, has := payload["next_tool"]; has {
		t.Errorf("next_tool = %v, want none (the scope grant is an operator action, not a tool call)", payload["next_tool"])
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "catalog:import") || !strings.Contains(step, "operator") {
		t.Errorf("actionable_step %q must name the catalog:import scope and route to the operator", step)
	}
}

func TestMCPImportAPI_MissingAPIIDIsInvalidParams(t *testing.T) {
	s := fastAccessServer(t)
	res, err := s.handleImportAPI(activeCtx("http://127.0.0.1:0"), callToolRequest("import_api", `{}`))
	if res != nil {
		t.Fatalf("want a protocol error, got a result: %v", res)
	}
	for _, spelling := range []string{"api_id", "id", "api"} {
		if err == nil || !strings.Contains(err.Error(), spelling) {
			t.Errorf("err %v must name the accepted spelling %q", err, spelling)
		}
	}
}

// --- review-wave regressions ----------------------------------------------------

// TestMCPImportAPI_JobPollFailureIsSoftErrorNotSuccess pins the MAJOR review
// finding: a failing GET /jobs/{id} leaves the job's state UNKNOWN — reporting
// it as a clean non-terminal success would send the model into a re-import
// loop (the docstring says re-call on a non-terminal status, and the backend
// does not de-duplicate in-flight imports).
func TestMCPImportAPI_JobPollFailureIsSoftErrorNotSuccess(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, ":import"):
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"job_id":"job_9","status":"queued","_links":{"job":"/jobs/job_9"}}`))
		case r.Method == http.MethodGet && r.URL.Path == "/jobs/job_9":
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = w.Write([]byte(`{"detail":"job store down"}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"api_id":"googleapis.com/sheets"}`))
	if err != nil {
		t.Fatalf("handleImportAPI: %v", err)
	}
	if !res.IsError {
		t.Fatalf("a job-poll failure must be an isError result, never the still-running success shape: %s", toolResultText(res))
	}
	payload := decodeToolJSON(t, res)
	if code, _ := payload["error_code"].(string); code == "" {
		t.Errorf("payload %v must carry a coded error", payload)
	}
	if payload["job_id"] != "job_9" {
		t.Errorf("job_id = %v, want job_9 carried in extras so the model can keep watching this job", payload["job_id"])
	}
}

// TestMCPImportAPI_TraversalAPIIDIsInvalidParams pins the syntactic guard in
// front of the raw-path {api_id:path} route: traversal-shaped ids must never
// reach the wire (rawPathEditor splices the value into the path VERBATIM).
func TestMCPImportAPI_TraversalAPIIDIsInvalidParams(t *testing.T) {
	s := fastAccessServer(t)
	for _, bad := range []string{
		"../access-requests",
		"/catalog/x",
		"a//b",
		"a/./b",
		"googleapis.com/sheets/..",
		"googleapis.com/",
	} {
		res, err := s.handleImportAPI(activeCtx("http://127.0.0.1:0"),
			callToolRequest("import_api", fmt.Sprintf(`{"api_id":%q}`, bad)))
		if res != nil {
			t.Fatalf("api_id %q: want a protocol error before the wire, got a result: %v", bad, res)
		}
		if err == nil || !strings.Contains(err.Error(), "search_catalog") {
			t.Errorf("api_id %q: err %v must point back at search_catalog", bad, err)
		}
	}
	// The legitimate umbrella shape (literal slash) must stay accepted.
	if err := validateAPIID("googleapis.com/sheets"); err != nil {
		t.Errorf("validateAPIID must accept a real umbrella api_id: %v", err)
	}
}

// TestMCPSearchCatalog_403IsOperatorScopeGrant mirrors the import
// mapping: a 403 on GET /catalog is the missing capabilities:read scope — an
// access gap the operator closes with a dashboard grant — not a revoked
// identity.
func TestMCPSearchCatalog_403IsOperatorScopeGrant(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"detail":"requires one of: capabilities:read"}`))
	}))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleSearchCatalog(activeCtx(srv.URL), callToolRequest("search_catalog", `{"query":"x"}`))
	if err != nil {
		t.Fatalf("handleSearchCatalog: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeBrokerDenied {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeBrokerDenied)
	}
	if _, has := payload["next_tool"]; has {
		t.Errorf("next_tool = %v, want none (the scope grant is an operator action, not a tool call)", payload["next_tool"])
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "capabilities:read") || !strings.Contains(step, "operator") {
		t.Errorf("actionable_step %q must name the capabilities:read scope and route to the operator", step)
	}
}

// TestMCPSearchCatalog_TransportFailureIsRetryable pins the shared
// transportSoftError contract on the access loop: a dead control plane comes
// back as a retryable TRANSPORT_ERROR with the get_started pointer, never as
// INTERNAL_ERROR with no recovery.
func TestMCPSearchCatalog_TransportFailureIsRetryable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	srv.Close() // the port is now provably closed: dial fails pre-send

	s := fastAccessServer(t)
	res, err := s.handleSearchCatalog(activeCtx(srv.URL), callToolRequest("search_catalog", `{"query":"x"}`))
	if err != nil {
		t.Fatalf("handleSearchCatalog: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeTransportError {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeTransportError)
	}
	if payload["retryable"] != true {
		t.Errorf("retryable = %v, want true (access-loop calls are reads or server-converging writes)", payload["retryable"])
	}
	if payload["next_tool"] != "get_started" {
		t.Errorf("next_tool = %v, want get_started", payload["next_tool"])
	}
}
