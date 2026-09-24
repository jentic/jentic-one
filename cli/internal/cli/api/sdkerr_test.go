package api

import (
	"errors"
	"net/http"
	"testing"
)

// fakeResp is a stand-in for a generated `*HTTPResp` wrapper: the adapter only
// needs StatusCode() + GetBody().
type fakeResp struct {
	code int
	body []byte
}

func (r fakeResp) StatusCode() int { return r.code }
func (r fakeResp) GetBody() []byte { return r.body }

// TestHTTPErrorForSuccess: a 2xx response is not an error.
func TestHTTPErrorForSuccess(t *testing.T) {
	for _, code := range []int{200, 201, 202, 204} {
		if err := apiErrorFor(fakeResp{code: code}, nil); err != nil {
			t.Errorf("status %d should be nil, got %v", code, err)
		}
	}
}

// TestHTTPErrorForTransport: a transport error is returned verbatim, ahead of
// any status inspection.
func TestHTTPErrorForTransport(t *testing.T) {
	sentinel := errors.New("dial tcp: connection refused")
	if err := apiErrorFor(fakeResp{code: 200}, sentinel); !errors.Is(err, sentinel) {
		t.Errorf("transport error should pass through, got %v", err)
	}
}

// TestHTTPErrorForNon2xx: a non-2xx yields a typed *HTTPError whose StatusCode and
// Detail() (problem-details precedence) match the old httpx.HTTPError contract.
func TestHTTPErrorForNon2xx(t *testing.T) {
	body := `{"type":"forbidden","title":"Forbidden","detail":"needs org:admin","status":403}`
	err := apiErrorFor(fakeResp{code: http.StatusForbidden, body: []byte(body)}, nil)
	var ae *HTTPError
	if !errors.As(err, &ae) {
		t.Fatalf("want *HTTPError, got %v", err)
	}
	if ae.StatusCode != http.StatusForbidden {
		t.Errorf("StatusCode = %d, want 403", ae.StatusCode)
	}
	if ae.Detail() != "needs org:admin" {
		t.Errorf("Detail() = %q, want the `detail` member", ae.Detail())
	}
	if v, _ := ae.Fields()["title"].(string); v != "Forbidden" {
		t.Errorf("Fields() should expose extension/standard members, got %v", ae.Fields())
	}
}

// TestHTTPErrorDetailFallback: with no problem-details keys, Detail() falls back
// to the raw body (matching httpx.HTTPError.Detail).
func TestHTTPErrorDetailFallback(t *testing.T) {
	ae := &HTTPError{StatusCode: 500, Body: "upstream boom"}
	if ae.Detail() != "upstream boom" {
		t.Errorf("Detail() = %q, want the raw body", ae.Detail())
	}
	if len(ae.Fields()) != 0 {
		t.Errorf("non-JSON body should yield empty Fields(), got %v", ae.Fields())
	}
}
