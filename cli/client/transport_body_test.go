package client

import (
	"bytes"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/client/auth"
)

// poisonBody fails the test if the transport reads the original request body
// instead of replaying through GetBody.
type poisonBody struct{ t *testing.T }

func (p poisonBody) Read([]byte) (int, error) {
	p.t.Error("transport read req.Body; a request with GetBody must be replayed via GetBody, not re-buffered")
	return 0, io.EOF
}
func (poisonBody) Close() error { return nil }

// TestRetryTransportReplaysViaGetBody pins the zero-copy retry rewind (#1320):
// a request carrying a known-length GetBody is never re-read into a second
// buffer; every attempt (including retries) gets a fresh, complete body from
// GetBody.
func TestRetryTransportReplaysViaGetBody(t *testing.T) {
	shrinkRetryKnobs(t)
	payload := strings.Repeat("multipart-bytes-", 64)
	var opens int
	var seen []string
	rt := &fakeRT{respond: func(attempt int, req *http.Request) (*http.Response, error) {
		b, err := io.ReadAll(req.Body)
		if err != nil {
			t.Fatalf("read attempt %d body: %v", attempt, err)
		}
		if req.ContentLength != int64(len(payload)) {
			t.Errorf("attempt %d ContentLength = %d, want %d", attempt, req.ContentLength, len(payload))
		}
		seen = append(seen, string(b))
		if attempt == 0 {
			return resp(http.StatusServiceUnavailable, nil), nil
		}
		return resp(http.StatusOK, nil), nil
	}}
	req, err := http.NewRequest(http.MethodPost, "https://broker.example/x", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Idempotency-Key", "k1") // makes the POST retry-safe
	req.Body = poisonBody{t}
	req.ContentLength = int64(len(payload))
	req.GetBody = func() (io.ReadCloser, error) {
		opens++
		return io.NopCloser(strings.NewReader(payload)), nil
	}

	got, err := newRetryTransport(rt, auth.Credentials{InjectedBearerToken: "at_x"}).RoundTrip(req)
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	_ = got.Body.Close()
	if len(seen) != 2 {
		t.Fatalf("attempts = %d, want 2 (one 503 retry)", len(seen))
	}
	for i, s := range seen {
		if s != payload {
			t.Errorf("attempt %d body = %d bytes, want the full %d-byte payload", i, len(s), len(payload))
		}
	}
	if opens != 2 {
		t.Errorf("GetBody opens = %d, want one per attempt (2)", opens)
	}
}

// TestRetryTransportBuffersBodyWithoutGetBody keeps the fallback: a streaming
// body (no GetBody) is read once, bounded, and replayed intact on retry.
func TestRetryTransportBuffersBodyWithoutGetBody(t *testing.T) {
	shrinkRetryKnobs(t)
	payload := "streamed-body"
	var seen []string
	rt := &fakeRT{respond: func(attempt int, req *http.Request) (*http.Response, error) {
		b, _ := io.ReadAll(req.Body)
		seen = append(seen, string(b))
		if attempt == 0 {
			return resp(http.StatusBadGateway, nil), nil
		}
		return resp(http.StatusOK, nil), nil
	}}
	req, err := http.NewRequest(http.MethodPost, "https://broker.example/x", io.MultiReader(strings.NewReader(payload)))
	if err != nil {
		t.Fatal(err)
	}
	if req.GetBody != nil {
		t.Fatal("precondition: a MultiReader body must not carry GetBody")
	}
	req.Header.Set("Idempotency-Key", "k1")
	got, err := newRetryTransport(rt, auth.Credentials{InjectedBearerToken: "at_x"}).RoundTrip(req)
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	_ = got.Body.Close()
	if len(seen) != 2 || seen[0] != payload || seen[1] != payload {
		t.Errorf("attempt bodies = %q, want the payload twice", seen)
	}
}

// TestBufferBodyOversizeIsRequestError pins the request-path wording: an
// oversize outbound body is refused with ErrRequestBodyTooLarge — never the
// response-side ErrBodyTooLarge, which would misreport an upload as a bad
// response — on both the GetBody and the read-once path.
func TestBufferBodyOversizeIsRequestError(t *testing.T) {
	cases := map[string]func() *http.Request{
		"getbody known length": func() *http.Request {
			req, _ := http.NewRequest(http.MethodPost, "https://broker.example/x", bytes.NewReader([]byte("x")))
			req.ContentLength = MaxBodyBytes + 1
			return req
		},
		"streaming body": func() *http.Request {
			req, _ := http.NewRequest(http.MethodPost, "https://broker.example/x",
				io.MultiReader(io.LimitReader(zeroReader{}, MaxBodyBytes+1)))
			return req
		},
	}
	for name, mk := range cases {
		t.Run(name, func(t *testing.T) {
			_, _, err := bufferBody(mk())
			if !errors.Is(err, ErrRequestBodyTooLarge) {
				t.Fatalf("err = %v, want ErrRequestBodyTooLarge", err)
			}
			if errors.Is(err, ErrBodyTooLarge) || strings.Contains(err.Error(), "response") {
				t.Errorf("request-path error must not read as a response failure: %v", err)
			}
		})
	}
}

type zeroReader struct{}

func (zeroReader) Read(p []byte) (int, error) {
	clear(p)
	return len(p), nil
}
