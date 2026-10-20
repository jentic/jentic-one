package api

// mcp_catalog_test.go exercises the catalog tool handlers (search_catalog /
// import_api) against an httptest control plane, following the per-tool
// patterns of the 1-B/1-C suites: envelope passthrough with the sibling
// instance stamp, alias tolerance, and the coded soft-error mappings with
// their recovery pointers.

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
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
			"data": [{"entry_id":"public/googleapis.com/sheets","source_slug":"public","catalog_api_id":"googleapis.com/sheets","vendor":"googleapis.com","registered":false,"update_available":false,"_links":{"self":"/catalog/public/googleapis.com/sheets","operations":"/catalog/public/googleapis.com/sheets/operations","import":"/catalog/public/googleapis.com/sheets:import"}}],
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
	if entry["entry_id"] != "public/googleapis.com/sheets" || entry["registered"] != false {
		t.Errorf("entry = %v, want the catalog projection (entry_id, registered)", entry)
	}
	// The schema_version 2 entry: the HTTP mount's keys, and none of the
	// retired ones (nor the CLI-only tags/human_url).
	for _, key := range []string{
		"entry_id", "source_slug", "catalog_api_id", "vendor", "display_vendor", "title",
		"version", "has_spec", "registered", "update_available", "alternates", "_links",
	} {
		if _, ok := entry[key]; !ok {
			t.Errorf("entry is missing %q: %v", key, entry)
		}
	}
	for _, key := range []string{"api_id", "spec_url", "path", "github_url", "tags", "human_url"} {
		if _, ok := entry[key]; ok {
			t.Errorf("entry carries the retired key %q: %v", key, entry)
		}
	}
	links, _ := entry["_links"].(map[string]any)
	if _, ok := links["github"]; ok {
		t.Errorf("_links carries the retired github key: %v", links)
	}
	// Nothing collapsed and no human page: both links are omitted.
	for _, key := range []string{"alternates", "human"} {
		if _, ok := links[key]; ok {
			t.Errorf("_links.%s = %v, want it omitted", key, links[key])
		}
	}
	if _, ok := payload["oldest_source_sync_age_seconds"]; !ok {
		t.Errorf("payload is missing oldest_source_sync_age_seconds: %v", payload)
	}
	if _, ok := payload["manifest_age_seconds"]; ok {
		t.Errorf("payload carries the retired manifest_age_seconds: %v", payload)
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
	// `id` alias for entry_id; the entry_id carries literal "/"s.
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"id":"public/googleapis.com/sheets"}`))
	if err != nil {
		t.Fatalf("handleImportAPI: %v", err)
	}
	if res.IsError {
		t.Fatalf("unexpected soft error: %s", toolResultText(res))
	}

	// The rawPathEditor hazard: the entry_id must reach the backend with its
	// literal slashes, not %2F (the Starlette {entry_id:path} route).
	if plane.importPath != "/catalog/public/googleapis.com/sheets:import" {
		t.Errorf("import path = %q, want the literal-slash {entry_id:path} form", plane.importPath)
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
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"entry_id":"public/googleapis.com/sheets"}`))
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
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"entry_id":"public/googleapis.com/sheets"}`))
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

// requireImportInvalidParams asserts a JSON-RPC invalid_params protocol error
// (no tool result) and returns its message.
func requireImportInvalidParams(t *testing.T, res *mcp.CallToolResult, err error) string {
	t.Helper()
	if res != nil {
		t.Fatalf("an unknown entry must be a protocol error, got a result: %s", toolResultText(res))
	}
	var rpcErr *jsonrpc.Error
	if !errors.As(err, &rpcErr) || rpcErr.Code != jsonrpc.CodeInvalidParams {
		t.Fatalf("want invalid_params, got %v", err)
	}
	return rpcErr.Message
}

func TestMCPImportAPI_404IsInvalidParamsPointingAtSearchCatalog(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"unknown api"}`))
	}))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"entry_id":"public/nope/nothing"}`))
	msg := requireImportInvalidParams(t, res, err)
	// Byte-identical to the HTTP mount's text for the same miss.
	want := "catalog entry 'public/nope/nothing' not found; call search_catalog with a keyword " +
		"for the API you need and pass the entry_id of one of its hits verbatim"
	if msg != want {
		t.Errorf("message = %q, want %q", msg, want)
	}
}

func TestMCPImportAPI_403IsOperatorPermissionGrant(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"detail":"requires one of: catalog:import"}`))
	}))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"entry_id":"public/googleapis.com/sheets"}`))
	if err != nil {
		t.Fatalf("handleImportAPI: %v", err)
	}
	if !res.IsError {
		t.Fatalf("want IsError result")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeBrokerDenied {
		t.Errorf("error_code = %v, want %q (a missing permission is an access gap, not a revoked identity)", payload["error_code"], ux.CodeBrokerDenied)
	}
	if _, has := payload["next_tool"]; has {
		t.Errorf("next_tool = %v, want none (the permission grant is an operator action, not a tool call)", payload["next_tool"])
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "catalog:import") || !strings.Contains(step, "operator") {
		t.Errorf("actionable_step %q must name the catalog:import permission and route to the operator", step)
	}
}

func TestMCPImportAPI_MissingEntryIDIsInvalidParams(t *testing.T) {
	s := fastAccessServer(t)
	res, err := s.handleImportAPI(activeCtx("http://127.0.0.1:0"), callToolRequest("import_api", `{}`))
	if res != nil {
		t.Fatalf("want a protocol error, got a result: %v", res)
	}
	for _, spelling := range []string{"entry_id", "api_id", "id", "api"} {
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
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"entry_id":"public/googleapis.com/sheets"}`))
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

