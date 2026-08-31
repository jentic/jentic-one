package agentops

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// The two tests below cover Do's dial-level transport branch — the one execute
// branch no golden can pin (a real dial failure's error string embeds an
// ephemeral port and OS-specific text, never byte-stable), so it is asserted
// here by fields (PR #1179 review #1). Both use POST without an
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
