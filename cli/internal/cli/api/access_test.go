package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/accessclient"
	"github.com/jentic/jentic-one/cli/internal/profile"
)

// --- option parsing (no network) ---

func TestAccessRequestCompose(t *testing.T) {
	t.Run("none", func(t *testing.T) {
		_, err := (&accessRequestOptions{}).compose()
		if !errors.Is(err, errAccessTargetRequired) {
			t.Fatalf("err = %v, want errAccessTargetRequired", err)
		}
	})
	t.Run("scope", func(t *testing.T) {
		items, err := (&accessRequestOptions{scopes: []string{"owner:toolkits:read"}}).compose()
		if err != nil {
			t.Fatal(err)
		}
		if len(items) != 1 {
			t.Fatalf("items = %+v", items)
		}
		it := items[0]
		if it.ResourceType != "scope" || it.Action != "grant" || it.ResourceID != "owner:toolkits:read" {
			t.Errorf("item = %+v", it)
		}
	})
	t.Run("toolkit-id", func(t *testing.T) {
		items, err := (&accessRequestOptions{toolkitIDs: []string{"tk_1"}}).compose()
		if err != nil {
			t.Fatal(err)
		}
		it := items[0]
		if it.ResourceType != "toolkit" || it.Action != "bind" || it.ResourceID != "tk_1" {
			t.Errorf("item = %+v", it)
		}
		if it.ResourceReference != nil {
			t.Errorf("toolkit-id should not set a reference: %+v", it)
		}
	})
	t.Run("toolkit reference", func(t *testing.T) {
		items, err := (&accessRequestOptions{toolkits: []string{"httpbin.org/httpbin/1.0.0"}}).compose()
		if err != nil {
			t.Fatal(err)
		}
		ref := items[0].ResourceReference
		if ref["vendor"] != "httpbin.org" || ref["name"] != "httpbin" || ref["version"] != "1.0.0" {
			t.Errorf("reference = %+v", ref)
		}
	})
	t.Run("composite combines chains and single items in fulfilment order", func(t *testing.T) {
		opts := &accessRequestOptions{
			provisions: []string{"slack.com/api", "googleapis.com/sheets"},
			toolkits:   []string{"github.com/api"},
			toolkitIDs: []string{"tk_9"},
			scopes:     []string{"catalog:import"},
		}
		items, err := opts.compose()
		if err != nil {
			t.Fatal(err)
		}
		// Two 4-item chains, then toolkit ref bind, toolkit id bind, scope grant.
		if len(items) != 11 {
			t.Fatalf("expected 11 items, got %d: %+v", len(items), items)
		}
		wantKinds := []string{
			"toolkit:create", "credential:provision", "credential:bind", "toolkit:bind",
			"toolkit:create", "credential:provision", "credential:bind", "toolkit:bind",
			"toolkit:bind", "toolkit:bind", "scope:grant",
		}
		for i, want := range wantKinds {
			got := items[i].ResourceType + ":" + items[i].Action
			if got != want {
				t.Errorf("item[%d] = %s, want %s", i, got, want)
			}
		}
		if items[0].ResourceReference["vendor"] != "slack.com" || items[4].ResourceReference["vendor"] != "googleapis.com" {
			t.Errorf("chains out of flag order: %+v / %+v", items[0].ResourceReference, items[4].ResourceReference)
		}
		if items[8].ResourceReference["vendor"] != "github.com" || items[9].ResourceID != "tk_9" {
			t.Errorf("single binds wrong: %+v / %+v", items[8], items[9])
		}
	})
	t.Run("duplicate provision rejected", func(t *testing.T) {
		opts := &accessRequestOptions{provisions: []string{"x.com/api", " x.com/api "}}
		if _, err := opts.compose(); err == nil || !strings.Contains(err.Error(), "more than once") {
			t.Fatalf("err = %v, want duplicate rejection", err)
		}
	})
	t.Run("raw-domain and slug spellings of the same API collide", func(t *testing.T) {
		// The server slugifies vendor/name (httpbin.org -> httpbin-org); the
		// CLI's dedup key mirrors that so both spellings can't file two chains
		// the server would treat as one API.
		opts := &accessRequestOptions{provisions: []string{"httpbin.org/http", "httpbin-org/http"}}
		if _, err := opts.compose(); err == nil || !strings.Contains(err.Error(), "more than once") {
			t.Fatalf("err = %v, want slug-collision rejection", err)
		}
	})
	t.Run("same API in toolkit and provision rejected", func(t *testing.T) {
		opts := &accessRequestOptions{provisions: []string{"x.com/api"}, toolkits: []string{"x.com/api"}}
		if _, err := opts.compose(); err == nil || !strings.Contains(err.Error(), "both --toolkit and --provision") {
			t.Fatalf("err = %v, want toolkit/provision conflict", err)
		}
	})
	t.Run("auth without provision rejected", func(t *testing.T) {
		opts := &accessRequestOptions{scopes: []string{"s"}, auths: []string{"bearer"}}
		if _, err := opts.compose(); err == nil || !strings.Contains(err.Error(), "only apply with --provision") {
			t.Fatalf("err = %v, want auth-without-provision rejection", err)
		}
	})
}

