package cmd

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/authclient"
	"github.com/jentic/jentic-one/cli/internal/catalogclient"
	"github.com/jentic/jentic-one/cli/internal/profile"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// seedRegistered writes a registered profile with a cached, non-expired token
// pointed at baseURL, so catalogSession resolves a token without any network.
func seedRegistered(t *testing.T, app *App, name, baseURL string) {
	t.Helper()
	p, err := profile.Open(app.Paths, name)
	if err != nil {
		t.Fatalf("open profile: %v", err)
	}
	if err := p.SaveMeta(&profile.Meta{AgentID: "agnt_test", BaseURL: baseURL, KID: "k"}); err != nil {
		t.Fatalf("save meta: %v", err)
	}
	if err := p.SaveTokens(&profile.Tokens{AccessToken: "tok_abc", AccessExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatalf("save tokens: %v", err)
	}
}

func TestCatalogListRendersAndStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{
			"data":[
				{"api_id":"stripe.com","vendor":"stripe.com","registered":true,"_links":{}},
				{"api_id":"slack.com","vendor":"slack.com","registered":false,"_links":{}}],
			"catalog_total":2,"registered_count":1,"manifest_age_seconds":120,
			"has_more":false,"next_cursor":""}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	ident := &identityOptions{baseURL: srv.URL}
	if err := app.catalogList(context.Background(), ident, &catalogListOptions{limit: 50}, ""); err != nil {
		t.Fatalf("catalogList: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"stripe.com", "slack.com", theme.SelectOn, theme.SelectOff, "2 entries · 1 imported"} {
		if !strings.Contains(got, want) {
			t.Errorf("list output missing %q\n---\n%s", want, got)
		}
	}
}

func TestCatalogSearchPassesQuery(t *testing.T) {
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.Query().Get("q")
		_, _ = w.Write([]byte(`{"data":[],"catalog_total":0,"registered_count":0,"has_more":false}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	ident := &identityOptions{baseURL: srv.URL}
	if err := app.catalogList(context.Background(), ident, &catalogListOptions{limit: 50}, "payments"); err != nil {
		t.Fatalf("catalogList: %v", err)
	}
	if gotQuery != "payments" {
		t.Errorf("server saw q=%q, want payments", gotQuery)
	}
}

func TestCatalogShowPreview(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/operations"):
			_, _ = w.Write([]byte(`{"data":[{"method":"GET","path":"/v1/charges","summary":"List charges"}],
				"total":1,"offset":0,"truncated":false,"info":{"title":"Stripe","version":"2024"}}`))
		default:
			_, _ = w.Write([]byte(`{"api_id":"stripe.com","vendor":"stripe.com","spec_url":"https://spec","registered":false,"_links":{}}`))
		}
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	ident := &identityOptions{baseURL: srv.URL}
	if err := app.catalogShow(context.Background(), ident, &catalogShowOptions{}, "stripe.com"); err != nil {
		t.Fatalf("catalogShow: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"stripe.com", "Stripe", "/v1/charges", "List charges"} {
		if !strings.Contains(got, want) {
			t.Errorf("show output missing %q\n---\n%s", want, got)
		}
	}
}

func TestCatalogImportAutoPromotes(t *testing.T) {
	var promoted string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, ":import"):
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"job_id":"job_1"}`))
		case strings.HasSuffix(r.URL.Path, "/result"):
			_, _ = w.Write([]byte(`{"revisions":[{"api":{"vendor":"stripe.com","name":"main","version":"2024"},"revision_id":"rev_1","state":"draft"}]}`))
		case strings.HasPrefix(r.URL.Path, "/jobs/"):
			_, _ = w.Write([]byte(`{"job_id":"job_1","status":"completed"}`))
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, ":promote"):
			promoted = r.URL.Path
			_, _ = w.Write([]byte(`{}`))
		default:
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
		}
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	ident := &identityOptions{baseURL: srv.URL}
	opts := &catalogImportOptions{timeout: 5 * time.Second}
	if err := app.catalogImport(context.Background(), ident, opts, "stripe.com"); err != nil {
		t.Fatalf("catalogImport: %v", err)
	}
	if promoted != "/apis/stripe.com/main/2024/revisions/rev_1:promote" {
		t.Errorf("promote not called correctly, got %q", promoted)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"Imported 1 revision", "stripe.com/main/2024", "live"} {
		if !strings.Contains(got, want) {
			t.Errorf("import output missing %q\n---\n%s", want, got)
		}
	}
}

