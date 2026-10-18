package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// registryFakeServer serves the control-plane routes the apis / catalog /
// endpoints command groups call, so full-tree runs exercise the real output
// branch end to end.
func registryFakeServer(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		p := r.URL.Path
		switch {
		case p == "/reference/endpoints.json":
			_, _ = w.Write([]byte(sampleReference))
		case p == "/inspect":
			_, _ = w.Write([]byte(`{"method":"GET","path":"/v1/charges","parameters":[]}`))
		case p == "/catalog/sources":
			_, _ = w.Write([]byte(`{"data":[{"slug":"public","kind":"git-manifest","visibility":"public","status":"ok","last_sync_at":null,"live_entries":3,"consecutive_failures":0,"sync_pending":false}]}`))
		case r.Method == http.MethodPost && strings.HasSuffix(p, ":sync"):
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"slug":"public","status":"queued","forced":false,"due_at":null}`))
		case p == "/apis":
			_, _ = w.Write([]byte(`{"data":[{"api":{"vendor":"stripe.com","name":"api","version":"v1"},"current_revision_id":"r1","operation_count":1}],"has_more":false,"next_cursor":""}`))
		case r.Method == http.MethodDelete && strings.HasPrefix(p, "/apis/"):
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodPost && (strings.HasSuffix(p, ":promote") || strings.HasSuffix(p, ":archive")):
			_, _ = w.Write([]byte(`{}`))
		case strings.HasPrefix(p, "/apis/") && strings.HasSuffix(p, "/openapi"):
			_, _ = w.Write([]byte(`{"openapi":"3.1.0","info":{"title":"Stripe","version":"v1"},"paths":{}}`))
		case strings.HasPrefix(p, "/apis/") && strings.HasSuffix(p, "/revisions"):
			_, _ = w.Write([]byte(`{"data":[{"revision_id":"r1","state":"published","is_current":true,"operation_count":1}],"has_more":false}`))
		case strings.HasPrefix(p, "/apis/") && strings.HasSuffix(p, "/operations"):
			_, _ = w.Write([]byte(`{"data":[{"method":"GET","path":"/v1/charges","name":"List charges"}],"has_more":true,"next_cursor":"c2"}`))
		case strings.HasPrefix(p, "/apis/"):
			_, _ = w.Write([]byte(`{"api":{"vendor":"stripe.com","name":"api","version":"v1"},"display_name":"Stripe","current_revision_id":"r1","operation_count":1}`))
		case p == "/catalog":
			_, _ = w.Write([]byte(`{"data":[{"entry_id":"public/stripe.com","source_slug":"public","catalog_api_id":"stripe.com","vendor":"stripe.com","registered":true,"_links":{"self":"/catalog/public/stripe.com","operations":"/catalog/public/stripe.com/operations","import":"/catalog/public/stripe.com:import"}}],"catalog_total":1,"registered_count":1,"oldest_source_sync_age_seconds":5,"has_more":false,"next_cursor":""}`))
		case r.Method == http.MethodPost && strings.HasSuffix(p, ":import"):
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"job_id":"job_1"}`))
		case strings.HasPrefix(p, "/jobs/") && strings.HasSuffix(p, "/result"):
			// No revisions key: the import envelope must still emit [] not null.
			_, _ = w.Write([]byte(`{}`))
		case strings.HasPrefix(p, "/jobs/"):
			_, _ = w.Write([]byte(`{"job_id":"job_1","status":"completed"}`))
		case strings.HasPrefix(p, "/catalog/") && strings.HasSuffix(p, "/operations"):
			_, _ = w.Write([]byte(`{"data":[{"method":"GET","path":"/v1/charges","summary":"List charges"}],"total":1,"offset":0,"truncated":false,"info":{"title":"Stripe","version":"2024"}}`))
		case strings.HasPrefix(p, "/catalog/"):
			_, _ = w.Write([]byte(`{"entry_id":"public/stripe.com","source_slug":"public","catalog_api_id":"stripe.com","vendor":"stripe.com","registered":true,"_links":{"self":"/catalog/public/stripe.com","operations":"/catalog/public/stripe.com/operations","import":"/catalog/public/stripe.com:import"}}`))
		default:
			t.Errorf("unexpected %s %s", r.Method, p)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
}

// runRegistryCmd executes args on the full jentic tree against srv and returns
// stdout and the command error.
func runRegistryCmd(t *testing.T, srv *httptest.Server, args ...string) (string, error) {
	t.Helper()
	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)
	root := newAPIRootCmd(app.App)
	out := new(bytes.Buffer)
	app.Out = out
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs(args)
	err := root.Execute()
	return out.String(), err
}

// simulatePTY makes the browsers believe stdin is a terminal, as it is under
// the PTY most agent harnesses use.
func simulatePTY(t *testing.T) {
	t.Helper()
	orig := browserInteractive
	browserInteractive = func() bool { return true }
	t.Cleanup(func() { browserInteractive = orig })
}

// decodeOneDocument asserts stdout is exactly one JSON object and returns it.
func decodeOneDocument(t *testing.T, out string) map[string]any {
	t.Helper()
	dec := json.NewDecoder(strings.NewReader(out))
	var doc map[string]any
	if err := dec.Decode(&doc); err != nil {
		t.Fatalf("stdout is not a JSON document: %v\n---\n%s", err, out)
	}
	if dec.More() {
		t.Fatalf("stdout carries more than one JSON document:\n%s", out)
	}
	return doc
}