// TestMCPImportAPI_TraversalEntryIDIsInvalidParams pins the syntactic guard in
// front of the raw-path {entry_id:path} route: traversal-shaped ids must never
// reach the wire (rawPathEditor splices the value into the path VERBATIM).
func TestMCPImportAPI_TraversalEntryIDIsInvalidParams(t *testing.T) {
	s := fastAccessServer(t)
	for _, bad := range []string{
		"../access-requests",
		"/catalog/x",
		"a//b",
		"a/./b",
		"public/googleapis.com/sheets/..",
		"public/googleapis.com/",
		"public/a\x00b",
		"public/a\x7fb",
		"public/" + strings.Repeat("x", maxEntryIDLength),
		"p/" + strings.Repeat("é", maxEntryIDLength-1),
	} {
		args, _ := json.Marshal(map[string]string{"entry_id": bad})
		res, err := s.handleImportAPI(activeCtx("http://127.0.0.1:0"),
			callToolRequest("import_api", string(args)))
		if res != nil {
			t.Fatalf("entry_id %q: want a protocol error before the wire, got a result: %v", bad, res)
		}
		if err == nil || !strings.Contains(err.Error(), "search_catalog") {
			t.Errorf("entry_id %q: err %v must point back at search_catalog", bad, err)
		}
	}
	// The legitimate shapes (literal slashes, a namespaced id) stay accepted.
	// The server's rule, not a stricter one: lengths are code points, so 320
	// multi-byte characters still fit; C1 characters (U+0080–U+009F) pass.
	for _, good := range []string{
		"public/googleapis.com/sheets", "internal/aws-prod~stripe.com", "stripe.com",
		"p/" + strings.Repeat("é", maxEntryIDLength-2), "public/a\u0085b",
	} {
		if err := checkEntryID(good); err != nil {
			t.Errorf("checkEntryID(%q) must accept it: %v", good, err)
		}
	}
}

// TestMCPSearchCatalog_403IsOperatorPermissionGrant mirrors the import
// mapping: a 403 on GET /catalog is the missing capabilities:read permission —
// an access gap the operator closes with a dashboard grant — not a revoked
// identity.
func TestMCPSearchCatalog_403IsOperatorPermissionGrant(t *testing.T) {
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
		t.Errorf("next_tool = %v, want none (the permission grant is an operator action, not a tool call)", payload["next_tool"])
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "capabilities:read") || !strings.Contains(step, "operator") {
		t.Errorf("actionable_step %q must name the capabilities:read permission and route to the operator", step)
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

// TestMCPImportAPI_AcceptsEveryAlias pins the entry_id parameter and its
// aliases: each spelling files the same import on the {entry_id:path} route.
func TestMCPImportAPI_AcceptsEveryAlias(t *testing.T) {
	for _, spelling := range []string{"entry_id", "api_id", "id", "api"} {
		t.Run(spelling, func(t *testing.T) {
			plane := &importControlPlane{jobStatus: []string{"completed"}}
			srv := httptest.NewServer(plane.handler(t))
			defer srv.Close()

			s := fastAccessServer(t)
			args := fmt.Sprintf(`{%q:"public/googleapis.com/sheets"}`, spelling)
			res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", args))
			if err != nil {
				t.Fatalf("handleImportAPI: %v", err)
			}
			if res.IsError {
				t.Fatalf("unexpected soft error: %s", toolResultText(res))
			}
			if plane.importPath != "/catalog/public/googleapis.com/sheets:import" {
				t.Errorf("import path = %q, want the entry_id route", plane.importPath)
			}
		})
	}
}

// TestMCPImportAPI_404DidYouMeanNamesTheVisibleEntry pins the old-style id
// recovery: the server's 404 body names the caller's visible entry_id, and the
// tool error repeats it verbatim (no CLI-built prefix) with a retry hint.
func TestMCPImportAPI_404DidYouMeanNamesTheVisibleEntry(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/problem+json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"type":"about:blank","title":"Not Found","status":404,` +
			`"detail":"catalog entry not found","did_you_mean":"internal/stripe.com"}`))
	}))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"api_id":"stripe.com"}`))
	msg := requireImportInvalidParams(t, res, err)
	want := "catalog entry 'stripe.com' not found — did you mean 'internal/stripe.com'?; call search_catalog " +
		"with a keyword for the API you need and pass the entry_id of one of its hits verbatim"
	if msg != want {
		t.Errorf("message = %q, want %q", msg, want)
	}
}