func TestAccessRequestKeyedAuthAndRules(t *testing.T) {
	t.Run("keyed auth routes to its chain", func(t *testing.T) {
		opts := &accessRequestOptions{
			provisions: []string{"slack.com/api", "googleapis.com/sheets"},
			auths:      []string{"slack.com/api=api_key", "googleapis.com/sheets=oauth2"},
		}
		items, err := opts.compose()
		if err != nil {
			t.Fatal(err)
		}
		if items[1].ResourceReference["security_scheme"] != "api_key" {
			t.Errorf("slack chain auth = %v", items[1].ResourceReference["security_scheme"])
		}
		if items[5].ResourceReference["security_scheme"] != "oauth2" {
			t.Errorf("sheets chain auth = %v", items[5].ResourceReference["security_scheme"])
		}
	})
	t.Run("unkeyed chain defaults to bearer", func(t *testing.T) {
		opts := &accessRequestOptions{
			provisions: []string{"slack.com/api", "googleapis.com/sheets"},
			auths:      []string{"googleapis.com/sheets=oauth2"},
		}
		items, err := opts.compose()
		if err != nil {
			t.Fatal(err)
		}
		if items[1].ResourceReference["security_scheme"] != "bearer" {
			t.Errorf("unkeyed chain should default to bearer, got %v", items[1].ResourceReference["security_scheme"])
		}
	})
	t.Run("bare auth with multiple provisions rejected", func(t *testing.T) {
		opts := &accessRequestOptions{
			provisions: []string{"a.com/x", "b.com/y"},
			auths:      []string{"bearer"},
		}
		if _, err := opts.compose(); err == nil || !strings.Contains(err.Error(), "must be keyed") {
			t.Fatalf("err = %v, want keying requirement", err)
		}
	})
	t.Run("bare auth with single provision still works", func(t *testing.T) {
		opts := &accessRequestOptions{provisions: []string{"a.com/x"}, auths: []string{"none"}}
		items, err := opts.compose()
		if err != nil {
			t.Fatal(err)
		}
		if items[1].ResourceReference["security_scheme"] != "no_auth" {
			t.Errorf("bare --auth none should apply, got %v", items[1].ResourceReference["security_scheme"])
		}
	})
	t.Run("auth key not among provisions rejected", func(t *testing.T) {
		opts := &accessRequestOptions{
			provisions: []string{"a.com/x", "b.com/y"},
			auths:      []string{"c.com/z=bearer"},
		}
		// The key doesn't match any provision, so the value is read as a bare
		// auth type — which is both invalid and unkeyable with two chains.
		if _, err := opts.compose(); err == nil {
			t.Fatal("expected error for auth keyed to an unknown API")
		}
	})
	t.Run("duplicate auth key rejected", func(t *testing.T) {
		opts := &accessRequestOptions{
			provisions: []string{"a.com/x", "b.com/y"},
			auths:      []string{"a.com/x=bearer", "a.com/x=basic"},
		}
		if _, err := opts.compose(); err == nil || !strings.Contains(err.Error(), "more than once") {
			t.Fatalf("err = %v, want duplicate key rejection", err)
		}
	})
	t.Run("keyed rules-json routes to its chain and bare JSON stays bare", func(t *testing.T) {
		opts := &accessRequestOptions{
			provisions: []string{"a.com/x", "b.com/y"},
			rulesJSONs: []string{`a.com/x=[{"effect":"allow","methods":["GET"]}]`},
		}
		items, err := opts.compose()
		if err != nil {
			t.Fatal(err)
		}
		if len(items[2].Rules) != 1 || items[2].Rules[0].Effect != "allow" {
			t.Errorf("a.com/x chain should carry the keyed rules, got %+v", items[2].Rules)
		}
		if items[6].Rules != nil {
			t.Errorf("b.com/y chain should carry no rules, got %+v", items[6].Rules)
		}
	})
}