// TestRegistryCommandsEmitJSONInAgentMode is the jentic-one#1414 regression:
// agent mode commits to exactly one JSON document on stdout, so these commands
// must route through cmdcore.JSONOrPretty rather than the bare --json flag —
// and the bare groups must not open their TUI browser on a PTY.
func TestRegistryCommandsEmitJSONInAgentMode(t *testing.T) {
	specOut := filepath.Join(t.TempDir(), "spec.json")
	for _, tc := range []struct {
		name    string
		args    []string
		wantKey string
	}{
		{"apis (bare)", []string{"apis"}, "data"},
		{"apis list", []string{"apis", "list"}, "data"},
		{"apis show", []string{"apis", "show", "stripe.com/api/v1"}, "api"},
		{"apis revisions", []string{"apis", "revisions", "stripe.com/api/v1"}, "data"},
		{"apis operations", []string{"apis", "operations", "stripe.com/api/v1"}, "data"},
		{"apis inspect", []string{"apis", "inspect", "GET:https://api.stripe.com/v1/charges"}, "method"},
		{"apis promote", []string{"apis", "promote", "stripe.com/api/v1", "r2"}, "status"},
		{"apis archive", []string{"apis", "archive", "stripe.com/api/v1", "r2"}, "status"},
		{"apis rm --yes", []string{"apis", "rm", "stripe.com/api/v1", "--yes"}, "status"},
		{"apis spec --out", []string{"apis", "spec", "stripe.com/api/v1", "--out", specOut}, "status"},
		{"catalog (bare)", []string{"catalog"}, "data"},
		{"catalog list", []string{"catalog", "list"}, "data"},
		{"catalog search", []string{"catalog", "search", "stripe"}, "data"},
		{"catalog outdated", []string{"catalog", "outdated"}, "data"},
		{"catalog show", []string{"catalog", "show", "public/stripe.com"}, "entry"},
		{"catalog import", []string{"catalog", "import", "public/stripe.com", "--timeout", "5s"}, "revisions"},
		{"catalog import --no-wait", []string{"catalog", "import", "public/stripe.com", "--no-wait"}, "job_id"},
		{"catalog refresh", []string{"catalog", "refresh"}, "status"},
		{"endpoints", []string{"endpoints"}, "data"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			simulatePTY(t)
			srv := registryFakeServer(t)
			defer srv.Close()

			out, err := runRegistryCmd(t, srv, append([]string{"--mode", "agent"}, tc.args...)...)
			if err != nil {
				t.Fatalf("%v: %v\nstdout: %s", tc.args, err, out)
			}
			doc := decodeOneDocument(t, out)
			if _, ok := doc[tc.wantKey]; !ok {
				t.Errorf("envelope missing %q key: %v", tc.wantKey, doc)
			}
			// apis inspect relays the server's document verbatim; every
			// CLI-built envelope carries schema_version.
			if tc.name != "apis inspect" && doc["schema_version"] != "2" {
				t.Errorf("schema_version = %v, want \"2\": %v", doc["schema_version"], doc)
			}
		})
	}
}

// TestRegistryEnvelopeDetails pins the shape fixes that ride along with the
// agent-mode routing: the show preview keeps its pagination, and an import
// with no revisions emits [] rather than null.
func TestRegistryEnvelopeDetails(t *testing.T) {
	srv := registryFakeServer(t)
	defer srv.Close()

	out, err := runRegistryCmd(t, srv, "--mode", "agent", "apis", "show", "stripe.com/api/v1")
	if err != nil {
		t.Fatalf("apis show: %v", err)
	}
	show := decodeOneDocument(t, out)
	if show["operations_has_more"] != true || show["operations_next_cursor"] != "c2" {
		t.Errorf("apis show dropped preview pagination: %v", show)
	}

	out, err = runRegistryCmd(t, srv, "--mode", "agent", "catalog", "import", "stripe.com", "--timeout", "5s")
	if err != nil {
		t.Fatalf("catalog import: %v", err)
	}
	if !strings.Contains(out, `"revisions": []`) {
		t.Errorf("empty import must emit revisions: [], got:\n%s", out)
	}

	// sampleReference's public /health row omits both arrays.
	out, err = runRegistryCmd(t, srv, "--mode", "agent", "endpoints")
	if err != nil {
		t.Fatalf("endpoints: %v", err)
	}
	if strings.Contains(out, "null") {
		t.Errorf("endpoints JSON must not carry null arrays:\n%s", out)
	}
}

// TestApisRmAgentModeFailsClosedWithoutYes: on a PTY in agent mode, rm must
// refuse with INTERACTIVE_CONFIRM_BLOCKED instead of opening a confirm prompt,
// and must not reach the server.
func TestApisRmAgentModeFailsClosedWithoutYes(t *testing.T) {
	simulatePTY(t)
	srv := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		t.Errorf("rm without --yes reached the server: %s %s", r.Method, r.URL.Path)
	}))
	defer srv.Close()

	out, err := runRegistryCmd(t, srv, "--mode", "agent", "apis", "rm", "stripe.com/api/v1")
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeConfirmBlocked {
		t.Fatalf("err = %v, want INTERACTIVE_CONFIRM_BLOCKED", err)
	}
	if out != "" {
		t.Errorf("stdout must stay empty on a blocked confirm, got:\n%s", out)
	}
}

// TestRegistryCommandsExplicitHumanStaysPretty pins the other side of the seam:
// an explicit --mode human keeps the formatted output even on a piped stdout.
func TestRegistryCommandsExplicitHumanStaysPretty(t *testing.T) {
	srv := registryFakeServer(t)
	defer srv.Close()

	for _, args := range [][]string{
		{"--mode", "human", "apis", "list"},
		{"--mode", "human", "apis", "promote", "stripe.com/api/v1", "r2"},
		{"--mode", "human", "catalog", "refresh"},
	} {
		out, err := runRegistryCmd(t, srv, args...)
		if err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		if json.Valid([]byte(out)) {
			t.Errorf("%v: explicit human mode emitted JSON:\n%s", args, out)
		}
	}
}
