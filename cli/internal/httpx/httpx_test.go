package httpx

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"
)

func TestDoDecodesJSONAndSendsAuth(t *testing.T) {
	var gotAuth, gotAccept string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		gotAccept = r.Header.Get("Accept")
		_, _ = w.Write([]byte(`{"value":"ok"}`))
	}))
	defer srv.Close()

	c := New(srv.URL+"/", time.Second)
	var out struct {
		Value string `json:"value"`
	}
	if err := c.Do(context.Background(), http.MethodGet, "/thing", "tok", nil, &out); err != nil {
		t.Fatalf("Do: %v", err)
	}
	if out.Value != "ok" {
		t.Errorf("decoded value = %q", out.Value)
	}
	if gotAuth != "Bearer tok" {
		t.Errorf("auth header = %q", gotAuth)
	}
	if gotAccept != "application/json" {
		t.Errorf("accept header = %q", gotAccept)
	}
}

func TestDoNon2xxReturnsHTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"detail":"nope"}`))
	}))
	defer srv.Close()

	err := New(srv.URL, time.Second).Do(context.Background(), http.MethodGet, "/x", "", nil, nil)
	var he *HTTPError
	if !errors.As(err, &he) {
		t.Fatalf("want *HTTPError, got %T", err)
	}
	if he.StatusCode != http.StatusForbidden {
		t.Errorf("status = %d", he.StatusCode)
	}
	if he.Detail() != "nope" {
		t.Errorf("detail = %q", he.Detail())
	}
}

func TestDoRawReturnsBody(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Accept") != "text/markdown" {
			t.Errorf("accept = %q", r.Header.Get("Accept"))
		}
		_, _ = w.Write([]byte("# hi"))
	}))
	defer srv.Close()

	body, err := New(srv.URL, time.Second).DoRaw(context.Background(), http.MethodGet, "/doc", "", "text/markdown")
	if err != nil {
		t.Fatalf("DoRaw: %v", err)
	}
	if string(body) != "# hi" {
		t.Errorf("body = %q", body)
	}
}

func TestDetailPrefersSpecificKey(t *testing.T) {
	cases := []struct{ body, want string }{
		{`{"detail":"d","title":"t"}`, "d"},
		{`{"title":"t"}`, "t"},
		{`{"error_description":"ed"}`, "ed"},
		{`{"error":"e"}`, "e"},
		{`not json`, "not json"},
	}
	for _, tc := range cases {
		e := &HTTPError{StatusCode: 400, Body: tc.body}
		if got := e.Detail(); got != tc.want {
			t.Errorf("Detail(%q) = %q, want %q", tc.body, got, tc.want)
		}
	}
}

func TestQuery(t *testing.T) {
	if got := Query(nil); got != "" {
		t.Errorf("Query(nil) = %q", got)
	}
	q := url.Values{}
	q.Set("a", "b")
	if got := Query(q); got != "?a=b" {
		t.Errorf("Query = %q", got)
	}
}

func TestBaseURLTrims(t *testing.T) {
	if got := New("  http://x/  ", time.Second).BaseURL(); got != "http://x" {
		t.Errorf("BaseURL = %q", got)
	}
}

// TestBearerRefusedOverPlaintextRemote proves SEC-1: a bearer is never sent to a
// non-loopback plaintext host. The request must fail before any dial, and an
// empty bearer to the same host is unaffected (no auth to protect).
func TestBearerRefusedOverPlaintextRemote(t *testing.T) {
	c := New("http://api.example.com", time.Second)
	err := c.Do(context.Background(), http.MethodGet, "/x", "secret-token", nil, nil)
	if err == nil {
		t.Fatal("Do with bearer over plaintext remote = nil, want rejection")
	}
	if _, rawErr := c.DoRaw(context.Background(), http.MethodGet, "/x", "secret-token", "application/json"); rawErr == nil {
		t.Fatal("DoRaw with bearer over plaintext remote = nil, want rejection")
	}
}
