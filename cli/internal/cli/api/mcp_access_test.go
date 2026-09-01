package api

// mcp_access_test.go exercises the 2-E1 access-loop tool handlers against an
// httptest control plane, following the per-tool patterns of the 1-B/1-C
// suites: envelope passthrough with the sibling instance stamp, alias
// tolerance, the coded soft-error mappings with their recovery pointers, and
// — for request_access — the never-self-approves invariant at the wire level.

import (
	"encoding/json"
	"fmt"
	"io"
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
// access-loop wait budgets shrunk so pending-path cases are near-instant.
func fastAccessServer(t *testing.T) *mcpServer {
	t.Helper()
	s := stampedTestMCPServer(t)
	s.app.SetPollCadence(time.Millisecond, 2*time.Millisecond, time.Millisecond)
	s.importWaitBudget = 100 * time.Millisecond
	s.accessPollBudget = 20 * time.Millisecond
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
		t.Fatalf("unexpected soft error: %v", res.Content)
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
		t.Fatalf("unexpected soft error: %v", res.Content)
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
		t.Fatalf("a slow import is not an error — the model converges by re-calling: %v", res.Content)
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

func TestMCPImportAPI_403PointsAtRequestAccessForScope(t *testing.T) {
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
	if payload["next_tool"] != "request_access" {
		t.Errorf("next_tool = %v, want request_access", payload["next_tool"])
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "catalog:import") {
		t.Errorf("actionable_step %q must name the catalog:import scope (skill wording)", step)
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

// --- request_access -----------------------------------------------------------

// accessRequestJSON renders a minimal-but-valid AccessRequestResponse body.
// approve_url is deliberately RELATIVE: the handler must absolutize it onto
// the environment's base URL (jentic-one#777) before the model sees it.
func accessRequestJSON(id, status, itemsJSON string) string {
	return fmt.Sprintf(`{
		"id": %q, "status": %q, "actor_id": "agent_1", "created_by": "agent_1", "requested_by": "agent_1",
		"approve_url": "/console/access-requests/%s",
		"filed_at": "2026-08-31T12:00:00Z", "expires_at": "2026-09-07T12:00:00Z",
		"items": %s
	}`, id, status, id, itemsJSON)
}

// accessControlPlane fakes POST /access-requests + GET /access-requests/{id},
// recording every request it sees (the never-self-approves assertion reads
// the log). fileStatus/pollStatus script the lifecycle; fileCode 409 turns
// the filing into a duplicate-pending collision.
type accessControlPlane struct {
	mu         sync.Mutex
	seen       []string
	fileBody   []byte
	fileCode   int
	fileStatus string
	pollStatus string
	itemsJSON  string
}

func (p *accessControlPlane) handler(t *testing.T) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		p.mu.Lock()
		p.seen = append(p.seen, r.Method+" "+r.URL.Path)
		p.mu.Unlock()
		items := p.itemsJSON
		if items == "" {
			items = "[]"
		}
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/access-requests":
			body, _ := io.ReadAll(r.Body)
			p.mu.Lock()
			p.fileBody = body
			p.mu.Unlock()
			if p.fileCode == http.StatusConflict {
				w.Header().Set("Content-Type", "application/problem+json")
				w.WriteHeader(http.StatusConflict)
				_, _ = w.Write([]byte(`{"title":"duplicate","detail":"a pending request exists","status":409,` +
					`"existing_request_id":"acr_old","approve_url":"/console/access-requests/acr_old"}`))
				return
			}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(accessRequestJSON("acr_1", p.fileStatus, items)))
		case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/access-requests/"):
			id := strings.TrimPrefix(r.URL.Path, "/access-requests/")
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(accessRequestJSON(id, p.pollStatus, items)))
		default:
			t.Errorf("unexpected control-plane call: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}
}

func TestMCPRequestAccess_FilesComposedPlanPendingWithApproveURL(t *testing.T) {
	plane := &accessControlPlane{fileStatus: statusPending, pollStatus: statusPending}
	srv := httptest.NewServer(plane.handler(t))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleRequestAccess(activeCtx(srv.URL), callToolRequest("request_access", `{
		"provision": ["stripe.com/api"],
		"auth": ["bearer"],
		"rules_json": [{"effect":"allow","methods":["GET"],"path":".*"}],
		"toolkits": ["github.com/api"],
		"scopes": ["catalog:import"],
		"reason": "read invoices for the summary task"
	}`))
	if err != nil {
		t.Fatalf("handleRequestAccess: %v", err)
	}
	if res.IsError {
		t.Fatalf("a pending filing is a normal result, not an error: %v", res.Content)
	}

	// The wire body: compose()'s exact plan — the 4-item provisioning chain
	// first, then the toolkit bind, then the scope grant — plus the reason.
	var wire struct {
		Reason string `json:"reason"`
		Items  []struct {
			ResourceType      string          `json:"resource_type"`
			Action            string          `json:"action"`
			ResourceReference map[string]any  `json:"resource_reference"`
			ResourceID        *string         `json:"resource_id"`
			Rules             json.RawMessage `json:"rules"`
		} `json:"items"`
	}
	if err := json.Unmarshal(plane.fileBody, &wire); err != nil {
		t.Fatalf("decode filed body: %v\n%s", err, plane.fileBody)
	}
	if wire.Reason != "read invoices for the summary task" {
		t.Errorf("reason on the wire = %q, want the argument mirrored", wire.Reason)
	}
	if len(wire.Items) != 6 {
		t.Fatalf("items = %d, want the 4-item provision chain + toolkit bind + scope grant", len(wire.Items))
	}
	wantKinds := []string{"toolkit/create", "credential/provision", "credential/bind", "toolkit/bind", "toolkit/bind", "scope/grant"}
	for i, want := range wantKinds {
		if got := wire.Items[i].ResourceType + "/" + wire.Items[i].Action; got != want {
			t.Errorf("item %d = %s, want %s (compose() fulfilment order)", i, got, want)
		}
	}
	if ref := wire.Items[1].ResourceReference; ref["security_scheme"] != "bearer" || ref["vendor"] != "stripe.com" {
		t.Errorf("provision item reference = %v, want the auth type + API stamped on", ref)
	}
	if len(wire.Items[2].Rules) == 0 || !strings.Contains(string(wire.Items[2].Rules), `"methods":["GET"]`) {
		t.Errorf("credential:bind rules = %s, want the proposed rules_json intact (never comma-split)", wire.Items[2].Rules)
	}
	if wire.Items[5].ResourceID == nil || *wire.Items[5].ResourceID != "catalog:import" {
		t.Errorf("scope item = %+v, want resource_id catalog:import", wire.Items[5])
	}

	payload := decodeToolJSON(t, res)
	if payload["schema_version"] != mcpSchemaVersion || payload["id"] != "acr_1" || payload["status"] != statusPending {
		t.Errorf("envelope = %v, want schema_version/id/status mirrored", payload)
	}
	// approve_url absolutized onto the environment base URL, for the HUMAN.
	if got, _ := payload["approve_url"].(string); got != srv.URL+"/console/access-requests/acr_1" {
		t.Errorf("approve_url = %q, want it absolutized onto the base URL", got)
	}
	instruction, _ := payload["instruction"].(string)
	if !strings.Contains(instruction, "never approves") || !strings.Contains(instruction, "request_id") {
		t.Errorf("pending instruction %q must route approval to the human and name the request_id poll", instruction)
	}
	if stamp, ok := payload["instance"].(map[string]any); !ok || stamp["backend"] != "local" {
		t.Errorf("instance stamp = %v, want the fresh identity", payload["instance"])
	}
}

// TestMCPRequestAccess_AutoDenialSurfacesInSameResult pins the status-poll
// half of the tool: a server-side auto-decision landing within the short
// post-file poll reaches the model in the SAME result, as the coded denial.
func TestMCPRequestAccess_AutoDenialSurfacesInSameResult(t *testing.T) {
	plane := &accessControlPlane{
		fileStatus: statusPending,
		pollStatus: statusDenied,
		itemsJSON:  `[{"id":"item_1","resource_type":"toolkit","action":"bind","status":"denied","decision_reason":"No toolkit serves API acme/pets; provision and bind a credential for it first"}]`,
	}
	srv := httptest.NewServer(plane.handler(t))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleRequestAccess(activeCtx(srv.URL), callToolRequest("request_access", `{"toolkits":["acme/pets"],"reason":"r"}`))
	if err != nil {
		t.Fatalf("handleRequestAccess: %v", err)
	}
	if !res.IsError {
		t.Fatalf("a denied request must be an isError result, never look like success")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeBrokerDenied {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeBrokerDenied)
	}
	if step, _ := payload["actionable_step"].(string); !strings.Contains(step, "provision") {
		t.Errorf("actionable_step %q must teach the provision-vs-bind recovery", step)
	}
	request, ok := payload["request"].(map[string]any)
	if !ok {
		t.Fatalf("denial must carry the full request for its decision_reason: %v", payload)
	}
	items, _ := request["items"].([]any)
	if len(items) != 1 || !strings.Contains(fmt.Sprint(items[0]), "No toolkit serves") {
		t.Errorf("request.items = %v, want the decision_reason relayed", request["items"])
	}
}

func TestMCPRequestAccess_DuplicatePendingSingleTargetAttaches(t *testing.T) {
	plane := &accessControlPlane{fileCode: http.StatusConflict, pollStatus: statusPending}
	srv := httptest.NewServer(plane.handler(t))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleRequestAccess(activeCtx(srv.URL), callToolRequest("request_access", `{"toolkits":["acme/pets"]}`))
	if err != nil {
		t.Fatalf("handleRequestAccess: %v", err)
	}
	if res.IsError {
		t.Fatalf("a single-target duplicate attaches, like the CLI: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if payload["id"] != "acr_old" || payload["attached_to_existing"] != true {
		t.Errorf("payload = %v, want the existing request attached and flagged", payload)
	}
}

func TestMCPRequestAccess_DuplicatePendingCompositeIsSoftError(t *testing.T) {
	plane := &accessControlPlane{fileCode: http.StatusConflict}
	srv := httptest.NewServer(plane.handler(t))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleRequestAccess(activeCtx(srv.URL),
		callToolRequest("request_access", `{"toolkits":["acme/pets"],"scopes":["catalog:import"]}`))
	if err != nil {
		t.Fatalf("handleRequestAccess: %v", err)
	}
	if !res.IsError {
		t.Fatalf("a composite collision files NOTHING and must not read as success")
	}
	payload := decodeToolJSON(t, res)
	if payload["error_code"] != ux.CodeResolveFailed {
		t.Errorf("error_code = %v, want %q", payload["error_code"], ux.CodeResolveFailed)
	}
	details, _ := payload["details"].(map[string]any)
	if details["existing_request_id"] != "acr_old" {
		t.Errorf("details = %v, want the colliding request id", payload["details"])
	}
	if msg, _ := payload["error"].(string); !strings.Contains(msg, "nothing was filed") {
		t.Errorf("error %q must state that nothing was filed", msg)
	}
}

func TestMCPRequestAccess_PollArmReportsApproved(t *testing.T) {
	plane := &accessControlPlane{pollStatus: statusApproved}
	srv := httptest.NewServer(plane.handler(t))
	defer srv.Close()

	s := fastAccessServer(t)
	res, err := s.handleRequestAccess(activeCtx(srv.URL), callToolRequest("request_access", `{"request_id":"acr_1"}`))
	if err != nil {
		t.Fatalf("handleRequestAccess: %v", err)
	}
	if res.IsError {
		t.Fatalf("an approved request is a normal result: %v", res.Content)
	}
	payload := decodeToolJSON(t, res)
	if payload["id"] != "acr_1" || payload["status"] != statusApproved {
		t.Errorf("payload = %v, want the approved request passed through", payload)
	}
	if _, hasInstruction := payload["instruction"]; hasInstruction {
		t.Errorf("an approved result must not carry the pending approve instruction")
	}
	// The poll arm is exactly one GET — no filing, no mutation.
	for _, call := range plane.seen {
		if !strings.HasPrefix(call, "GET /access-requests/") {
			t.Errorf("poll arm made a non-GET call: %s", call)
		}
	}
}

func TestMCPRequestAccess_MissingTargetIsInvalidParams(t *testing.T) {
	s := fastAccessServer(t)
	res, err := s.handleRequestAccess(activeCtx("http://127.0.0.1:0"), callToolRequest("request_access", `{}`))
	if res != nil {
		t.Fatalf("want a protocol error, got a result: %v", res)
	}
	for _, name := range []string{"provision", "toolkits", "scopes", "request_id"} {
		if err == nil || !strings.Contains(err.Error(), name) {
			t.Errorf("err %v must name the parameter %q", err, name)
		}
	}
}

func TestMCPRequestAccess_RequestIDPlusTargetsIsInvalidParams(t *testing.T) {
	s := fastAccessServer(t)
	res, err := s.handleRequestAccess(activeCtx("http://127.0.0.1:0"),
		callToolRequest("request_access", `{"request_id":"acr_1","toolkits":["acme/pets"]}`))
	if res != nil {
		t.Fatalf("want a protocol error, got a result: %v", res)
	}
	if err == nil || !strings.Contains(err.Error(), "not both") {
		t.Fatalf("err = %v, want the either-or invalid-params error", err)
	}
}

// TestMCPRequestAccess_NeverSelfApproves pins the §3.2 invariant at the wire
// level: across the filing arm (with its status short-poll) AND the poll arm,
// the tool only ever files (POST /access-requests) and reads
// (GET /access-requests/{id}) — no decide/approve-shaped call exists.
func TestMCPRequestAccess_NeverSelfApproves(t *testing.T) {
	plane := &accessControlPlane{fileStatus: statusPending, pollStatus: statusPending}
	srv := httptest.NewServer(plane.handler(t))
	defer srv.Close()

	s := fastAccessServer(t)
	ctx := activeCtx(srv.URL)
	if res, err := s.handleRequestAccess(ctx, callToolRequest("request_access", `{"toolkits":["acme/pets"],"reason":"r"}`)); err != nil || res.IsError {
		t.Fatalf("filing arm: err %v, res %v", err, res)
	}
	if res, err := s.handleRequestAccess(ctx, callToolRequest("request_access", `{"request_id":"acr_1"}`)); err != nil || res.IsError {
		t.Fatalf("poll arm: err %v, res %v", err, res)
	}
	if len(plane.seen) == 0 {
		t.Fatal("the control plane saw no calls")
	}
	for _, call := range plane.seen {
		if call != "POST /access-requests" && !strings.HasPrefix(call, "GET /access-requests/") {
			t.Errorf("request_access made a call that is neither file nor read: %s", call)
		}
	}
}