// TestMCPImportAPI_404DropsAMalformedDidYouMean: a suggestion that couldn't
// name an entry is never relayed into a retry; the miss points at search.
func TestMCPImportAPI_404DropsAMalformedDidYouMean(t *testing.T) {
	for _, bad := range []string{"../access-requests", "public//x", "public/a\nb", "  "} {
		body, _ := json.Marshal(map[string]any{"status": 404, "detail": "catalog entry not found", "did_you_mean": bad})
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/problem+json")
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write(body)
		}))
		s := fastAccessServer(t)
		res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", `{"entry_id":"stripe.com"}`))
		srv.Close()
		if msg := requireImportInvalidParams(t, res, err); strings.Contains(msg, "did you mean") {
			t.Errorf("%q: a malformed suggestion was relayed: %q", bad, msg)
		}
	}
}

func TestPyRepr(t *testing.T) {
	for in, want := range map[string]string{
		"public/stripe.com": "'public/stripe.com'",
		"o'brien.com":       `"o'brien.com"`,
		`a'b"c`:             `'a\'b"c'`,
		`a\b`:               `'a\\b'`,
	} {
		if got := pyRepr(in); got != want {
			t.Errorf("pyRepr(%q) = %q, want %q", in, got, want)
		}
	}
}

// TestMCPImportAPI_CatalogSourceErrorsMatchSharedFixture replays the shared
// 410/409/503 fixture against the stdio server. tests/unit/mcp/test_tools_import_api.py
// replays the same file against the HTTP mount, so the two cannot drift.
func TestMCPImportAPI_CatalogSourceErrorsMatchSharedFixture(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "golden", "testdata", "mcp", "import_api_catalog_errors.json"))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	var cases []struct {
		Name       string         `json:"name"`
		EntryID    string         `json:"entry_id"`
		Status     int            `json:"status"`
		Type       string         `json:"type"`
		Detail     string         `json:"detail"`
		RetryAfter *int           `json:"retry_after"`
		Payload    map[string]any `json:"payload"`
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatalf("decode fixture: %v", err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				if tc.RetryAfter != nil {
					w.Header().Set("Retry-After", strconv.Itoa(*tc.RetryAfter))
				}
				w.Header().Set("Content-Type", "application/problem+json")
				w.WriteHeader(tc.Status)
				body, _ := json.Marshal(map[string]any{
					"type": tc.Type, "title": http.StatusText(tc.Status), "status": tc.Status,
					"detail": tc.Detail,
				})
				_, _ = w.Write(body)
			}))
			defer srv.Close()

			s := fastAccessServer(t)
			args, _ := json.Marshal(map[string]string{"entry_id": tc.EntryID})
			res, err := s.handleImportAPI(activeCtx(srv.URL), callToolRequest("import_api", string(args)))
			if err != nil {
				t.Fatalf("handleImportAPI: %v", err)
			}
			if !res.IsError {
				t.Fatalf("want IsError result")
			}
			payload := decodeToolJSON(t, res)
			delete(payload, "schema_version")
			delete(payload, "instance")
			// JSON numbers decode as float64 on both sides; compare as decoded JSON.
			want, _ := json.Marshal(tc.Payload)
			got, _ := json.Marshal(payload)
			var wantV, gotV any
			_ = json.Unmarshal(want, &wantV)
			_ = json.Unmarshal(got, &gotV)
			if !reflect.DeepEqual(gotV, wantV) {
				t.Errorf("payload mismatch\n got: %s\nwant: %s", got, want)
			}
		})
	}
}

func TestRetryAfterSeconds(t *testing.T) {
	for raw, want := range map[string]int{"": 5, "x": 5, "-1": 5, "0": 1, "0.2": 1, "12": 12, "12.1": 13} {
		if got := retryAfterSeconds(raw); got != want {
			t.Errorf("retryAfterSeconds(%q) = %d, want %d", raw, got, want)
		}
	}
}
