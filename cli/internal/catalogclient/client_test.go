package catalogclient

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func contains(s, sub string) bool { return strings.Contains(s, sub) }

func TestListSendsParamsAndDecodes(t *testing.T) {
	var gotQuery string
	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		gotAuth = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"data":[{"api_id":"stripe.com","vendor":"stripe.com","spec_url":"https://u","registered":true,"update_available":true,
				"_links":{"self":"/catalog/stripe.com","operations":"o","import":"i","github":"g"}}],
			"catalog_total":5,"registered_count":1,"outdated_count":2,"manifest_age_seconds":60,
			"has_more":true,"next_cursor":"c1"}`))
	}))
	defer srv.Close()

	c := New(srv.URL)
	res, err := c.List(context.Background(), "tok", ListParams{Q: "pay", Registered: true, Outdated: true, IncludeSnoozed: true, Limit: 25})
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if gotAuth != "Bearer tok" {
		t.Errorf("auth header = %q", gotAuth)
	}
	for _, want := range []string{"q=pay", "registered_only=true", "outdated_only=true", "include_snoozed=true", "limit=25"} {
		if !contains(gotQuery, want) {
			t.Errorf("query %q missing %q", gotQuery, want)
		}
	}
	if len(res.Data) != 1 || res.Data[0].APIID != "stripe.com" || !res.Data[0].Registered {
		t.Errorf("bad data: %+v", res.Data)
	}
	if !res.Data[0].UpdateAvailable {
		t.Errorf("update_available not decoded: %+v", res.Data[0])
	}
	if res.OutdatedCount != 2 {
		t.Errorf("outdated_count = %d, want 2", res.OutdatedCount)
	}
	if res.Data[0].Links.Github != "g" {
		t.Errorf("links not decoded: %+v", res.Data[0].Links)
	}
	if res.ManifestAgeSeconds == nil || *res.ManifestAgeSeconds != 60 {
		t.Errorf("manifest age = %v", res.ManifestAgeSeconds)
	}
	if !res.HasMore || res.NextCursor != "c1" {
		t.Errorf("pagination = %v %q", res.HasMore, res.NextCursor)
	}
}

func TestGetUsesRawSlashPath(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		_, _ = w.Write([]byte(`{"api_id":"googleapis.com/admin","vendor":"googleapis.com"}`))
	}))
	defer srv.Close()

	c := New(srv.URL)
	entry, err := c.Get(context.Background(), "tok", "googleapis.com/admin")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if gotPath != "/catalog/googleapis.com/admin" {
		t.Errorf("path = %q, want unencoded slash", gotPath)
	}
	if entry.APIID != "googleapis.com/admin" {
		t.Errorf("api_id = %q", entry.APIID)
	}
}

func TestPreviewDecodesParamLocation(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/catalog/stripe.com/operations" {
			t.Errorf("path = %q", r.URL.Path)
		}
		_, _ = w.Write([]byte(`{"data":[{"method":"GET","path":"/v1/charges","summary":"List",
			"parameters":[{"name":"limit","in":"query","required":false,"description":"n"}],
			"security":["BearerAuth"],"tags":["charges"]}],
			"total":1,"offset":0,"truncated":false,
			"info":{"title":"Stripe","version":"2024"},"security_schemes":{}}`))
	}))
	defer srv.Close()

	c := New(srv.URL)
	p, err := c.Preview(context.Background(), "tok", "stripe.com", 0, 50, "")
	if err != nil {
		t.Fatalf("Preview: %v", err)
	}
	if len(p.Data) != 1 || p.Data[0].Path != "/v1/charges" {
		t.Fatalf("bad ops: %+v", p.Data)
	}
	if p.Data[0].Parameters[0].Location != "query" {
		t.Errorf("param location not decoded from 'in': %+v", p.Data[0].Parameters[0])
	}
	if p.Info.Title != "Stripe" {
		t.Errorf("info = %+v", p.Info)
	}
}

func TestImportAndPromotePaths(t *testing.T) {
	var importPath, promotePath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && contains(r.URL.Path, ":import"):
			importPath = r.URL.Path
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"job_id":"job_1","status":"queued"}`))
		case r.Method == http.MethodPost && contains(r.URL.Path, ":promote"):
			promotePath = r.URL.Path
			_, _ = w.Write([]byte(`{}`))
		default:
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
		}
	}))
	defer srv.Close()

	c := New(srv.URL)
	jobID, err := c.Import(context.Background(), "tok", "googleapis.com/admin")
	if err != nil {
		t.Fatalf("Import: %v", err)
	}
	if jobID != "job_1" {
		t.Errorf("job id = %q", jobID)
	}
	if importPath != "/catalog/googleapis.com/admin:import" {
		t.Errorf("import path = %q", importPath)
	}
	if err := c.Promote(context.Background(), "tok", "stripe.com", "main", "2024", "rev_1"); err != nil {
		t.Fatalf("Promote: %v", err)
	}
	if promotePath != "/apis/stripe.com/main/2024/revisions/rev_1:promote" {
		t.Errorf("promote path = %q", promotePath)
	}
}

func TestHTTPErrorDetail(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"catalog entry not found"}`))
	}))
	defer srv.Close()

	c := New(srv.URL)
	_, err := c.Get(context.Background(), "tok", "missing")
	var he *HTTPError
	if !errors.As(err, &he) {
		t.Fatalf("want *HTTPError, got %T", err)
	}
	if he.StatusCode != http.StatusNotFound {
		t.Errorf("status = %d", he.StatusCode)
	}
	if he.Detail() != "catalog entry not found" {
		t.Errorf("detail = %q", he.Detail())
	}
}
