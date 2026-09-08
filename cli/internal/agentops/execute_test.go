package agentops

// execute_test.go pins the transport-failure behaviors no golden can pin:
// the dial-level branch of Do (a real dial failure's error string embeds an
// ephemeral port and OS-specific text, never byte-stable) and the pre-send
// classification the MCP execute surface keys its retryable hint off — only
// provably PRE-SEND failures (the upstream cannot have received the request)
// may invite a blind retry of an unkeyed mutating call.

import (
	"context"
	"crypto/tls"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"syscall"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// The two tests below cover Do's dial-level transport branch — the one execute
// branch no golden can pin (a real dial failure's error string embeds an
// ephemeral port and OS-specific text, never byte-stable), so it is asserted
// here by fields. Both use POST without an
// Idempotency-Key so the broker transport's idempotent-retry backoff
// (client/transport.go) gives the dial exactly one attempt instead of
// sleeping through three.

// TestDo_DialTLSMismatchAgainstLoopbackBroker pins the AGT-23/UX-4 half of the
// branch: an https scheme dialed against a plain-HTTP loopback server
// reproduces the exact local papercut signature ("server gave HTTP response to
// HTTPS client"), which must map to a coded TRANSPORT_ERROR carrying the
// loopback TLS-mismatch actionable — not a bare, unactionable error.
func TestDo_DialTLSMismatchAgainstLoopbackBroker(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(srv.Close)

	// srv serves plain HTTP on 127.0.0.1; dialing it with the https scheme
	// (which passes SEC-1, so BuildRequest cannot short-circuit) forces the
	// mismatch inside Do itself.
	req, err := BuildRequest(context.Background(), ExecuteRequest{
		Method:       http.MethodPost,
		Path:         "/v1/pets",
		BrokerScheme: "https",
		BrokerHost:   srv.Listener.Addr().String(),
		Token:        "tok_abc",
	})
	if err != nil {
		t.Fatalf("BuildRequest: %v", err)
	}

	_, err = Do(req)
	if err == nil {
		t.Fatal("Do against an https→http mismatch must fail")
	}
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("Do returned %T (%v), want *ux.CodedError", err, err)
	}
	if coded.Code != ux.CodeTransportError {
		t.Errorf("code = %q, want %q", coded.Code, ux.CodeTransportError)
	}
	if !strings.HasPrefix(coded.Msg, "transport error: ") {
		t.Errorf("msg = %q, want the single-sourced \"transport error: …\" prefix (AGT-23)", coded.Msg)
	}
	// UX-4: the loopback mismatch must carry the exact recovery, not a bare
	// transport error.
	for _, want := range []string{
		"resolved to https but is serving http",
		"--broker-scheme http --broker-host 127.0.0.1:8100",
		"jentic env add <env> --broker-url http://127.0.0.1:8100 --force",
	} {
		if !strings.Contains(coded.Actionable, want) {
			t.Errorf("actionable %q missing %q (the UX-4 loopback TLS-mismatch recovery)", coded.Actionable, want)
		}
	}
}

// TestDo_DialClosedPortIsBareTransportError pins the other half of the branch:
// a refused dial (closed loopback port) is a coded TRANSPORT_ERROR with NO
// actionable — the TLS-mismatch recovery is reserved for its exact signature,
// so we never suggest a scheme downgrade for an unrelated dial failure.
func TestDo_DialClosedPortIsBareTransportError(t *testing.T) {
	// Reserve a loopback port, then close the listener so the dial is refused.
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("reserve port: %v", err)
	}
	addr := ln.Addr().String()
	_ = ln.Close()

	// Plain http to a loopback host passes SEC-1, so the failure happens at
	// dial time inside Do.
	req, err := BuildRequest(context.Background(), ExecuteRequest{
		Method:       http.MethodPost,
		Path:         "/v1/pets",
		BrokerScheme: "http",
		BrokerHost:   addr,
		Token:        "tok_abc",
	})
	if err != nil {
		t.Fatalf("BuildRequest: %v", err)
	}

	_, err = Do(req)
	if err == nil {
		t.Fatal("Do against a closed port must fail")
	}
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("Do returned %T (%v), want *ux.CodedError", err, err)
	}
	if coded.Code != ux.CodeTransportError {
		t.Errorf("code = %q, want %q", coded.Code, ux.CodeTransportError)
	}
	if !strings.HasPrefix(coded.Msg, "transport error: ") {
		t.Errorf("msg = %q, want the single-sourced \"transport error: …\" prefix (AGT-23)", coded.Msg)
	}
	if coded.Actionable != "" {
		t.Errorf("actionable = %q, want empty — a plain refused dial is not the TLS-mismatch papercut", coded.Actionable)
	}
}