func TestParseToolkitRef(t *testing.T) {
	if _, err := parseToolkitRef("noslash"); err == nil {
		t.Error("expected error for a vendor with no name")
	}
	ref, err := parseToolkitRef("vendor/name")
	if err != nil {
		t.Fatal(err)
	}
	if _, hasVersion := ref["version"]; hasVersion {
		t.Errorf("two-part ref should omit version: %+v", ref)
	}
}

// --- end-to-end through the command tree ---

func seedAccessProfile(t *testing.T, app *app, name, baseURL string) {
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

func runAccess(t *testing.T, app *app, baseURL string, args ...string) (string, error) {
	t.Helper()
	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(out)
	full := append([]string{"access"}, args...)
	full = append(full, "--profile", "demo", "--base-url", baseURL, "--json")
	root.SetArgs(full)
	err := root.Execute()
	return out.String(), err
}

func TestAccessWhoami(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/me" {
			t.Errorf("path = %q", r.URL.Path)
		}
		_, _ = w.Write([]byte(`{"id":"agnt_test","name":"demo","status":"active",
			"scopes":["capabilities:execute"],"toolkit_bindings":[]}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedAccessProfile(t, app, "demo", srv.URL)

	out, err := runAccess(t, app, srv.URL, "whoami")
	if err != nil {
		t.Fatalf("whoami: %v\n%s", err, out)
	}
	var me map[string]any
	if jsonErr := json.Unmarshal([]byte(out), &me); jsonErr != nil {
		t.Fatalf("output not JSON: %v\n%s", jsonErr, out)
	}
	if me["id"] != "agnt_test" {
		t.Errorf("me = %v", me)
	}
}

func TestAccessWhoamiRendersToolkitName(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/me" {
			t.Errorf("path = %q", r.URL.Path)
		}
		_, _ = w.Write([]byte(`{"id":"agnt_test","name":"demo","status":"active",
			"scopes":["capabilities:execute"],"toolkit_bindings":[
			{"toolkit_id":"tk_named","name":"Design news radar","bound_at":"2026-01-01T00:00:00Z"},
			{"toolkit_id":"tk_bare","bound_at":"2026-01-01T00:00:00Z"}]}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedAccessProfile(t, app, "demo", srv.URL)

	// No --json: exercise the human-readable rendering that shows name (tk_…).
	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app.App)
	root.SetOut(out)
	root.SetErr(out)
	root.SetArgs([]string{"access", "whoami", "--profile", "demo", "--base-url", srv.URL})
	if err := root.Execute(); err != nil {
		t.Fatalf("whoami: %v\n%s", err, out.String())
	}
	rendered := out.String()
	if !strings.Contains(rendered, "Design news radar") {
		t.Errorf("expected toolkit name in output, got:\n%s", rendered)
	}
	if !strings.Contains(rendered, "tk_named") || !strings.Contains(rendered, "tk_bare") {
		t.Errorf("expected both toolkit ids in output, got:\n%s", rendered)
	}
}

// printMe points the agent at `jentic profile view` for the filesystem side of
// "what can I do?", both when it has toolkit bindings and when it has none (the
// no-bindings branch must not short-circuit before the hint).
func TestPrintMePointsAtProfileView(t *testing.T) {
	for _, tc := range []struct {
		name string
		me   *accessclient.Me
	}{
		{"with bindings", &accessclient.Me{ID: "agnt_1", Status: "active", ToolkitBindings: []accessclient.ToolkitBinding{{ToolkitID: "tk_1"}}}},
		{"no bindings", &accessclient.Me{ID: "agnt_1", Status: "active"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			app := testApp(t)
			app.printMe(tc.me)
			got := app.Out.(*bytes.Buffer).String()
			if !strings.Contains(got, "jentic profile view") {
				t.Errorf("expected a pointer to `jentic profile view`, got:\n%s", got)
			}
		})
	}
}

func TestAccessRequestFiles(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	var gotBody map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &gotBody)
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"id":"arq_1","actor_id":"agnt_test","status":"pending",
			"approve_url":"https://cp/approve/arq_1","filed_at":"2026-01-01T00:00:00Z",
			"expires_at":"2026-01-08T00:00:00Z",
			"items":[{"id":"arqi_1","resource_type":"toolkit","action":"bind","status":"pending"}]}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedAccessProfile(t, app, "demo", srv.URL)

	out, err := runAccess(t, app, srv.URL, "request", "--toolkit", "httpbin.org/httpbin", "--reason", "smoke")
	if err != nil {
		t.Fatalf("request: %v\n%s", err, out)
	}
	if gotBody["reason"] != "smoke" {
		t.Errorf("reason not sent: %v", gotBody)
	}
	item := gotBody["items"].([]any)[0].(map[string]any)
	ref := item["resource_reference"].(map[string]any)
	if ref["vendor"] != "httpbin.org" || ref["name"] != "httpbin" {
		t.Errorf("reference not sent: %v", item)
	}
	if !strings.Contains(out, "arq_1") {
		t.Errorf("output missing request id:\n%s", out)
	}
}

