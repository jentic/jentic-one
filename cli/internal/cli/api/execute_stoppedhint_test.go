package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"testing"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// closedLoopbackAddr reserves a loopback port and closes its listener, so a
// dial to the returned host:port is refused with a real ECONNREFUSED — a
// stopped local jentic-one.
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

// captureStdStreams redirects the process stdout and stderr for fn's duration
// and returns what each received. The Audience writes envelopes to the real
// os.Stdout/os.Stderr, not the app's writers.
func captureStdStreams(t *testing.T, fn func()) (stdout, stderr string) {
	t.Helper()
	oldOut, oldErr := os.Stdout, os.Stderr
	outR, outW, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	errR, errW, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stdout, os.Stderr = outW, errW
	var outBuf, errBuf bytes.Buffer
	outDone, errDone := make(chan struct{}), make(chan struct{})
	go func() { _, _ = io.Copy(&outBuf, outR); close(outDone) }()
	go func() { _, _ = io.Copy(&errBuf, errR); close(errDone) }()
	defer func() {
		os.Stdout, os.Stderr = oldOut, oldErr
	}()
	fn()
	_ = outW.Close()
	_ = errW.Close()
	<-outDone
	<-errDone
	return outBuf.String(), errBuf.String()
}

// TestContextAuthErrKeepsRefusedCause pins the token-mint leg of #849: a mint
// refused by a stopped local control plane stays NOT_AUTHENTICATED (exit 1)
// but keeps the refused dial as its cause, so execute can still append the
// stopped-instance hint.
func TestContextAuthErrKeepsRefusedCause(t *testing.T) {
	closed := closedLoopbackAddr(t)
	resp, dialErr := http.Post("http://"+closed+"/token", "application/json", nil) //nolint:noctx // test-only refused dial
	if dialErr == nil {
		_ = resp.Body.Close()
		t.Fatal("POST to a closed loopback port must fail")
	}
	mintErr := fmt.Errorf("failed to authenticate: %w", fmt.Errorf("token exchange request failed: %w", dialErr))

	err := contextAuthErr(mintErr, &clictx.ActiveState{ResolvedState: &sdkconfig.ResolvedState{
		IdentityName: "test-agent", EnvironmentName: "test",
	}})
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeNotAuthenticated || coded.ExitCode() != ux.ExitError {
		t.Fatalf("error = %T (%v), want NOT_AUTHENTICATED exit 1", err, err)
	}
	if !agentops.LoopbackConnRefused(err) {
		t.Errorf("contextAuthErr(%v) lost the refused-dial cause", mintErr)
	}
}

// TestExecuteStoppedLocalInstanceHint pins #849 end to end: when execute's
// broker leg or its control-plane resolve leg is refused by a loopback host,
// stderr carries the usual error report followed by the one-line
// stopped-instance hint, while stdout stays empty and the error_code / exit
// code are exactly the unhinted ones (TRANSPORT_ERROR → 1, RESOLVE_FAILED → 2).
func TestExecuteStoppedLocalInstanceHint(t *testing.T) {
	cases := []struct {
		name     string
		mode     string
		target   string
		broker   bool // dial the broker leg (METHOD:/path skips resolve)
		wantCode string
		wantExit int
	}{
		{"broker leg, agent mode", "agent", "POST:/v1/pets", true, ux.CodeTransportError, ux.ExitError},
		{"broker leg, human mode", "human", "POST:/v1/pets", true, ux.CodeTransportError, ux.ExitError},
		{"resolve leg, agent mode", "agent", "GET:https://api.example.com/v1/pets", false, ux.CodeResolveFailed, ux.ExitDenied},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("JENTIC_MODE", tc.mode)
			app := testApp(t)
			closed := closedLoopbackAddr(t)
			seedRegistered(t, app, "default", "http://"+closed)

			args := []string{"execute", tc.target, "--json"}
			if tc.broker {
				args = append(args, "--broker-scheme", "http", "--broker-host", closed)
			}

			appOut := new(bytes.Buffer)
			var err error
			stdout, stderr := captureStdStreams(t, func() {
				app.Out = appOut
				app.Err = os.Stderr // the hint and the envelope share one stream, as in production
				root := newAPIRootCmd(app.App)
				root.SetOut(appOut)
				root.SetErr(os.Stderr)
				root.SetArgs(args)
				err = root.Execute()
			})

			var coded *ux.CodedError
			if !errors.As(err, &coded) {
				t.Fatalf("execute returned %T (%v), want *ux.CodedError", err, err)
			}
			if coded.Code != tc.wantCode || coded.ExitCode() != tc.wantExit {
				t.Errorf("code/exit = %s/%d, want %s/%d (the hint must not change the contract)",
					coded.Code, coded.ExitCode(), tc.wantCode, tc.wantExit)
			}
			if stdout != "" || appOut.Len() != 0 {
				t.Errorf("stdout = %q / %q, want empty: a failure emits no stdout envelope", stdout, appOut.String())
			}

			lines := strings.Split(strings.TrimRight(stderr, "\n"), "\n")
			if len(lines) < 2 || !strings.Contains(lines[len(lines)-1], ux.StoppedInstanceHint) {
				t.Fatalf("stderr = %q, want the stopped-instance hint as the last line", stderr)
			}
			if strings.Count(stderr, ux.StoppedInstanceHint) != 1 {
				t.Errorf("stderr = %q, want the hint exactly once", stderr)
			}
			report := strings.Join(lines[:len(lines)-1], "\n")
			if tc.mode == "agent" {
				var env ux.AgentError
				if jerr := json.Unmarshal([]byte(report), &env); jerr != nil {
					t.Fatalf("error report %q is not a single JSON envelope: %v", report, jerr)
				}
				if env.ErrorCode != tc.wantCode || strings.Contains(env.Actionable, "jenticctl") {
					t.Errorf("envelope = %+v, want error_code %s with the hint kept off it", env, tc.wantCode)
				}
			} else if !strings.Contains(report, "connection refused") {
				t.Errorf("human error report %q should name the refused connection", report)
			}
		})
	}
}
