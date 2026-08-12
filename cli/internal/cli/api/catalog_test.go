package api

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/catalogclient"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// TestAgentSessionResolvesFromContext proves the context-only session: the
// base URL and bearer come from the ACTIVE V2 state in the context — there is
// no profile store, no flag override, and no fallback to read from anywhere
// else on disk.
func TestAgentSessionResolvesFromContext(t *testing.T) {
	app := testApp(t)

	baseURL, token, err := app.agentSession(v2Ctx("http://ctrl:9000"))
	if err != nil {
		t.Fatalf("agentSession: %v", err)
	}
	if token != "tok_abc" {
		t.Errorf("token = %q, want the context's injected tok_abc", token)
	}
	if baseURL != "http://ctrl:9000" {
		t.Errorf("base_url = %q, want the context's http://ctrl:9000", baseURL)
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
	if err := app.catalogList(v2Ctx(srv.URL), &catalogListOptions{limit: 50}, ""); err != nil {
		t.Fatalf("catalogList: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"stripe.com", "slack.com", theme.SelectOn, theme.SelectOff, "2 entries · 1 imported"} {
		if !strings.Contains(got, want) {
			t.Errorf("list output missing %q\n---\n%s", want, got)
		}
	}
}

func TestCatalogOutdatedFiltersAndMarks(t *testing.T) {
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		_, _ = w.Write([]byte(`{
			"data":[
				{"api_id":"stripe.com","vendor":"stripe.com","registered":true,"update_available":true,"_links":{}}],
			"catalog_total":2,"registered_count":1,"outdated_count":1,"manifest_age_seconds":120,
			"has_more":false,"next_cursor":""}`))
	}))
	defer srv.Close()

	app := testApp(t)
	if err := app.catalogList(v2Ctx(srv.URL), &catalogListOptions{limit: 50, outdated: true}, ""); err != nil {
		t.Fatalf("catalogList: %v", err)
	}
	if !strings.Contains(gotQuery, "outdated_only=true") {
		t.Errorf("server saw query %q, want outdated_only=true", gotQuery)
	}
	// The snooze filter is opt-in: without --include-snoozed the param must be
	// absent, so snoozed entries stay hidden by default (the user-facing contract).
	if strings.Contains(gotQuery, "include_snoozed") {
		t.Errorf("server saw query %q, include_snoozed must be absent by default", gotQuery)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"stripe.com", "UPDATE AVAILABLE", "1 update(s) available"} {
		if !strings.Contains(got, want) {
			t.Errorf("outdated output missing %q\n---\n%s", want, got)
		}
	}
}