func TestAccessRequestAttachesToExistingPending(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/access-requests":
			w.Header().Set("Content-Type", "application/problem+json")
			w.WriteHeader(http.StatusConflict)
			_, _ = w.Write([]byte(`{"type":"access_request_duplicate_pending","status":409,
				"existing_request_id":"arq_old","approve_url":"https://cp/approve/arq_old"}`))
		case r.Method == http.MethodGet && r.URL.Path == "/access-requests/arq_old":
			_, _ = w.Write([]byte(`{"id":"arq_old","actor_id":"agnt_test","status":"pending",
				"approve_url":"https://cp/approve/arq_old","filed_at":"2026-01-01T00:00:00Z",
				"expires_at":"2026-01-08T00:00:00Z","items":[]}`))
		default:
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	app := testApp(t)
	seedAccessProfile(t, app, "demo", srv.URL)

	out, err := runAccess(t, app, srv.URL, "request", "--toolkit-id", "tk_1")
	if err != nil {
		t.Fatalf("request: %v\n%s", err, out)
	}
	if !strings.Contains(out, "arq_old") {
		t.Errorf("should have attached to existing request:\n%s", out)
	}
}

func TestAccessRequestCompositeFilesAllItems(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	var gotBody map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &gotBody)
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"id":"arq_1","actor_id":"agnt_test","status":"pending",
			"approve_url":"https://cp/approve/arq_1","filed_at":"2026-01-01T00:00:00Z",
			"expires_at":"2026-01-08T00:00:00Z","items":[]}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedAccessProfile(t, app, "demo", srv.URL)

	out, err := runAccess(t, app, srv.URL, "request",
		"--provision", "slack.com/api", "--auth", "slack.com/api=api_key",
		"--provision", "googleapis.com/sheets",
		"--toolkit", "github.com/api",
		"--scope", "catalog:import",
		"--reason", "composite")
	if err != nil {
		t.Fatalf("request: %v\n%s", err, out)
	}
	items := gotBody["items"].([]any)
	// Two 4-item chains + one toolkit bind + one scope grant, in one envelope.
	if len(items) != 10 {
		t.Fatalf("expected 10 items in one request, got %d: %v", len(items), items)
	}
	// The keyed auth landed on the slack chain only.
	slackProv := items[1].(map[string]any)["resource_reference"].(map[string]any)
	sheetsProv := items[5].(map[string]any)["resource_reference"].(map[string]any)
	if slackProv["security_scheme"] != "api_key" || sheetsProv["security_scheme"] != "bearer" {
		t.Errorf("keyed auth misrouted: slack=%v sheets=%v", slackProv, sheetsProv)
	}
	// Each chain's credential:bind names its API so the items stay attributable.
	slackBind := items[2].(map[string]any)
	if ref, ok := slackBind["resource_reference"].(map[string]any); !ok || ref["vendor"] != "slack.com" {
		t.Errorf("credential:bind should carry its chain's reference, got %v", slackBind)
	}
}

