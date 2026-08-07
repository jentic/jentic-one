package client

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
)

// roundTripFunc lets a test capture the outbound request and return a canned
// response without a real network.
type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) { return f(req) }

func recordingClient(capture **http.Request) *http.Client {
	return &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		*capture = req
		return &http.Response{
			StatusCode: http.StatusOK,
			Body:       io.NopCloser(strings.NewReader(`{}`)),
			Header:     http.Header{"Content-Type": {"application/json"}},
		}, nil
	})}
}

func TestNewControl_RequiresBaseURL(t *testing.T) {
	if _, err := NewControl(Config{}); err == nil {
		t.Error("NewControl with empty ControlBaseURL should error")
	}
}

func TestNewBroker_RequiresBrokerAndControlURL(t *testing.T) {
	if _, err := NewBroker(Config{ControlBaseURL: "https://ctl"}); err == nil {
		t.Error("NewBroker without BrokerBaseURL should error")
	}
	if _, err := NewBroker(Config{BrokerBaseURL: "https://brk"}); err == nil {
		t.Error("NewBroker without ControlBaseURL (needed to authenticate) should error")
	}
}

// TestEditorChain drives a real request through the generated control client and
// asserts the assembled chain: the auth editor attaches the injected bearer AND a
// caller-supplied editor also runs. This is exactly the wiring arch test 1A
// protects.
func TestEditorChain(t *testing.T) {
	var captured *http.Request
	callerRan := false

	cli, err := NewControl(Config{
		ControlBaseURL:      "https://control.example",
		InjectedBearerToken: "tok-xyz",
		HTTPClient:          recordingClient(&captured),
		Editors: []RequestEditor{
			func(_ context.Context, req *http.Request) error {
				callerRan = true
				req.Header.Set("X-Caller", "1")
				return nil
			},
		},
	})
	if err != nil {
		t.Fatalf("NewControl: %v", err)
	}

	if _, err := cli.HealthWithResponse(context.Background()); err != nil {
		t.Fatalf("HealthWithResponse: %v", err)
	}
	if captured == nil {
		t.Fatal("no request captured; editor chain never reached transport")
	}
	if got := captured.Header.Get("Authorization"); got != "Bearer tok-xyz" {
		t.Errorf("Authorization = %q, want Bearer tok-xyz", got)
	}
	if !callerRan || captured.Header.Get("X-Caller") != "1" {
		t.Errorf("caller editor did not run (X-Caller=%q)", captured.Header.Get("X-Caller"))
	}
}

// TestEditorChain_TransportGuard: an http (non-loopback) base URL must be refused
// BEFORE any bearer is attached (F3), surfacing as an error from the call.
func TestEditorChain_TransportGuard(t *testing.T) {
	var captured *http.Request
	cli, err := NewControl(Config{
		ControlBaseURL:      "http://control.example", // insecure, non-loopback
		InjectedBearerToken: "tok-xyz",
		HTTPClient:          recordingClient(&captured),
	})
	if err != nil {
		t.Fatalf("NewControl: %v", err)
	}
	if _, err := cli.HealthWithResponse(context.Background()); err == nil {
		t.Fatal("expected transport guard to reject insecure base URL")
	}
	if captured != nil && captured.Header.Get("Authorization") != "" {
		t.Error("bearer was attached to an insecure request (F3 violation)")
	}
}

// TestSessionEditor_AttachedWhenSet: X-Jentic-Session-Id rides along when
// Config.SessionID is set, and is absent otherwise.
func TestSessionEditor_AttachedWhenSet(t *testing.T) {
	var captured *http.Request
	cli, err := NewControl(Config{
		ControlBaseURL:      "https://control.example",
		InjectedBearerToken: "tok",
		SessionID:           "sess-123",
		HTTPClient:          recordingClient(&captured),
	})
	if err != nil {
		t.Fatalf("NewControl: %v", err)
	}
	if _, err := cli.HealthWithResponse(context.Background()); err != nil {
		t.Fatalf("HealthWithResponse: %v", err)
	}
	if got := captured.Header.Get("X-Jentic-Session-Id"); got != "sess-123" {
		t.Errorf("X-Jentic-Session-Id = %q, want sess-123", got)
	}
}

func TestSessionEditor_AbsentWhenUnset(t *testing.T) {
	var captured *http.Request
	cli, err := NewControl(Config{
		ControlBaseURL:      "https://control.example",
		InjectedBearerToken: "tok",
		HTTPClient:          recordingClient(&captured),
	})
	if err != nil {
		t.Fatalf("NewControl: %v", err)
	}
	if _, err := cli.HealthWithResponse(context.Background()); err != nil {
		t.Fatalf("HealthWithResponse: %v", err)
	}
	if got := captured.Header.Get("X-Jentic-Session-Id"); got != "" {
		t.Errorf("X-Jentic-Session-Id = %q, want empty", got)
	}
}
