package api

// mcp_http_gate_test.go pins the platform gate in front of the SDK handler:
// strict Origin validation (spoof → 403), the 401 token gate with the
// pre-auth discovery whitelist, and the stateless transport's 405 GET.

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
)

const toolsListFrame = `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`

// postFrame POSTs one JSON-RPC frame to a daemon with optional extra
// headers, returning the response status and headers (body closed).
func postFrame(t *testing.T, url, body string, headers map[string]string) (int, http.Header) {
	t.Helper()
	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, url+mcpHTTPPath, strings.NewReader(body))
	if err != nil {
		t.Fatalf("build request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	defer func() { _ = resp.Body.Close() }()
	_, _ = io.Copy(io.Discard, resp.Body)
	return resp.StatusCode, resp.Header
}

func TestMCPHTTP_OriginSpoofForbidden(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, nil)
	daemon := startHTTPDaemon(context.Background(), t, s, nil, []string{"https://allowed.example"})

	cases := []struct {
		name, origin string
		wantStatus   int
	}{
		{"spoofed origin is refused", "https://evil.example", http.StatusForbidden},
		{"null origin is refused", "null", http.StatusForbidden},
		{"loopback-prefixed DNS name is refused", "http://127.0.0.1.evil.example", http.StatusForbidden},
		{"loopback origin passes", "http://127.0.0.1:6274", http.StatusOK},
		{"localhost origin passes", "http://localhost:6274", http.StatusOK},
		{"explicitly allowed origin passes", "https://allowed.example", http.StatusOK},
		{"absent origin passes", "", http.StatusOK},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			headers := map[string]string{}
			if tc.origin != "" {
				headers["Origin"] = tc.origin
			}
			status, _ := postFrame(t, daemon.URL, toolsListFrame, headers)
			if status != tc.wantStatus {
				t.Errorf("status = %d, want %d", status, tc.wantStatus)
			}
		})
	}
}

func TestMCPHTTP_GETIsMethodNotAllowed(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, nil)
	daemon := startHTTPDaemon(context.Background(), t, s, nil, nil)

	req, _ := http.NewRequestWithContext(context.Background(), http.MethodGet, daemon.URL+mcpHTTPPath, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("GET status = %d, want 405 (stateless daemon serves POST only)", resp.StatusCode)
	}
	if allow := resp.Header.Get("Allow"); allow != "POST" {
		t.Errorf("Allow = %q, want POST", allow)
	}
}

// TestMCPHTTP_TokenGate pins the TCP auth posture: a token-gated daemon
// serves the pre-auth discovery whitelist without a credential, refuses
// everything else with a 401 challenge, and constant-time-accepts the token.
func TestMCPHTTP_TokenGate(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("XDG_STATE_HOME", t.TempDir())

	s := newTestMCPServer(t, nil)
	daemon := startHTTPDaemon(context.Background(), t, s, []byte("tok_secret"), nil)

	const callFrame = `{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_started"}}`

	t.Run("tools/list needs no credential", func(t *testing.T) {
		if status, _ := postFrame(t, daemon.URL, toolsListFrame, nil); status != http.StatusOK {
			t.Errorf("pre-auth tools/list status = %d, want 200", status)
		}
	})
	t.Run("tools/call without a credential is challenged", func(t *testing.T) {
		status, header := postFrame(t, daemon.URL, callFrame, nil)
		if status != http.StatusUnauthorized {
			t.Errorf("status = %d, want 401", status)
		}
		if !strings.HasPrefix(header.Get("WWW-Authenticate"), "Bearer") {
			t.Errorf("WWW-Authenticate = %q, want a Bearer challenge", header.Get("WWW-Authenticate"))
		}
	})
	t.Run("a wrong token is refused", func(t *testing.T) {
		status, _ := postFrame(t, daemon.URL, callFrame, map[string]string{"Authorization": "Bearer wrong"})
		if status != http.StatusUnauthorized {
			t.Errorf("status = %d, want 401", status)
		}
	})
	t.Run("the right token serves the call", func(t *testing.T) {
		status, _ := postFrame(t, daemon.URL, callFrame, map[string]string{"Authorization": "Bearer tok_secret"})
		if status != http.StatusOK {
			t.Errorf("status = %d, want 200", status)
		}
	})
	t.Run("GET with the token is 405, without it 401", func(t *testing.T) {
		req, _ := http.NewRequestWithContext(context.Background(), http.MethodGet, daemon.URL+mcpHTTPPath, nil)
		req.Header.Set("Authorization", "Bearer tok_secret")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("GET: %v", err)
		}
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusMethodNotAllowed {
			t.Errorf("authenticated GET status = %d, want 405", resp.StatusCode)
		}
		req2, _ := http.NewRequestWithContext(context.Background(), http.MethodGet, daemon.URL+mcpHTTPPath, nil)
		resp2, err := http.DefaultClient.Do(req2)
		if err != nil {
			t.Fatalf("GET: %v", err)
		}
		_ = resp2.Body.Close()
		if resp2.StatusCode != http.StatusUnauthorized {
			t.Errorf("unauthenticated GET status = %d, want 401", resp2.StatusCode)
		}
	})
}

func TestAllPreAuthMethods(t *testing.T) {
	cases := map[string]bool{
		`{"jsonrpc":"2.0","id":1,"method":"tools/list"}`:                             true,
		`{"jsonrpc":"2.0","id":1,"method":"ping"}`:                                   true,
		`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"execute"}}`: false,
		`{"jsonrpc":"2.0","id":1,"method":"resources/read"}`:                         false,
		`[{"method":"tools/list"},{"method":"ping"}]`:                                true,
		`[{"method":"tools/list"},{"method":"tools/call"}]`:                          false,
		`[]`:                       false,
		`not json`:                 false,
		`{"jsonrpc":"2.0","id":1}`: false,
	}
	for body, want := range cases {
		if got := allPreAuthMethods([]byte(body)); got != want {
			t.Errorf("allPreAuthMethods(%s) = %v, want %v", body, got, want)
		}
	}
}