func TestAccessRequestCompositeDuplicateAborts(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	var gets atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/access-requests" {
			w.Header().Set("Content-Type", "application/problem+json")
			w.WriteHeader(http.StatusConflict)
			_, _ = w.Write([]byte(`{"type":"access_request_duplicate_pending","status":409,
				"existing_request_id":"arq_old","approve_url":"https://cp/approve/arq_old"}`))
			return
		}
		gets.Add(1)
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	app := testApp(t)
	seedAccessProfile(t, app, "demo", srv.URL)

	// Filing is all-or-nothing: attaching a composite to the older, smaller
	// pending request would silently drop the other targets, so it must abort
	// with an actionable error instead.
	out, err := runAccess(t, app, srv.URL, "request",
		"--provision", "slack.com/api", "--toolkit", "github.com/api")
	if err == nil {
		t.Fatalf("composite 409 should abort, got success:\n%s", out)
	}
	if !strings.Contains(err.Error(), "nothing was filed") || !strings.Contains(err.Error(), "arq_old") {
		t.Errorf("error should name the collision and the pending request: %v", err)
	}
	if gets.Load() != 0 {
		t.Errorf("composite must not attach to (GET) the existing request; got %d GETs", gets.Load())
	}
}

func TestAccessRequestWaitPollsUntilTerminal(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	fastPoll(t)
	var gets atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/access-requests" {
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"id":"arq_1","actor_id":"agnt_test","status":"pending",
				"approve_url":"u","filed_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-08T00:00:00Z","items":[]}`))
			return
		}
		// GET status: pending twice, then approved.
		status := "pending"
		if gets.Add(1) >= 3 {
			status = "approved"
		}
		_, _ = w.Write([]byte(`{"id":"arq_1","actor_id":"agnt_test","status":"` + status + `",
			"approve_url":"u","filed_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-08T00:00:00Z","items":[]}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedAccessProfile(t, app, "demo", srv.URL)

	out, err := runAccess(t, app, srv.URL, "request", "--scope", "owner:toolkits:read", "--wait", "--timeout", "30s")
	if err != nil {
		t.Fatalf("request --wait: %v\n%s", err, out)
	}
	if !strings.Contains(out, `"status": "approved"`) {
		t.Errorf("expected approved status after polling:\n%s", out)
	}
}