func TestCatalogImportDeadLetterFailsFast(t *testing.T) {
	// A dead-lettered job is terminal: the poller must stop immediately and
	// return an error, not spin until the --timeout (the re-import "infinite
	// loop" symptom). We give a generous timeout but the job is dead_letter from
	// the first poll, so the call must return well before it.
	var jobPolls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, ":import"):
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"job_id":"job_1"}`))
		case strings.HasPrefix(r.URL.Path, "/jobs/"):
			jobPolls++
			_, _ = w.Write([]byte(`{"job_id":"job_1","status":"dead_letter","error":"all import source(s) failed"}`))
		default:
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
		}
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	ident := &identityOptions{baseURL: srv.URL}
	opts := &catalogImportOptions{timeout: 30 * time.Second}
	start := time.Now()
	err := app.catalogImport(context.Background(), ident, opts, "stripe.com")
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("expected an error for a dead-lettered import job")
	}
	if !strings.Contains(err.Error(), "dead_letter") {
		t.Errorf("error should name the dead_letter status, got: %v", err)
	}
	// Must fail fast — the first poll is terminal, so it returns in well under
	// the 30s timeout (and never reaches the timeout branch).
	if elapsed > 5*time.Second {
		t.Errorf("dead_letter should stop immediately, took %s", elapsed)
	}
	if jobPolls == 0 {
		t.Error("expected at least one job poll")
	}
}

func TestCatalogImportNoPromoteLeavesDraft(t *testing.T) {
	var promoteCalled bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, ":import"):
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"job_id":"job_1"}`))
		case strings.HasSuffix(r.URL.Path, "/result"):
			_, _ = w.Write([]byte(`{"revisions":[{"api":{"vendor":"v","name":"n","version":"1"},"revision_id":"rev_1","state":"draft"}]}`))
		case strings.HasPrefix(r.URL.Path, "/jobs/"):
			_, _ = w.Write([]byte(`{"status":"completed"}`))
		case strings.HasSuffix(r.URL.Path, ":promote"):
			promoteCalled = true
			_, _ = w.Write([]byte(`{}`))
		}
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	ident := &identityOptions{baseURL: srv.URL}
	opts := &catalogImportOptions{timeout: 5 * time.Second, noPromote: true}
	if err := app.catalogImport(context.Background(), ident, opts, "v/n"); err != nil {
		t.Fatalf("catalogImport: %v", err)
	}
	if promoteCalled {
		t.Error("promote should not be called with --no-promote")
	}
	if got := app.Out.(*bytes.Buffer).String(); !strings.Contains(got, "not promoted") {
		t.Errorf("expected draft note, got:\n%s", got)
	}
}

func TestCatalogNotRegisteredErrors(t *testing.T) {
	app := testApp(t)
	// No profile seeded → no agent id.
	ident := &identityOptions{baseURL: "http://127.0.0.1:1"}
	err := app.catalogList(context.Background(), ident, &catalogListOptions{}, "")
	if err == nil || !strings.Contains(err.Error(), "jentic register") {
		t.Fatalf("expected register hint, got %v", err)
	}
}

func TestCatalogListNotAvailable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"not found"}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	ident := &identityOptions{baseURL: srv.URL}
	err := app.catalogList(context.Background(), ident, &catalogListOptions{}, "")
	if err == nil || !strings.Contains(err.Error(), "not available") {
		t.Fatalf("expected not-available error, got %v", err)
	}
}

func TestCatalogShowEntryNotFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"no such entry"}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)

	ident := &identityOptions{baseURL: srv.URL}
	err := app.catalogShow(context.Background(), ident, &catalogShowOptions{}, "ghost.com")
	if err == nil || !strings.Contains(err.Error(), "not found") {
		t.Fatalf("expected not-found error, got %v", err)
	}
}

func TestCatalogAuthErrIsActionable(t *testing.T) {
	// A pending/invalid-assertion mint failure must point at register, not leak
	// the raw "Assertion is invalid" detail on its own.
	pending := agentAuthErr(&authclient.PendingError{Detail: "Assertion is invalid"}, "default")
	if !strings.Contains(pending.Error(), "jentic register") {
		t.Errorf("pending error not actionable: %v", pending)
	}
	if !strings.Contains(pending.Error(), "Assertion is invalid") {
		t.Errorf("pending error dropped server detail: %v", pending)
	}

	generic := agentAuthErr(errors.New("boom"), "work")
	if !strings.Contains(generic.Error(), "jentic register --profile work") {
		t.Errorf("generic error not actionable: %v", generic)
	}
}

