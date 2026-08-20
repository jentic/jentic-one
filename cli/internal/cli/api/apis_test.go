package api

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// keyMsg builds a tea.KeyMsg for the given rune string (test helper).
func keyMsg(s string) tea.KeyMsg {
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)}
}

func TestApisListRenders(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":[
			{"api":{"vendor":"stripe.com","name":"api","version":"v1"},"current_revision_id":"r1","operation_count":12},
			{"api":{"vendor":"slack.com","name":"web","version":"v2"},"operation_count":3}],
			"has_more":false,"next_cursor":""}`))
	}))
	defer srv.Close()

	app := testApp(t)
	if err := app.apisList(activeCtx(srv.URL), &apisListOptions{limit: 50}); err != nil {
		t.Fatalf("apisList: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"stripe.com/api/v1", "slack.com/web/v2", "12 ops", "2 API(s)"} {
		if !strings.Contains(got, want) {
			t.Errorf("list output missing %q\n---\n%s", want, got)
		}
	}
}

func TestApisListSendsVendor(t *testing.T) {
	var gotVendor string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotVendor = r.URL.Query().Get("vendor")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":[],"has_more":false}`))
	}))
	defer srv.Close()

	app := testApp(t)
	if err := app.apisList(activeCtx(srv.URL), &apisListOptions{vendor: "stripe.com", limit: 50}); err != nil {
		t.Fatalf("apisList: %v", err)
	}
	if gotVendor != "stripe.com" {
		t.Errorf("server saw vendor=%q", gotVendor)
	}
}

func TestApisShowRendersDetailAndOps(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(r.URL.Path, "/operations") {
			_, _ = w.Write([]byte(`{"data":[{"method":"GET","path":"/v1/charges","name":"List charges"}],"has_more":false}`))
			return
		}
		_, _ = w.Write([]byte(`{"api":{"vendor":"stripe.com","name":"api","version":"v1","host":"api.stripe.com"},
			"display_name":"Stripe","description":"Payments API","current_revision_id":"r1",
			"revision_count":2,"operation_count":1,"security_schemes":["bearer"]}`))
	}))
	defer srv.Close()

	app := testApp(t)
	if err := app.apisShow(activeCtx(srv.URL), &apisShowOptions{}, "stripe.com/api/v1"); err != nil {
		t.Fatalf("apisShow: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"stripe.com/api/v1", "Stripe", "Payments API", "bearer", "GET", "/v1/charges", "List charges"} {
		if !strings.Contains(got, want) {
			t.Errorf("show output missing %q\n---\n%s", want, got)
		}
	}
}

func TestApisRevisionsRenders(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"data":[
			{"revision_id":"r2","state":"draft","operation_count":4},
			{"revision_id":"r1","state":"published","is_current":true,"operation_count":4}],
			"has_more":false}`))
	}))
	defer srv.Close()

	app := testApp(t)
	if err := app.apisRevisions(activeCtx(srv.URL), &apisRevisionsOptions{limit: 50}, "stripe.com/api/v1"); err != nil {
		t.Fatalf("apisRevisions: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"r2", "draft", "r1", "published", "(current)"} {
		if !strings.Contains(got, want) {
			t.Errorf("revisions output missing %q\n---\n%s", want, got)
		}
	}
}

func TestApisPromoteHitsEndpoint(t *testing.T) {
	var method, path string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		method, path = r.Method, r.URL.Path
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	app := testApp(t)
	if err := app.apisLifecycle(activeCtx(srv.URL), "stripe.com/api/v1", "r2", lifecyclePromote); err != nil {
		t.Fatalf("promote: %v", err)
	}
	if method != http.MethodPost || !strings.HasSuffix(path, "/revisions/r2:promote") {
		t.Errorf("promote hit %s %s", method, path)
	}
	if !strings.Contains(app.Out.(*bytes.Buffer).String(), "Promoted") {
		t.Errorf("missing success message")
	}
}

func TestApisRemoveWithYesDeletesAPI(t *testing.T) {
	var method, path string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		method, path = r.Method, r.URL.Path
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	app := testApp(t)
	if err := app.apisRemove(activeCtx(srv.URL), &apisRmOptions{yes: true}, "stripe.com/api/v1", ""); err != nil {
		t.Fatalf("rm: %v", err)
	}
	if method != http.MethodDelete || !strings.HasSuffix(path, "/apis/stripe.com/api/v1") {
		t.Errorf("delete hit %s %s", method, path)
	}
	if !strings.Contains(app.Out.(*bytes.Buffer).String(), "Deleted API") {
		t.Errorf("missing delete message")
	}
}

func TestApisShowNotFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"not found"}`))
	}))
	defer srv.Close()

	app := testApp(t)
	err := app.apisShow(activeCtx(srv.URL), &apisShowOptions{}, "ghost.com/x/v1")
	if err == nil || !strings.Contains(err.Error(), "not found in the local registry") {
		t.Errorf("want friendly not-found, got %v", err)
	}
}