func TestAccessRequestWaitDeniedExitsCode2(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	fastPoll(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/access-requests" {
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"id":"arq_1","actor_id":"agnt_test","status":"pending",
				"approve_url":"u","filed_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-08T00:00:00Z","items":[]}`))
			return
		}
		_, _ = w.Write([]byte(`{"id":"arq_1","actor_id":"agnt_test","status":"denied",
			"approve_url":"u","filed_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-08T00:00:00Z","items":[]}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedAccessProfile(t, app, "demo", srv.URL)

	out, err := runAccess(t, app, srv.URL, "request", "--scope", "owner:toolkits:read", "--wait", "--timeout", "30s")
	var ec *exitCodeError
	if !errors.As(err, &ec) {
		t.Fatalf("error type = %T (%v), want *exitCodeError\n%s", err, err, out)
	}
	if ec.Code != 2 {
		t.Errorf("exit code = %d, want 2", ec.Code)
	}
	if !strings.Contains(out, `"status": "denied"`) {
		t.Errorf("expected denied status in output:\n%s", out)
	}
}

func TestAccessRequestWaitTimeoutExitsCode3(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	fastPoll(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/access-requests" {
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"id":"arq_1","actor_id":"agnt_test","status":"pending",
				"approve_url":"u","filed_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-08T00:00:00Z","items":[]}`))
			return
		}
		// Never leaves pending → forces a timeout.
		_, _ = w.Write([]byte(`{"id":"arq_1","actor_id":"agnt_test","status":"pending",
			"approve_url":"u","filed_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-08T00:00:00Z","items":[]}`))
	}))
	defer srv.Close()

	app := testApp(t)
	seedAccessProfile(t, app, "demo", srv.URL)

	out, err := runAccess(t, app, srv.URL, "request", "--scope", "owner:toolkits:read", "--wait", "--timeout", "1ms")
	var ec *exitCodeError
	if !errors.As(err, &ec) {
		t.Fatalf("error type = %T (%v), want *exitCodeError\n%s", err, err, out)
	}
	if ec.Code != 3 {
		t.Errorf("exit code = %d, want 3", ec.Code)
	}
}

func TestAccessListAndStatusAndWithdraw(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/access-requests" && r.Method == http.MethodGet:
			_, _ = w.Write([]byte(`{"data":[{"id":"arq_1","actor_id":"agnt_test","status":"pending",
				"approve_url":"u","filed_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-08T00:00:00Z","items":[]}],
				"has_more":false,"next_cursor":""}`))
		case r.URL.Path == "/access-requests/arq_1" && r.Method == http.MethodGet:
			_, _ = w.Write([]byte(`{"id":"arq_1","actor_id":"agnt_test","status":"pending",
				"approve_url":"u","filed_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-08T00:00:00Z","items":[]}`))
		case strings.HasSuffix(r.URL.Path, ":withdraw"):
			_, _ = w.Write([]byte(`{"id":"arq_1","actor_id":"agnt_test","status":"withdrawn",
				"approve_url":"u","filed_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-08T00:00:00Z","items":[]}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	app := testApp(t)
	seedAccessProfile(t, app, "demo", srv.URL)

	if out, err := runAccess(t, app, srv.URL, "list", "--status", "pending"); err != nil {
		t.Fatalf("list: %v\n%s", err, out)
	} else if !strings.Contains(out, "arq_1") {
		t.Errorf("list missing arq_1:\n%s", out)
	}
	if out, err := runAccess(t, app, srv.URL, "status", "arq_1"); err != nil {
		t.Fatalf("status: %v\n%s", err, out)
	}
	if out, err := runAccess(t, app, srv.URL, "withdraw", "arq_1"); err != nil {
		t.Fatalf("withdraw: %v\n%s", err, out)
	} else if !strings.Contains(out, "withdrawn") {
		t.Errorf("withdraw output:\n%s", out)
	}
}