func TestCatalogOutdatedIncludeSnoozedThreadsQuery(t *testing.T) {
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		_, _ = w.Write([]byte(`{
			"data":[],"catalog_total":0,"registered_count":0,"outdated_count":0,
			"manifest_age_seconds":0,"has_more":false,"next_cursor":""}`))
	}))
	defer srv.Close()

	app := testApp(t)
	opts := &catalogListOptions{limit: 50, outdated: true, includeSnoozed: true}
	if err := app.catalogList(v2Ctx(srv.URL), opts, ""); err != nil {
		t.Fatalf("catalogList: %v", err)
	}
	if !strings.Contains(gotQuery, "include_snoozed=true") {
		t.Errorf("server saw query %q, want include_snoozed=true", gotQuery)
	}
	if !strings.Contains(gotQuery, "outdated_only=true") {
		t.Errorf("server saw query %q, want outdated_only=true", gotQuery)
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
	if err := app.catalogList(v2Ctx(srv.URL), &catalogListOptions{limit: 50}, "payments"); err != nil {
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
	if err := app.catalogShow(v2Ctx(srv.URL), &catalogShowOptions{}, "stripe.com"); err != nil {
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
	opts := &catalogImportOptions{timeout: 5 * time.Second}
	if err := app.catalogImport(v2Ctx(srv.URL), opts, "stripe.com"); err != nil {
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
	opts := &catalogImportOptions{timeout: 30 * time.Second}
	start := time.Now()
	err := app.catalogImport(v2Ctx(srv.URL), opts, "stripe.com")
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
	opts := &catalogImportOptions{timeout: 5 * time.Second, noPromote: true}
	if err := app.catalogImport(v2Ctx(srv.URL), opts, "v/n"); err != nil {
		t.Fatalf("catalogImport: %v", err)
	}
	if promoteCalled {
		t.Error("promote should not be called with --no-promote")
	}
	if got := app.Out.(*bytes.Buffer).String(); !strings.Contains(got, "not promoted") {
		t.Errorf("expected draft note, got:\n%s", got)
	}
}

func TestCatalogNoContextErrors(t *testing.T) {
	app := testApp(t)
	// A bare context (no active V2 state) must fail with the canonical
	// no-context resolve error, not attempt any legacy fallback.
	err := app.catalogList(context.Background(), &catalogListOptions{}, "")
	if err == nil || !strings.Contains(err.Error(), "no active context") {
		t.Fatalf("expected no-context error, got %v", err)
	}
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeResolveFailed {
		t.Fatalf("expected RESOLVE_FAILED coded error, got %v", err)
	}
	if !strings.Contains(coded.Actionable, "jentic register") {
		t.Errorf("remediation should name jentic register, got %q", coded.Actionable)
	}
}

func TestCatalogListNotAvailable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"not found"}`))
	}))
	defer srv.Close()

	app := testApp(t)
	err := app.catalogList(v2Ctx(srv.URL), &catalogListOptions{}, "")
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
	err := app.catalogShow(v2Ctx(srv.URL), &catalogShowOptions{}, "ghost.com")
	if err == nil || !strings.Contains(err.Error(), "not found") {
		t.Fatalf("expected not-found error, got %v", err)
	}
}

func TestCatalogAuthErrIsActionable(t *testing.T) {
	st := &clictx.ActiveState{ResolvedState: &sdkconfig.ResolvedState{
		IdentityName:    "bot",
		EnvironmentName: "prod",
	}}

	// A pending mint failure must point at approval + register, not leak the
	// raw "Assertion is invalid" detail on its own.
	pending := contextAuthErr(&auth.PendingError{Detail: "Assertion is invalid"}, st)
	var coded *ux.CodedError
	if !errors.As(pending, &coded) || coded.Code != ux.CodePendingApproval {
		t.Fatalf("pending error not PENDING_APPROVAL: %v", pending)
	}
	if !strings.Contains(pending.Error(), "Assertion is invalid") {
		t.Errorf("pending error dropped server detail: %v", pending)
	}
	if !strings.Contains(coded.Actionable, "jentic register") {
		t.Errorf("pending remediation should name jentic register: %q", coded.Actionable)
	}

	// An unregistered identity degrades to the typed not-registered error.
	unreg := contextAuthErr(fmt.Errorf("mint: %w", auth.ErrNotRegistered), st)
	if !errors.As(unreg, &coded) || coded.Code != ux.CodeNotAuthenticated {
		t.Fatalf("unregistered error not NOT_AUTHENTICATED: %v", unreg)
	}
	for _, want := range []string{"bot", "prod", "jentic register"} {
		if !strings.Contains(unreg.Error(), want) {
			t.Errorf("unregistered error missing %q: %v", want, unreg)
		}
	}

	// Anything else (revoked key, server misconfig) still lands on a coded,
	// actionable NOT_AUTHENTICATED — never a bare error.
	generic := contextAuthErr(errors.New("boom"), st)
	if !errors.As(generic, &coded) || coded.Code != ux.CodeNotAuthenticated {
		t.Fatalf("generic error not coded: %v", generic)
	}
	if coded.Actionable != "jentic register" {
		t.Errorf("generic remediation = %q, want jentic register", coded.Actionable)
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