// TestDoWith_RefusesBrokerRedirect is the #1207 broker-leg regression: a
// broker that answers with a redirect must NOT be followed — no second
// request lands anywhere — and the surfaced 3xx maps to a coded
// TRANSPORT_ERROR naming the redirect, never a confusing "HTTP 302" success.
func TestDoWith_RefusesBrokerRedirect(t *testing.T) {
	var paths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		if r.URL.Path == "/internal-admin" {
			_, _ = w.Write([]byte(`{"should":"never be seen"}`))
			return
		}
		http.Redirect(w, r, "/internal-admin", http.StatusFound)
	}))
	t.Cleanup(srv.Close)

	req, err := BuildRequest(context.Background(), ExecuteRequest{
		Method:       http.MethodGet,
		Path:         "/v1/pets",
		BrokerScheme: "http",
		BrokerHost:   srv.Listener.Addr().String(),
		Token:        "tok_abc",
	})
	if err != nil {
		t.Fatalf("BuildRequest: %v", err)
	}

	_, err = Do(req)
	if err == nil {
		t.Fatal("a broker redirect must surface as an error, not be followed or pass as a result")
	}
	var coded *ux.CodedError
	if !errors.As(err, &coded) {
		t.Fatalf("Do returned %T (%v), want *ux.CodedError", err, err)
	}
	if coded.Code != ux.CodeTransportError {
		t.Errorf("code = %q, want %q", coded.Code, ux.CodeTransportError)
	}
	if !strings.Contains(coded.Msg, "redirect") || !strings.Contains(coded.Msg, "302") ||
		!strings.Contains(coded.Msg, "/internal-admin") {
		t.Errorf("msg %q should name the refused redirect, its status, and its Location", coded.Msg)
	}
	if !strings.Contains(coded.Actionable, "broker_url") {
		t.Errorf("actionable %q should point at the broker_url configuration", coded.Actionable)
	}
	if len(paths) != 1 || paths[0] != "/v1/pets" {
		t.Errorf("requested paths = %v, want only the broker path (the redirect target must never be fetched)", paths)
	}
}

// TestDoWith_UpstreamRedirectPassesThrough pins the transparent-proxy carve-out
// of the #1207 refusal: a 3xx the broker MIRRORED from the upstream
// (Jentic-Error-Origin: upstream) is a successfully proxied response — the
// caller's data, not a broker transport violation. It is still never
// FOLLOWED (the Location may be internal to the upstream's own topology and
// must be the agent's decision), but it flows through as a normal result.
func TestDoWith_UpstreamRedirectPassesThrough(t *testing.T) {
	var hits int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits++
		w.Header().Set("Jentic-Error-Origin", "upstream")
		w.Header().Set("Location", "https://upstream.example/moved")
		w.WriteHeader(http.StatusFound)
	}))
	t.Cleanup(srv.Close)

	req, err := BuildRequest(context.Background(), ExecuteRequest{
		Method:       http.MethodGet,
		Path:         "/v1/pets",
		BrokerScheme: "http",
		BrokerHost:   srv.Listener.Addr().String(),
		Token:        "tok_abc",
	})
	if err != nil {
		t.Fatalf("BuildRequest: %v", err)
	}

	res, err := Do(req)
	if err != nil {
		t.Fatalf("a mirrored upstream 302 is the caller's data, not an error: %v", err)
	}
	if res.Status != http.StatusFound {
		t.Errorf("status = %d, want the upstream's 302 surfaced verbatim", res.Status)
	}
	if hits != 1 {
		t.Errorf("broker hits = %d, want 1 (the Location must never be fetched)", hits)
	}
}

func TestTransportFailurePreSend(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want bool
	}{
		{
			name: "dial failure is pre-send",
			err:  &net.OpError{Op: "dial", Net: "tcp", Err: syscall.ECONNREFUSED},
			want: true,
		},
		{
			name: "connection refused is pre-send",
			err:  syscall.ECONNREFUSED,
			want: true,
		},
		{
			name: "DNS failure is pre-send",
			err:  &net.DNSError{Err: "no such host", Name: "broker.invalid"},
			want: true,
		},
		{
			name: "TLS record header mismatch is pre-send",
			err:  tls.RecordHeaderError{Msg: "first record does not look like a TLS handshake"},
			want: true,
		},
		{
			name: "TLS certificate verification failure is pre-send",
			err:  &tls.CertificateVerificationError{Err: errors.New("x509: certificate signed by unknown authority")},
			want: true,
		},
		{
			name: "deadline exceeded is NOT provably pre-send",
			err:  context.DeadlineExceeded,
			want: false,
		},
		{
			name: "mid-flight EOF is NOT provably pre-send",
			err:  io.EOF,
			want: false,
		},
		{
			name: "connection reset is NOT provably pre-send",
			err:  &net.OpError{Op: "read", Net: "tcp", Err: syscall.ECONNRESET},
			want: false,
		},
		{
			name: "nil is not a failure at all",
			err:  nil,
			want: false,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := TransportFailurePreSend(tc.err); got != tc.want {
				t.Errorf("TransportFailurePreSend(%v) = %v, want %v", tc.err, got, tc.want)
			}
			// The classification must survive DoWith's CodedError wrapping
			// (the Cause chain is how the MCP handler reaches it).
			wrapped := &ux.CodedError{Code: ux.CodeTransportError, Msg: "transport error", Cause: tc.err}
			if got := TransportFailurePreSend(wrapped); got != tc.want {
				t.Errorf("TransportFailurePreSend(CodedError{Cause: %v}) = %v, want %v", tc.err, got, tc.want)
			}
		})
	}
}
