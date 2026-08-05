package adminclient

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestReportLatestRelease(t *testing.T) {
	var gotMethod, gotPath, gotAuth, gotVersion string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod, gotPath, gotAuth = r.Method, r.URL.Path, r.Header.Get("Authorization")
		body, _ := io.ReadAll(r.Body)
		var req latestReleaseRequest
		_ = json.Unmarshal(body, &req)
		gotVersion = req.Version
		// Empty 200 body — ReportLatestRelease passes nil out, so this must succeed.
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	if err := New(srv.URL).ReportLatestRelease(context.Background(), "tok-123", "v0.26.0"); err != nil {
		t.Fatalf("ReportLatestRelease: %v", err)
	}
	if gotMethod != http.MethodPost {
		t.Errorf("method = %q, want POST", gotMethod)
	}
	if gotPath != "/admin/system/latest-release" {
		t.Errorf("path = %q", gotPath)
	}
	if gotAuth != "Bearer tok-123" {
		t.Errorf("auth = %q", gotAuth)
	}
	if gotVersion != "v0.26.0" {
		t.Errorf("version = %q, want v0.26.0 (sent as-is; server normalizes)", gotVersion)
	}
}

func TestReportLatestReleaseSurfacesHTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"detail":"forbidden"}`))
	}))
	defer srv.Close()

	err := New(srv.URL).ReportLatestRelease(context.Background(), "tok", "0.26.0")
	if err == nil {
		t.Fatal("expected an error on 403")
	}
	var httpErr *HTTPError
	if !errors.As(err, &httpErr) || httpErr.StatusCode != http.StatusForbidden {
		t.Errorf("want a 403 HTTPError, got %v", err)
	}
}
