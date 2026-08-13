// Package httpx is the shared HTTP transport for the Jentic control-plane API
// clients (apiclient, authclient, catalogclient, searchclient, accessclient, …).
// It owns the base client, the JSON request/response plumbing, and the
// problem-details error type so each typed client only has to describe its own
// endpoints and payloads.
package httpx

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/client/auth"
)

// maxResponseBytes caps how much of a response body we buffer, so a hostile or
// misbehaving server the operator points at cannot OOM the CLI. 64 MiB is far
// larger than any legitimate control-plane response (the OpenAPI doc is ~1 MiB).
const maxResponseBytes = 64 << 20

// readBody reads up to maxResponseBytes+1 and errors if the cap is exceeded, so
// an over-large body is rejected rather than silently truncated.
func readBody(r io.Reader) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(r, maxResponseBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > maxResponseBytes {
		return nil, fmt.Errorf("response body exceeds %d bytes", maxResponseBytes)
	}
	return data, nil
}

// Client talks to a single base URL with a bounded HTTP client.
type Client struct {
	baseURL string
	http    *http.Client
}

// New returns a client for the given base URL (trailing slash trimmed) with the
// supplied request timeout.
func New(baseURL string, timeout time.Duration) *Client {
	return &Client{
		baseURL: strings.TrimRight(strings.TrimSpace(baseURL), "/"),
		http:    &http.Client{Timeout: timeout},
	}
}

// BaseURL returns the normalized base URL.
func (c *Client) BaseURL() string { return c.baseURL }

// HTTPError captures a non-2xx response.
type HTTPError struct {
	StatusCode int
	Body       string
}

func (e *HTTPError) Error() string {
	return fmt.Sprintf("http %d: %s", e.StatusCode, e.Detail())
}

// Detail extracts a problem-details message, preferring the most specific key.
func (e *HTTPError) Detail() string {
	p := e.Fields()
	for _, k := range []string{"detail", "title", "error_description", "error"} {
		if v, ok := p[k].(string); ok && v != "" {
			return v
		}
	}
	return e.Body
}

// Fields decodes the problem-details body into a map so callers can read
// extension members (e.g. existing_request_id, agent_directive). Returns an
// empty map when the body is not a JSON object.
func (e *HTTPError) Fields() map[string]any {
	var p map[string]any
	if json.Unmarshal([]byte(e.Body), &p) == nil {
		return p
	}
	return map[string]any{}
}

// Do issues a JSON request. When body is non-nil it is marshalled and sent as
// the request body; when out is non-nil a 2xx response body is decoded into it.
func (c *Client) Do(ctx context.Context, method, path, bearer string, body, out any) error {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(data)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, reader)
	if err != nil {
		return err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("Accept", "application/json")
	if bearer != "" {
		// SEC-1: never send the agent bearer over plaintext to a non-loopback
		// host. Mirrors the SDK's AttachAuth guard so the hand-rolled clients
		// hold the token to the same https-or-loopback invariant.
		if err := auth.RequireSecureURL(c.baseURL + path); err != nil {
			return err
		}
		req.Header.Set("Authorization", "Bearer "+bearer)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	data, err := readBody(resp.Body)
	if err != nil {
		return fmt.Errorf("read %s response: %w", path, err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return &HTTPError{StatusCode: resp.StatusCode, Body: strings.TrimSpace(string(data))}
	}
	if out != nil && len(data) > 0 {
		if err := json.Unmarshal(data, out); err != nil {
			return fmt.Errorf("decode %s response: %w", path, err)
		}
	}
	return nil
}

// DoRaw issues a GET and returns the raw response body for endpoints that
// content-negotiate non-JSON bodies (spec download, inspect markdown/yaml).
func (c *Client) DoRaw(ctx context.Context, method, path, bearer, accept string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", accept)
	if bearer != "" {
		// SEC-1: guard the bearer against plaintext to a remote host (see Do).
		if err := auth.RequireSecureURL(c.baseURL + path); err != nil {
			return nil, err
		}
		req.Header.Set("Authorization", "Bearer "+bearer)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	data, err := readBody(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read %s response: %w", path, err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, &HTTPError{StatusCode: resp.StatusCode, Body: strings.TrimSpace(string(data))}
	}
	return data, nil
}

// Query renders url.Values as a "?a=b" suffix, or "" when empty.
func Query(q url.Values) string {
	if len(q) == 0 {
		return ""
	}
	return "?" + q.Encode()
}
