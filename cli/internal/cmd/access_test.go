package cmd

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

	"github.com/jentic/jentic-one/cli/internal/profile"
)

// --- option parsing (no network) ---

func TestAccessRequestItemTargetSelection(t *testing.T) {
	t.Run("none", func(t *testing.T) {
		_, err := (&accessRequestOptions{}).item()
		if !errors.Is(err, errAccessTargetRequired) {
			t.Fatalf("err = %v, want errAccessTargetRequired", err)
		}
	})
	t.Run("conflict", func(t *testing.T) {
		_, err := (&accessRequestOptions{toolkit: "a/b", scope: "s"}).item()
		if !errors.Is(err, errAccessTargetConflict) {
			t.Fatalf("err = %v, want errAccessTargetConflict", err)
		}
	})
	t.Run("scope", func(t *testing.T) {
		it, err := (&accessRequestOptions{scope: "owner:toolkits:read"}).item()
		if err != nil {
			t.Fatal(err)
		}
		if it.ResourceType != "scope" || it.Action != "grant" || it.ResourceID != "owner:toolkits:read" {
			t.Errorf("item = %+v", it)
		}
	})
	t.Run("toolkit-id", func(t *testing.T) {
		it, err := (&accessRequestOptions{toolkitID: "tk_1"}).item()
		if err != nil {
			t.Fatal(err)
		}
		if it.ResourceType != "toolkit" || it.Action != "bind" || it.ResourceID != "tk_1" {
			t.Errorf("item = %+v", it)
		}
		if it.ResourceReference != nil {
			t.Errorf("toolkit-id should not set a reference: %+v", it)
		}
	})
	t.Run("toolkit reference", func(t *testing.T) {
		it, err := (&accessRequestOptions{toolkit: "httpbin.org/httpbin/1.0.0"}).item()
		if err != nil {
			t.Fatal(err)
		}
		ref := it.ResourceReference
		if ref["vendor"] != "httpbin.org" || ref["name"] != "httpbin" || ref["version"] != "1.0.0" {
			t.Errorf("reference = %+v", ref)
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

func seedAccessProfile(t *testing.T, app *App, name, baseURL string) {
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

func runAccess(t *testing.T, app *App, baseURL string, args ...string) (string, error) {
	t.Helper()
	out := new(bytes.Buffer)
	app.Out = out
	root := newAPIRootCmd(app)
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
	root := newAPIRootCmd(app)
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
	if ec.code != 2 {
		t.Errorf("exit code = %d, want 2", ec.code)
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
	if ec.code != 3 {
		t.Errorf("exit code = %d, want 3", ec.code)
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
