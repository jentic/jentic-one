package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// runJenticCapture runs the jentic root capturing app.Out, so passthrough tests
// can assert on the emitted body (the api command writes to app.Out, not the
// Audience).
func runJenticCapture(t *testing.T, args ...string) (string, error) {
	t.Helper()
	app := testApp(t)
	var out bytes.Buffer
	app.Out = &out
	root := newAPIRootCmd(app.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs(args)
	err := root.Execute()
	return out.String(), err
}

func TestAPIPassthrough_GETMatchesSpecAndEmitsBody(t *testing.T) {
	withXDG(t)

	var gotPath, gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotAuth = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"data": []string{"cred_1"}})
	}))
	defer srv.Close()
	seedActiveContextTo(t, srv.URL)

	out, err := runJenticCapture(t, "api", "GET", "/credentials")
	if err != nil {
		t.Fatalf("api GET /credentials: %v", err)
	}
	if gotPath != "/credentials" {
		t.Errorf("server saw path %q", gotPath)
	}
	if gotAuth != "Bearer test-token" {
		t.Errorf("auth header = %q (session/auth editor should have run)", gotAuth)
	}
	if !strings.Contains(out, "cred_1") {
		t.Errorf("body not emitted: %q", out)
	}
}

func TestAPIPassthrough_404NegotiatesBackendVersion(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/system/version":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"current":"0.20.0","update_available":false}`))
		default:
			// A real spec route, but this (old) server doesn't serve it.
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()
	seedActiveContextTo(t, srv.URL)

	// /credentials is in the embedded spec; a 404 should trigger the version probe
	// and surface the route-unsupported-upstream hint (exit non-zero).
	_, err := runJenticCapture(t, "api", "GET", "/credentials")
	if err == nil {
		t.Fatal("expected an enriched error on a 404 for a spec route")
	}
}

func TestAPIPassthrough_RejectsUnknownPath(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("server should never be reached for a spec-rejected path")
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()
	seedActiveContextTo(t, srv.URL)

	if _, err := runJenticCapture(t, "api", "GET", "/definitely/not/a/route"); err == nil {
		t.Fatal("expected a spec-allowlist rejection")
	}
}

func TestAPIPassthrough_FailOnError(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"detail":"boom"}`))
	}))
	defer srv.Close()
	seedActiveContextTo(t, srv.URL)

	// Without --fail-on-error, a 500 is still exit 0 (body is data).
	if _, err := runJenticCapture(t, "api", "GET", "/credentials"); err != nil {
		t.Errorf("without --fail-on-error a 500 should not error: %v", err)
	}
	// With --fail-on-error, non-2xx surfaces as an error.
	if _, err := runJenticCapture(t, "api", "GET", "/credentials", "--fail-on-error"); err == nil {
		t.Error("expected --fail-on-error to surface the 500")
	}
}

func TestAPIOps_RunsOfflineFromEmbeddedSpec(t *testing.T) {
	withXDG(t)
	// api ops works fully offline (embedded spec) — no server needed, but the
	// interceptor still needs an active state, so point at a dummy file-less env.
	// Render writes to real stdout (not app.Out), so we assert it simply succeeds;
	// the listing/filtering logic itself is covered in the apispec package tests.
	t.Setenv("JENTIC_BASE_URL", "https://control.invalid")
	t.Setenv("JENTIC_BEARER_TOKEN", "x")
	t.Setenv("JENTIC_MODE", "agent")

	if _, err := runJenticCapture(t, "api", "ops", "--filter", "credential"); err != nil {
		t.Fatalf("api ops: %v", err)
	}
}
