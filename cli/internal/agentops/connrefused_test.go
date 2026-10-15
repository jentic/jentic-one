package agentops

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/url"
	"syscall"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// closedLoopbackAddr reserves a loopback port and closes its listener, so a
// dial to the returned host:port is refused with a real ECONNREFUSED.
func closedLoopbackAddr(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("reserve port: %v", err)
	}
	addr := ln.Addr().String()
	_ = ln.Close()
	return addr
}

// refusedAt synthesizes the *url.Error → *net.OpError → ECONNREFUSED chain
// net/http returns for a refused dial, for hosts a test cannot really dial.
func refusedAt(rawURL string, dialed net.Addr) error {
	return &url.Error{Op: "Post", URL: rawURL, Err: &net.OpError{
		Op: "dial", Net: "tcp", Addr: dialed, Err: syscall.ECONNREFUSED,
	}}
}

// TestLoopbackConnRefused pins the #849 classifier: only a refused dial whose
// target is loopback (127.0.0.0/8, ::1, "localhost") reads as a stopped local
// instance. A refused REMOTE target, a non-refusal failure, or a refusal with
// no host to judge never does, so the stopped-instance hint cannot mislead.
func TestLoopbackConnRefused(t *testing.T) {
	// A real refused dial through the execute core: the chain DoWith returns
	// (CodedError → *url.Error → *net.OpError → ECONNREFUSED) must classify.
	// POST with no Idempotency-Key gives the dial exactly one attempt.
	req, err := BuildRequest(context.Background(), ExecuteRequest{
		Method:       http.MethodPost,
		Path:         "/v1/pets",
		BrokerScheme: "http",
		BrokerHost:   closedLoopbackAddr(t),
		Token:        "tok_abc",
	})
	if err != nil {
		t.Fatalf("BuildRequest: %v", err)
	}
	_, realErr := Do(req)
	if realErr == nil {
		t.Fatal("Do against a closed loopback port must fail")
	}

	loopV4 := &net.TCPAddr{IP: net.ParseIP("127.0.0.1"), Port: 8100}
	loopV6 := &net.TCPAddr{IP: net.IPv6loopback, Port: 8100}
	remote := &net.TCPAddr{IP: net.ParseIP("203.0.113.9"), Port: 8100}

	cases := []struct {
		name string
		err  error
		want bool
	}{
		{"real refused loopback dial", realErr, true},
		{"localhost URL", refusedAt("http://localhost:8000/inspect", loopV4), true},
		{"IPv6 loopback URL", refusedAt("http://[::1]:8100/v1/pets", loopV6), true},
		{"127/8 beyond .1", refusedAt("http://127.0.0.2:8100/", &net.TCPAddr{IP: net.ParseIP("127.0.0.2"), Port: 8100}), true},
		{"dial address only (no URL)", &net.OpError{Op: "dial", Net: "tcp", Addr: loopV4, Err: syscall.ECONNREFUSED}, true},
		{"Windows WSAECONNREFUSED, loopback", &url.Error{Op: "Post", URL: "http://127.0.0.1:8100/v1/pets", Err: &net.OpError{Op: "dial", Net: "tcp", Addr: loopV4, Err: wsaeconnrefused}}, true},
		{"Windows WSAECONNREFUSED, remote", &url.Error{Op: "Post", URL: "https://broker.example.com/v1/pets", Err: &net.OpError{Op: "dial", Net: "tcp", Addr: remote, Err: wsaeconnrefused}}, false},
		{"wrapped in a CodedError cause", &ux.CodedError{Code: ux.CodeResolveFailed, Cause: refusedAt("http://127.0.0.1:8000/inspect", loopV4)}, true},
		{"remote URL", refusedAt("https://broker.example.com/v1/pets", remote), false},
		{"remote URL via a refused loopback proxy", refusedAt("https://broker.example.com/v1/pets", loopV4), false},
		{"remote dial address only", &net.OpError{Op: "dial", Net: "tcp", Addr: remote, Err: syscall.ECONNREFUSED}, false},
		{"loopback but reset, not refused", &url.Error{Op: "Post", URL: "http://127.0.0.1:8100/", Err: &net.OpError{Op: "read", Net: "tcp", Addr: loopV4, Err: syscall.ECONNRESET}}, false},
		{"bare refusal names no host", syscall.ECONNREFUSED, false},
		{"unrelated error", errors.New("boom"), false},
		{"nil", nil, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := LoopbackConnRefused(tc.err); got != tc.want {
				t.Errorf("LoopbackConnRefused(%v) = %v, want %v", tc.err, got, tc.want)
			}
		})
	}
}

// refusingInspector fails every inspect with a refused loopback dial — the
// control plane of a stopped local install.
type refusingInspector struct{ err error }

func (r refusingInspector) Inspect(context.Context, string, string, string) ([]byte, error) {
	return nil, r.err
}

// TestResolveOperationKeepsTransportCause pins that a non-404 resolve failure
// keeps its transport error as the CodedError cause, so a refused loopback
// control plane is classifiable on the resolve leg (#849) while the code and
// message stay the documented RESOLVE_FAILED shape.
func TestResolveOperationKeepsTransportCause(t *testing.T) {
	cause := refusedAt("http://127.0.0.1:8000/inspect", &net.TCPAddr{IP: net.ParseIP("127.0.0.1"), Port: 8000})
	_, err := ResolveOperation(context.Background(), refusingInspector{cause}, "listPets", "")
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeResolveFailed {
		t.Fatalf("error = %T (%v), want RESOLVE_FAILED CodedError", err, err)
	}
	if coded.ExitCode() != ux.ExitDenied {
		t.Errorf("exit = %d, want %d (RESOLVE_FAILED)", coded.ExitCode(), ux.ExitDenied)
	}
	if !isConnRefused(err) || !LoopbackConnRefused(err) {
		t.Errorf("resolve error %v lost its refused-dial cause", err)
	}
}
