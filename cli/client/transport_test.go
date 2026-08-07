package client

import (
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
)

// withClientConfigDir isolates the XDG config/state dirs so token/key files land
// in a temp location for the duration of the test.
func withClientConfigDir(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(dir, "config"))
	t.Setenv("XDG_STATE_HOME", filepath.Join(dir, "state"))
}

// fakeRT is a scripted RoundTripper: it returns the next scripted response/error
// per call and records how many times it was invoked.
type fakeRT struct {
	calls   int32
	respond func(attempt int, req *http.Request) (*http.Response, error)
}

func (f *fakeRT) RoundTrip(req *http.Request) (*http.Response, error) {
	n := int(atomic.AddInt32(&f.calls, 1))
	return f.respond(n-1, req)
}

func resp(status int, hdr map[string]string) *http.Response {
	h := http.Header{}
	for k, v := range hdr {
		h.Set(k, v)
	}
	return &http.Response{
		StatusCode: status,
		Header:     h,
		Body:       io.NopCloser(strings.NewReader("{}")),
	}
}

// shrinkRetryKnobs makes backoff instant so tests don't sleep real seconds.
func shrinkRetryKnobs(t *testing.T) {
	t.Helper()
	oldDelay, oldAfter := retryBaseDelay, maxRetryAfter
	retryBaseDelay = time.Millisecond
	maxRetryAfter = 5 * time.Second
	t.Cleanup(func() { retryBaseDelay, maxRetryAfter = oldDelay, oldAfter })
}

func newReq(t *testing.T, method, url, body string) *http.Request {
	t.Helper()
	var r io.Reader
	if body != "" {
		r = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, url, r)
	if err != nil {
		t.Fatal(err)
	}
	return req
}

// closeResp drains and closes a response body so the bodyclose linter is
// satisfied and connections are freed. Safe on nil.
func closeResp(r *http.Response) {
	if r != nil && r.Body != nil {
		_, _ = io.Copy(io.Discard, r.Body)
		_ = r.Body.Close()
	}
}

// TestRetry_5xxIdempotentRetriesThenSurfaces: a GET retries on 500 up to the cap
// and returns the final response.
func TestRetry_5xxIdempotentRetriesThenSurfaces(t *testing.T) {
	shrinkRetryKnobs(t)
	fake := &fakeRT{respond: func(_ int, _ *http.Request) (*http.Response, error) {
		return resp(http.StatusInternalServerError, nil), nil
	}}
	rt := newRetryTransport(fake, auth.Credentials{InjectedBearerToken: "at_x"})

	r, err := rt.RoundTrip(newReq(t, http.MethodGet, "https://x.test/thing", ""))
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	defer closeResp(r)
	if r.StatusCode != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500", r.StatusCode)
	}
	// 1 initial + maxIdempotentRetries retries.
	if got := int(fake.calls); got != maxIdempotentRetries+1 {
		t.Errorf("calls = %d, want %d", got, maxIdempotentRetries+1)
	}
}

// TestRetry_5xxNonIdempotentNotRetried: a plain POST (no Idempotency-Key) is never
// blind-retried on 500.
func TestRetry_5xxNonIdempotentNotRetried(t *testing.T) {
	shrinkRetryKnobs(t)
	fake := &fakeRT{respond: func(_ int, _ *http.Request) (*http.Response, error) {
		return resp(http.StatusBadGateway, nil), nil
	}}
	rt := newRetryTransport(fake, auth.Credentials{InjectedBearerToken: "at_x"})

	r, err := rt.RoundTrip(newReq(t, http.MethodPost, "https://x.test/exec", `{"a":1}`))
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	closeResp(r)
	if got := int(fake.calls); got != 1 {
		t.Errorf("plain POST retried on 5xx: calls = %d, want 1", got)
	}
}

// TestRetry_5xxPostWithIdempotencyKeyRetried: a POST carrying an Idempotency-Key
// IS retry-safe (the broker dedupes on it).
func TestRetry_5xxPostWithIdempotencyKeyRetried(t *testing.T) {
	shrinkRetryKnobs(t)
	fake := &fakeRT{respond: func(attempt int, _ *http.Request) (*http.Response, error) {
		if attempt == 0 {
			return resp(http.StatusServiceUnavailable, nil), nil
		}
		return resp(http.StatusOK, nil), nil
	}}
	rt := newRetryTransport(fake, auth.Credentials{InjectedBearerToken: "at_x"})

	req := newReq(t, http.MethodPost, "https://x.test/exec", `{"a":1}`)
	req.Header.Set("Idempotency-Key", "key-1")
	r, err := rt.RoundTrip(req)
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	defer closeResp(r)
	if r.StatusCode != http.StatusOK {
		t.Errorf("status = %d, want 200 after retry", r.StatusCode)
	}
	if got := int(fake.calls); got != 2 {
		t.Errorf("calls = %d, want 2", got)
	}
}

