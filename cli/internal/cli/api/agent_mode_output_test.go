package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// registryFakeServer serves the control-plane routes the apis / catalog /
// endpoints command groups read, so full-tree runs exercise the real output
// branch end to end.
func registryFakeServer(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		p := r.URL.Path
		switch {
		case p == "/reference/endpoints.json":
			_, _ = w.Write([]byte(sampleReference))
		case p == "/apis":
			_, _ = w.Write([]byte(`{"data":[{"api":{"vendor":"stripe.com","name":"api","version":"v1"},"current_revision_id":"r1","operation_count":1}],"has_more":false,"next_cursor":""}`))
		case strings.HasPrefix(p, "/apis/") && strings.HasSuffix(p, "/revisions"):
			_, _ = w.Write([]byte(`{"data":[{"revision_id":"r1","state":"published","is_current":true,"operation_count":1}],"has_more":false}`))
		case strings.HasPrefix(p, "/apis/") && strings.HasSuffix(p, "/operations"):
			_, _ = w.Write([]byte(`{"data":[{"method":"GET","path":"/v1/charges","name":"List charges"}],"has_more":false}`))
		case strings.HasPrefix(p, "/apis/") && strings.HasSuffix(p, ":promote"):
			_, _ = w.Write([]byte(`{}`))
		case strings.HasPrefix(p, "/apis/"):
			_, _ = w.Write([]byte(`{"api":{"vendor":"stripe.com","name":"api","version":"v1"},"display_name":"Stripe","current_revision_id":"r1","operation_count":1}`))
		case p == "/catalog":
			_, _ = w.Write([]byte(`{"data":[{"api_id":"stripe.com","vendor":"stripe.com","registered":true,"_links":{}}],"catalog_total":1,"registered_count":1,"manifest_age_seconds":5,"has_more":false,"next_cursor":""}`))
		case r.Method == http.MethodPost && strings.HasSuffix(p, ":import"):
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"job_id":"job_1"}`))
		case strings.HasPrefix(p, "/jobs/") && strings.HasSuffix(p, "/result"):
			_, _ = w.Write([]byte(`{"revisions":[{"api":{"vendor":"stripe.com","name":"api","version":"v1"},"revision_id":"r1","state":"draft"}]}`))
		case strings.HasPrefix(p, "/jobs/"):
			_, _ = w.Write([]byte(`{"job_id":"job_1","status":"completed"}`))
		case strings.HasPrefix(p, "/catalog/") && strings.HasSuffix(p, "/operations"):
			_, _ = w.Write([]byte(`{"data":[{"method":"GET","path":"/v1/charges","summary":"List charges"}],"total":1,"offset":0,"truncated":false,"info":{"title":"Stripe","version":"2024"}}`))
		case strings.HasPrefix(p, "/catalog/"):
			_, _ = w.Write([]byte(`{"api_id":"stripe.com","vendor":"stripe.com","spec_url":"https://spec","registered":true,"_links":{}}`))
		default:
			t.Errorf("unexpected %s %s", r.Method, p)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
}

// runRegistryCmd executes args on the full jentic tree against srv and returns
// stdout.
func runRegistryCmd(t *testing.T, srv *httptest.Server, args ...string) string {
	t.Helper()
	app := testApp(t)
	seedRegistered(t, app, "default", srv.URL)
	root := newAPIRootCmd(app.App)
	out := new(bytes.Buffer)
	app.Out = out
	root.SetOut(out)
	root.SetErr(new(bytes.Buffer))
	root.SetArgs(args)
	if err := root.Execute(); err != nil {
		t.Fatalf("%v: %v\nstdout: %s", args, err, out.String())
	}
	return out.String()
}

// TestRegistryCommandsEmitJSONInAgentMode is the jentic-one#1414 regression:
// agent mode commits to exactly one JSON document on stdout, so these commands
// must route through cmdcore.JSONOrPretty rather than the bare --json flag.
func TestRegistryCommandsEmitJSONInAgentMode(t *testing.T) {
	for _, tc := range []struct {
		name    string
		args    []string
		wantKey string
	}{
		{"apis list", []string{"apis", "list"}, "data"},
		{"apis show", []string{"apis", "show", "stripe.com/api/v1"}, "api"},
		{"apis revisions", []string{"apis", "revisions", "stripe.com/api/v1"}, "data"},
		{"apis operations", []string{"apis", "operations", "stripe.com/api/v1"}, "data"},
		{"catalog list", []string{"catalog", "list"}, "data"},
		{"catalog search", []string{"catalog", "search", "stripe"}, "data"},
		{"catalog outdated", []string{"catalog", "outdated"}, "data"},
		{"catalog show", []string{"catalog", "show", "stripe.com"}, "entry"},
		{"catalog import", []string{"catalog", "import", "stripe.com", "--timeout", "5s"}, "revisions"},
		{"catalog import --no-wait", []string{"catalog", "import", "stripe.com", "--no-wait"}, "job_id"},
		{"endpoints", []string{"endpoints"}, "data"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := registryFakeServer(t)
			defer srv.Close()

			out := runRegistryCmd(t, srv, append([]string{"--mode", "agent"}, tc.args...)...)

			// Decoding with a Decoder and then demanding EOF proves stdout holds
			// exactly one JSON document — no table, banner, or progress line.
			dec := json.NewDecoder(strings.NewReader(out))
			var doc map[string]any
			if err := dec.Decode(&doc); err != nil {
				t.Fatalf("stdout is not a JSON document: %v\n---\n%s", err, out)
			}
			if dec.More() {
				t.Fatalf("stdout carries more than one JSON document:\n%s", out)
			}
			if _, ok := doc[tc.wantKey]; !ok {
				t.Errorf("envelope missing %q key: %v", tc.wantKey, doc)
			}
		})
	}
}

// TestRegistryCommandsExplicitHumanStaysPretty pins the other side of the seam:
// an explicit --mode human keeps the formatted output even on a piped stdout.
func TestRegistryCommandsExplicitHumanStaysPretty(t *testing.T) {
	srv := registryFakeServer(t)
	defer srv.Close()

	out := runRegistryCmd(t, srv, "--mode", "human", "apis", "list")
	if json.Valid([]byte(out)) {
		t.Fatalf("explicit human mode emitted JSON:\n%s", out)
	}
	if !strings.Contains(out, "stripe.com/api/v1") {
		t.Errorf("human list missing the API row:\n%s", out)
	}
}