// A registered profile whose mint is rejected by the server must degrade to the
// friendly register hint rather than surfacing the raw assertion error.
func TestCatalogSessionRejectedMintDegrades(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/oauth/token") {
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"detail":"Assertion is invalid"}`))
			return
		}
		t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
	}))
	defer srv.Close()

	app := testApp(t)
	p, err := profile.Open(app.Paths, "default")
	if err != nil {
		t.Fatalf("open profile: %v", err)
	}
	// Registered agent but no cached token → ValidToken must mint and fail.
	if err := p.SaveMeta(&profile.Meta{AgentID: "agnt_test", BaseURL: srv.URL, KID: "k"}); err != nil {
		t.Fatalf("save meta: %v", err)
	}

	ident := &identityOptions{baseURL: srv.URL}
	err = app.catalogList(context.Background(), ident, &catalogListOptions{}, "")
	if err == nil || !strings.Contains(err.Error(), "jentic register") {
		t.Fatalf("expected register hint, got %v", err)
	}
}

// ── pure browser-helper tests (no TTY loop) ──────────────────────────────────

func TestCatalogFilterCycle(t *testing.T) {
	if filterAll.next() != filterRegistered ||
		filterRegistered.next() != filterUnregistered ||
		filterUnregistered.next() != filterAll {
		t.Errorf("filter cycle is wrong")
	}
}

func TestCatalogBrowserListRow(t *testing.T) {
	m := &catalogBrowser{
		entries: []catalogclient.Entry{
			{APIID: "stripe.com", Registered: true},
			{APIID: "slack.com", Registered: false},
		},
		cursor: 0,
	}
	if row := m.listRow(0); !strings.Contains(row, theme.SelectOn) || !strings.Contains(row, "stripe.com") {
		t.Errorf("registered row = %q", row)
	}
	if row := m.listRow(1); !strings.Contains(row, theme.SelectOff) || !strings.Contains(row, "slack.com") {
		t.Errorf("unregistered row = %q", row)
	}
}

func TestCatalogBrowserHeaderStatus(t *testing.T) {
	age := 120
	m := &catalogBrowser{total: 10, registered: 3, ageSeconds: &age}
	got := m.headerStatus()
	for _, want := range []string{"10 entries", "3 imported", "2m old"} {
		if !strings.Contains(got, want) {
			t.Errorf("header status %q missing %q", got, want)
		}
	}
}

func TestCatalogBrowserRefreshSuccess(t *testing.T) {
	m := &catalogBrowser{refreshing: true}
	m.onRefresh(catRefreshMsg{count: 42})
	if m.refreshing {
		t.Error("refreshing flag not cleared")
	}
	if !m.loading {
		t.Error("expected list reload after refresh")
	}
	if !strings.Contains(m.status, "42") {
		t.Errorf("status missing count: %q", m.status)
	}
}

func TestCatalogBrowserRefreshForbidden(t *testing.T) {
	m := &catalogBrowser{refreshing: true}
	m.onRefresh(catRefreshMsg{err: &catalogclient.HTTPError{StatusCode: 403, Body: "{}"}})
	if m.refreshing {
		t.Error("refreshing flag not cleared")
	}
	if !strings.Contains(m.status, "org:admin") {
		t.Errorf("status should hint org:admin, got %q", m.status)
	}
}

func TestCatalogBrowserBackPeelsLevels(t *testing.T) {
	m := &catalogBrowser{
		entries:    []catalogclient.Entry{{APIID: "acme.com"}},
		previews:   map[string]*catalogclient.Preview{"acme.com": {}},
		previewErr: map[string]string{},
		query:      "pay",
		filter:     filterRegistered,
	}

	// 1st back: collapse the open preview.
	if _, cmd := m.back(); cmd != nil {
		t.Error("collapsing preview should not issue a command")
	}
	if _, shown := m.previews["acme.com"]; shown {
		t.Error("preview should be collapsed")
	}
	if m.done {
		t.Error("should not quit while a preview was open")
	}

	// 2nd back: clear the search query.
	m.back()
	if m.query != "" {
		t.Errorf("query should be cleared, got %q", m.query)
	}
	if m.done {
		t.Error("should not quit while a filter is active")
	}

	// 3rd back: reset the filter to all.
	m.back()
	if m.filter != filterAll {
		t.Errorf("filter should reset to all, got %v", m.filter)
	}
	if m.done {
		t.Error("should not quit until at base level")
	}

	// 4th back: now at base level → quit.
	if _, cmd := m.back(); cmd == nil {
		t.Error("expected quit command at base level")
	}
	if !m.done {
		t.Error("should be done at base level")
	}
}

func TestWrapLines(t *testing.T) {
	lines := wrapLines("the quick brown fox jumps", 9, 3)
	if len(lines) == 0 || len(lines) > 3 {
		t.Fatalf("unexpected line count: %v", lines)
	}
	for _, ln := range lines {
		if len([]rune(ln)) > 9 {
			t.Errorf("line exceeds width: %q", ln)
		}
	}

	// Overflow past maxLines gets an ellipsis on the final kept line.
	capped := wrapLines("alpha beta gamma delta epsilon zeta eta theta", 6, 2)
	if len(capped) != 2 {
		t.Fatalf("expected 2 lines, got %v", capped)
	}
	if !strings.HasSuffix(capped[1], "…") {
		t.Errorf("expected ellipsis on overflow, got %q", capped[1])
	}

	if wrapLines("anything", 0, 3) != nil {
		t.Error("zero width should yield nil")
	}
}

func TestTruncate(t *testing.T) {
	if got := truncate("hello world", 5); got != "hell…" {
		t.Errorf("truncate = %q", got)
	}
	if got := truncate("short", 10); got != "short" {
		t.Errorf("truncate should pass short strings, got %q", got)
	}
}
