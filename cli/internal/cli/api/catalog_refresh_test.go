package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// refreshFakeServer lists three sources — one disabled, one whose :sync fails,
// one fine — and records which :sync routes were called.
func refreshFakeServer(t *testing.T, failing ...string) (*httptest.Server, func() []string) {
	t.Helper()
	var mu sync.Mutex
	var synced []string
	fail := map[string]bool{}
	for _, slug := range failing {
		fail[slug] = true
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		p := r.URL.Path
		switch {
		case p == "/catalog/sources":
			_, _ = w.Write([]byte(`{"data":[` +
				`{"slug":"public","kind":"git-manifest","visibility":"public","status":"ok","enabled":false,"last_sync_at":null,"live_entries":0,"consecutive_failures":0,"sync_pending":false},` +
				`{"slug":"broken","kind":"http-index","visibility":"public","status":"ok","enabled":true,"last_sync_at":null,"live_entries":0,"consecutive_failures":0,"sync_pending":false},` +
				`{"slug":"corp","kind":"http-index","visibility":"public","status":"ok","last_sync_at":null,"live_entries":0,"consecutive_failures":0,"sync_pending":false}]}`))
		case r.Method == http.MethodPost && strings.HasSuffix(p, ":sync"):
			slug := strings.TrimSuffix(strings.TrimPrefix(p, "/catalog/sources/"), ":sync")
			mu.Lock()
			synced = append(synced, slug)
			mu.Unlock()
			if fail[slug] {
				w.WriteHeader(http.StatusInternalServerError)
				_, _ = w.Write([]byte(`{"type":"about:blank","title":"boom","status":500}`))
				return
			}
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"slug":"` + slug + `","status":"queued","forced":false,"due_at":null}`))
		default:
			t.Errorf("unexpected %s %s", r.Method, p)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, func() []string {
		mu.Lock()
		defer mu.Unlock()
		return append([]string(nil), synced...)
	}
}

// A disabled source is never asked to sync, and one failed :sync does not
// abort the rest: the refresh reports it and still succeeds.
func TestCatalogRefreshSkipsDisabledAndReportsPartialFailure(t *testing.T) {
	srv, synced := refreshFakeServer(t, "broken")
	out, err := runRegistryCmd(t, srv, "catalog", "refresh", "--json")
	if err != nil {
		t.Fatalf("refresh: %v", err)
	}
	if got := strings.Join(synced(), ","); got != "broken,corp" {
		t.Fatalf("synced %q, want broken,corp (public is disabled)", got)
	}
	doc := decodeOneDocument(t, out)
	fields, _ := doc["fields"].(map[string]any)
	if fields["sources"] != float64(1) || fields["skipped"] != float64(1) {
		t.Fatalf("fields = %v", fields)
	}
	failed, _ := fields["failed"].([]any)
	if len(failed) != 1 || failed[0] != "broken" {
		t.Fatalf("failed = %v", fields["failed"])
	}
}

// When every requested sync fails the refresh fails with the first error.
func TestCatalogRefreshFailsWhenEverySyncFails(t *testing.T) {
	srv, synced := refreshFakeServer(t, "broken", "corp")
	if _, err := runRegistryCmd(t, srv, "catalog", "refresh", "--json"); err == nil {
		t.Fatal("expected an error when every sync request fails")
	}
	if got := strings.Join(synced(), ","); got != "broken,corp" {
		t.Fatalf("synced %q, want broken,corp", got)
	}
}

func TestCatalogBrowserRefreshPartial(t *testing.T) {
	m := &catalogBrowser{refreshing: true}
	m.onRefresh(catRefreshMsg{count: 1, failed: []string{"broken"}})
	if !strings.Contains(m.status, "broken") || !m.loading {
		t.Errorf("partial refresh status %q (loading=%v)", m.status, m.loading)
	}
}
