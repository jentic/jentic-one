package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

func TestCredentialsList_WalksPages(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/credentials") {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data":     []map[string]any{{"credential_id": "cred_1", "name": "k", "active": true, "provider": "static", "api": map[string]any{"name": "x", "vendor": "y", "version": "1"}, "created_at": "2026-01-01T00:00:00Z", "type": "api_key"}},
			"has_more": false,
		})
	}))
	defer srv.Close()
	seedActiveContextTo(t, srv.URL)

	if err := runJentic(t, "credentials", "list"); err != nil {
		t.Fatalf("credentials list: %v", err)
	}
}

func TestBuildImportSource_URLvsFile(t *testing.T) {
	// A URL becomes an ApiSourceUrl, and --submitted-by is wired through (GEN-20).
	item, err := buildImportSource("https://example.com/openapi.yaml", "acme", "", "", "renton")
	if err != nil {
		t.Fatal(err)
	}
	if src, uerr := item.AsApiSourceUrl(); uerr != nil || src.Url != "https://example.com/openapi.yaml" {
		t.Errorf("expected ApiSourceUrl, got err=%v src=%+v", uerr, src)
	} else if src.SubmittedBy == nil || *src.SubmittedBy != "renton" {
		t.Errorf("submitted_by not wired: %+v", src.SubmittedBy)
	}

	// A local file becomes an ApiSourceInline with the file content.
	dir := t.TempDir()
	p := dir + "/spec.yaml"
	if werr := os.WriteFile(p, []byte("openapi: 3.0.0"), 0o600); werr != nil {
		t.Fatal(werr)
	}
	item, err = buildImportSource(p, "", "", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if src, ierr := item.AsApiSourceInline(); ierr != nil || src.Content != "openapi: 3.0.0" || src.Filename != "spec.yaml" {
		t.Errorf("expected inline source, got err=%v src=%+v", ierr, src)
	} else if src.SubmittedBy != nil {
		t.Errorf("submitted_by should be nil when --submitted-by unset, got %+v", src.SubmittedBy)
	}

	// A missing file is an error.
	if _, ferr := buildImportSource(dir+"/nope.yaml", "", "", "", ""); ferr == nil {
		t.Error("expected an error for a missing file")
	}
}

func TestApisImport_DryRunDoesNotCallServer(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("dry-run must not reach the server")
		w.WriteHeader(http.StatusAccepted)
	}))
	defer srv.Close()
	seedActiveContextTo(t, srv.URL)

	dir := t.TempDir()
	p := dir + "/spec.yaml"
	if err := os.WriteFile(p, []byte("openapi: 3.0.0"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := runJentic(t, "apis", "import", p, "--dry-run"); err != nil {
		t.Fatalf("apis import --dry-run: %v", err)
	}
}
