package cmdcore

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/url"
	"os"
	"strings"
	"syscall"
	"testing"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// refusedTransportErr is the coded TRANSPORT_ERROR execute's broker leg
// returns for a refused dial to rawURL (synthesized: a test cannot really
// dial a remote host).
func refusedTransportErr(rawURL string, dialed *net.TCPAddr) *ux.CodedError {
	cause := &url.Error{Op: "Post", URL: rawURL, Err: &net.OpError{
		Op: "dial", Net: "tcp", Addr: dialed, Err: syscall.ECONNREFUSED,
	}}
	return &ux.CodedError{Code: ux.CodeTransportError, Msg: "transport error: " + cause.Error(), Cause: cause}
}

// TestWithStoppedInstanceHint pins the #849 helper: a refused loopback dial
// gets the error envelope first and the stopped-instance line after it on
// stderr, with the error (and so its error_code and exit code) returned
// unchanged; a refused remote dial or any other failure writes nothing.
func TestWithStoppedInstanceHint(t *testing.T) {
	loop := &net.TCPAddr{IP: net.ParseIP("127.0.0.1"), Port: 8100}
	remote := &net.TCPAddr{IP: net.ParseIP("203.0.113.9"), Port: 8100}

	cases := []struct {
		name     string
		err      error
		wantHint bool
	}{
		{"refused loopback broker", refusedTransportErr("http://127.0.0.1:8100/v1/pets", loop), true},
		{"refused remote broker", refusedTransportErr("https://broker.example.com/v1/pets", remote), false},
		{"non-transport coded error", &ux.CodedError{Code: ux.CodeMissingArgument, Msg: "bad flag"}, false},
		{"plain error", errors.New("boom"), false},
		{"no error", nil, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cmd := &cobra.Command{}
			cmd.SetContext(ux.WithAudience(context.Background(), ux.NewAgentUX(false)))

			var got error
			stderr := captureStderr(t, func() {
				// os.Stderr is the capture pipe here, so the envelope and the
				// hint share one stream and their order is observable.
				got = WithStoppedInstanceHint(cmd, os.Stderr, tc.err)
			})
			if got != tc.err { //nolint:errorlint // identity: the helper must return err itself.
				t.Fatalf("returned %v, want the input error unchanged (%v)", got, tc.err)
			}

			if !tc.wantHint {
				if stderr != "" {
					t.Errorf("stderr = %q, want nothing for a non-loopback/non-refused failure", stderr)
				}
				return
			}

			lines := strings.Split(strings.TrimRight(stderr, "\n"), "\n")
			if len(lines) != 2 {
				t.Fatalf("stderr lines = %q, want the error envelope then the hint", lines)
			}
			var env ux.AgentError
			if err := json.Unmarshal([]byte(lines[0]), &env); err != nil {
				t.Fatalf("first stderr line %q is not the error envelope: %v", lines[0], err)
			}
			if env.ErrorCode != ux.CodeTransportError || env.Actionable != "" {
				t.Errorf("envelope = %+v, want TRANSPORT_ERROR with no actionable_step (the hint stays off the envelope)", env)
			}
			if lines[1] != ux.StoppedInstanceHint {
				t.Errorf("hint line = %q, want %q", lines[1], ux.StoppedInstanceHint)
			}
			var coded *ux.CodedError
			if !errors.As(got, &coded) || !coded.IsReported() {
				t.Error("the coded error must be marked reported so the root backstop does not render it twice")
			}
		})
	}
}