func TestParseAPIRef(t *testing.T) {
	v, n, ver, err := parseAPIRef("stripe.com/api/v1")
	if err != nil || v != "stripe.com" || n != "api" || ver != "v1" {
		t.Errorf("parse = %q/%q/%q err=%v", v, n, ver, err)
	}
	for _, bad := range []string{"", "stripe.com", "stripe.com/api", "stripe.com/api/v1/extra", "/api/v1"} {
		if _, _, _, err := parseAPIRef(bad); err == nil {
			t.Errorf("expected error for %q", bad)
		}
	}
}

// ── pure browser-helper tests ────────────────────────────────────────────────

func TestApisBrowserOpenRevisionsSwitchesView(t *testing.T) {
	m := &apisBrowser{
		apis:   []registeredAPI{{API: apiRef{Vendor: "v", Name: "n", Version: "1"}}},
		ops:    map[string]*operationListResult{},
		opsErr: map[string]string{},
	}
	if _, cmd := m.openRevisions(); cmd == nil {
		t.Error("expected a load command")
	}
	if m.view != viewRevisions {
		t.Error("view should switch to revisions")
	}
	if m.revAPI.Name != "n" {
		t.Errorf("revAPI not set: %+v", m.revAPI)
	}
}

func TestApisBrowserBackPeelsLevels(t *testing.T) {
	ref := apiRef{Vendor: "v", Name: "n", Version: "1"}
	m := &apisBrowser{
		apis:   []registeredAPI{{API: ref}},
		ops:    map[string]*operationListResult{apiRefLabel(ref): {}},
		opsErr: map[string]string{},
		vendor: "v",
	}
	// 1st back: collapse the ops preview.
	if _, cmd := m.back(); cmd != nil {
		t.Error("collapsing preview should not issue a command")
	}
	if _, shown := m.ops[apiRefLabel(ref)]; shown {
		t.Error("preview should be collapsed")
	}
	if m.done {
		t.Error("should not quit while preview was open")
	}
	// 2nd back: clear vendor filter.
	m.back()
	if m.vendor != "" {
		t.Errorf("vendor should be cleared, got %q", m.vendor)
	}
	// 3rd back: now at base → quit.
	if _, cmd := m.back(); cmd == nil {
		t.Error("expected quit command at base level")
	}
	if !m.done {
		t.Error("should be done at base level")
	}
}

func TestApisBrowserActOnRevisionGuardsState(t *testing.T) {
	m := &apisBrowser{
		view:   viewRevisions,
		revAPI: apiRef{Vendor: "v", Name: "n", Version: "1"},
		revs:   []apiRevision{{RevisionID: "r1", State: "published", IsCurrent: true}},
	}
	// Promoting a non-draft must be a no-op with a status hint, not a command.
	if _, cmd := m.actOnRevision("promote"); cmd != nil {
		t.Error("promote of published revision should not issue a command")
	}
	if !strings.Contains(m.status, "draft") {
		t.Errorf("status should explain draft-only, got %q", m.status)
	}
	// Deleting a non-archived must not enter confirm.
	m.actOnRevision("delete")
	if m.confirm != confirmNone {
		t.Error("delete of non-archived revision should not prompt confirm")
	}
}

func TestApisBrowserDeleteRevisionConfirmFlow(t *testing.T) {
	m := &apisBrowser{
		view:   viewRevisions,
		revAPI: apiRef{Vendor: "v", Name: "n", Version: "1"},
		revs:   []apiRevision{{RevisionID: "r9", State: "archived"}},
	}
	m.actOnRevision("delete")
	if m.confirm != confirmDeleteRevision {
		t.Fatalf("expected confirm prompt, got %v", m.confirm)
	}
	// Cancelling with any non-y key clears the prompt.
	m.onConfirmKey(keyMsg("n"))
	if m.confirm != confirmNone {
		t.Error("confirm should clear on cancel")
	}
}

func TestApisBrowserActionForbiddenHint(t *testing.T) {
	m := &apisBrowser{revAPI: apiRef{Vendor: "v", Name: "n", Version: "1"}}
	m.onAction(apisActionMsg{verb: "promoted", err: &HTTPError{StatusCode: 403, Body: "{}"}})
	if !strings.Contains(m.status, "not permitted") {
		t.Errorf("status should hint permission, got %q", m.status)
	}
}