// TestRetry_429HonorsRetryAfterWithinBudget: a 429 with a small Retry-After is
// waited out and retried once.
func TestRetry_429HonorsRetryAfterWithinBudget(t *testing.T) {
	shrinkRetryKnobs(t)
	fake := &fakeRT{respond: func(attempt int, _ *http.Request) (*http.Response, error) {
		if attempt == 0 {
			return resp(http.StatusTooManyRequests, map[string]string{"Retry-After": "0"}), nil
		}
		return resp(http.StatusOK, nil), nil
	}}
	rt := newRetryTransport(fake, auth.Credentials{InjectedBearerToken: "at_x"})

	r, err := rt.RoundTrip(newReq(t, http.MethodGet, "https://x.test/thing", ""))
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	defer closeResp(r)
	if r.StatusCode != http.StatusOK {
		t.Errorf("status = %d, want 200", r.StatusCode)
	}
	if got := int(fake.calls); got != 2 {
		t.Errorf("calls = %d, want 2", got)
	}
}

// TestRetry_429OverBudgetSurfaces: a Retry-After beyond the cap is surfaced, not
// waited on.
func TestRetry_429OverBudgetSurfaces(t *testing.T) {
	shrinkRetryKnobs(t) // maxRetryAfter = 5s
	fake := &fakeRT{respond: func(_ int, _ *http.Request) (*http.Response, error) {
		return resp(http.StatusTooManyRequests, map[string]string{"Retry-After": "3600"}), nil
	}}
	rt := newRetryTransport(fake, auth.Credentials{InjectedBearerToken: "at_x"})

	r, err := rt.RoundTrip(newReq(t, http.MethodGet, "https://x.test/thing", ""))
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	defer closeResp(r)
	if r.StatusCode != http.StatusTooManyRequests {
		t.Errorf("status = %d, want 429 surfaced", r.StatusCode)
	}
	if got := int(fake.calls); got != 1 {
		t.Errorf("calls = %d, want 1 (no wait)", got)
	}
}

// TestRetry_401InvalidatesTokenForExchangeableCreds: a 401 on the disk
// (JWT-bearer) path invalidates the cached token so the NEXT request re-exchanges.
// Here the stub base URL is unresolvable, so the inline re-exchange fails and the
// transport surfaces the original 401 — but the invalidation (the important state
// change) must still have happened exactly once.
func TestRetry_401InvalidatesTokenForExchangeableCreds(t *testing.T) {
	shrinkRetryKnobs(t)
	withClientConfigDir(t)

	ref := auth.IdentityRef{Identity: "a", Environment: "e"}
	if err := auth.SaveTokens(ref, &auth.TokenSet{AccessToken: "tok-old", ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}

	fake := &fakeRT{respond: func(_ int, _ *http.Request) (*http.Response, error) {
		return resp(http.StatusUnauthorized, nil), nil
	}}
	// base URL points at an unresolvable host so the re-exchange fails fast; the
	// policy then surfaces the 401 rather than looping.
	rt := newRetryTransport(fake, auth.Credentials{
		IdentityName:    "a",
		EnvironmentName: "e",
		BaseURL:         "https://nonexistent.invalid",
	})

	req := newReq(t, http.MethodGet, "https://nonexistent.invalid/thing", "")
	req.Header.Set("Authorization", "Bearer tok-old")
	r, err := rt.RoundTrip(req)
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	defer closeResp(r)
	if r.StatusCode != http.StatusUnauthorized {
		t.Errorf("status = %d, want 401 surfaced after failed re-exchange", r.StatusCode)
	}
	if _, rerr := auth.ReadTokens(ref); rerr == nil {
		t.Error("token should have been invalidated by the 401 branch")
	}
}

// TestRetry_401NotRetriedForFixedCreds: an injected token (or API key) is a fixed
// credential — a 401 is a hard denial and must NOT loop.
func TestRetry_401NotRetriedForFixedCreds(t *testing.T) {
	shrinkRetryKnobs(t)
	fake := &fakeRT{respond: func(_ int, _ *http.Request) (*http.Response, error) {
		return resp(http.StatusUnauthorized, nil), nil
	}}
	rt := newRetryTransport(fake, auth.Credentials{InjectedBearerToken: "at_x"})

	r, err := rt.RoundTrip(newReq(t, http.MethodGet, "https://x.test/thing", ""))
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	defer closeResp(r)
	if r.StatusCode != http.StatusUnauthorized {
		t.Errorf("status = %d, want 401", r.StatusCode)
	}
	if got := int(fake.calls); got != 1 {
		t.Errorf("fixed-cred 401 retried: calls = %d, want 1", got)
	}
}
