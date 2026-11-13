package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	sdkclient "github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/pkg/core"
)

// dataFileCommands enumerates both CLI surfaces that share readDataFile so
// every --data-file contract test exercises `jentic execute` and `jentic api`
// identically (#1793).
var dataFileCommands = []string{"execute", "api"}

// runBodyCommand executes either `jentic execute` or `jentic api` against a
// local test server with bodyArgs (e.g. `--data-file <path>` or `-d -`),
// returning whether any network request reached the server, the stderr stream
// emitted in agent mode, and the command error.
func runBodyCommand(t *testing.T, cmdKind string, onHit func(*http.Request), bodyArgs ...string) (hit bool, stderr string, err error) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hit = true
		if onHit != nil {
			onHit(r)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	t.Setenv("JENTIC_HOME", t.TempDir())
	outBuf := new(bytes.Buffer)
	errBuf := new(bytes.Buffer)
	deps := &core.AppContainer{In: os.Stdin, Out: outBuf, Err: errBuf}

	switch cmdKind {
	case "execute":
		seedRegistered(t, nil, "default", srv.URL)
		root := TreeBuilder()(deps)
		root.SetOut(outBuf)
		root.SetErr(errBuf)
		args := append([]string{
			"--mode", "agent",
			"execute", "POST:/v1/pets",
			"--broker-scheme", "http",
			"--broker-host", srv.Listener.Addr().String(),
		}, bodyArgs...)
		root.SetArgs(args)
		_ = captureStdout(t, func() {
			stderr = captureStderr(t, func() {
				err = root.Execute()
			})
		})
		return hit, stderr, err
	case "api":
		withXDG(t)
		seedActiveContextTo(t, srv.URL)
		root := TreeBuilder()(deps)
		root.SetOut(outBuf)
		root.SetErr(errBuf)
		args := append([]string{"--mode", "agent", "api", "POST", "/agents"}, bodyArgs...)
		root.SetArgs(args)
		stderr = captureStderr(t, func() {
			err = root.Execute()
		})
		return hit, stderr, err
	default:
		t.Fatalf("unknown cmdKind %q", cmdKind)
		return false, "", nil
	}
}

func withDataFileLimit(t *testing.T, limit int64) {
	t.Helper()
	orig := dataFileBodyLimit
	dataFileBodyLimit = limit
	t.Cleanup(func() { dataFileBodyLimit = orig })
}

func writeSparseFile(t *testing.T, dir, name string, size int64) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, nil, 0o600); err != nil {
		t.Fatalf("create %s: %v", p, err)
	}
	if err := os.Truncate(p, size); err != nil {
		t.Fatalf("truncate %s to %d: %v", p, size, err)
	}
	return p
}

// assertBodyTooLarge verifies both the returned *ux.CodedError and the agent
// stderr JSON envelope carry MISSING_ARGUMENT + details.reason="body_too_large"
// and the expected limit_bytes / total_bytes (#1793).
func assertBodyTooLarge(t *testing.T, err error, stderr string, wantLimit, wantMinTotal int64) {
	t.Helper()
	coded := wantUsageError(t, err, "request body limit")
	if got := coded.Details["reason"]; got != "body_too_large" {
		t.Errorf("details.reason = %v, want body_too_large", got)
	}
	if got := coded.Details["limit_bytes"]; got != wantLimit {
		t.Errorf("details.limit_bytes = %v (%T), want %d", got, got, wantLimit)
	}
	total, ok := coded.Details["total_bytes"].(int64)
	if !ok || total < wantMinTotal {
		t.Errorf("details.total_bytes = %v (%T), want >= %d", coded.Details["total_bytes"], coded.Details["total_bytes"], wantMinTotal)
	}

	var env ux.AgentError
	if uerr := json.Unmarshal([]byte(stderr), &env); uerr != nil {
		t.Fatalf("stderr is not an AgentError JSON document (%v): %q", uerr, stderr)
	}
	if env.ErrorCode != ux.CodeMissingArgument {
		t.Errorf("envelope error_code = %q, want %q", env.ErrorCode, ux.CodeMissingArgument)
	}
	if env.Details["reason"] != "body_too_large" {
		t.Errorf("envelope details.reason = %v, want body_too_large", env.Details["reason"])
	}
	if env.Details["limit_bytes"] != float64(wantLimit) {
		t.Errorf("envelope details.limit_bytes = %v, want %d", env.Details["limit_bytes"], wantLimit)
	}
}

// TestDataFile_MaxBodyBytesBoundary pins the real 64 MiB ceiling
// (sdkclient.MaxBodyBytes = 67108864) on both `jentic execute --data-file` and
// `jentic api --data-file`: an exact-limit file succeeds and delivers all
// 67108864 bytes, while MaxBodyBytes+1 fails locally before any network request
// with MISSING_ARGUMENT and details.reason="body_too_large" (#1793).
func TestDataFile_MaxBodyBytesBoundary(t *testing.T) {
	dir := t.TempDir()
	exact := writeSparseFile(t, dir, "exact-64mib.bin", sdkclient.MaxBodyBytes)
	over := writeSparseFile(t, dir, "over-64mib.bin", sdkclient.MaxBodyBytes+1)

	for _, cmdKind := range dataFileCommands {
		t.Run(cmdKind+"/exact_64MiB_accepted", func(t *testing.T) {
			var gotLen int64
			hit, _, err := runBodyCommand(t, cmdKind, func(r *http.Request) {
				n, copyErr := io.Copy(io.Discard, r.Body)
				if copyErr != nil {
					t.Errorf("drain request body: %v", copyErr)
				}
				gotLen = n
			}, "--data-file", exact)
			if err != nil {
				t.Fatalf("%s --data-file exact MaxBodyBytes: %v", cmdKind, err)
			}
			if !hit {
				t.Fatalf("%s did not send the exact-limit request", cmdKind)
			}
			if gotLen != sdkclient.MaxBodyBytes {
				t.Errorf("server received %d bytes, want %d", gotLen, sdkclient.MaxBodyBytes)
			}
		})

		t.Run(cmdKind+"/over_64MiB_refused_before_network", func(t *testing.T) {
			hit, stderr, err := runBodyCommand(t, cmdKind, nil, "--data-file", over)
			if hit {
				t.Fatalf("%s dialed the server for an over-limit --data-file", cmdKind)
			}
			assertBodyTooLarge(t, err, stderr, sdkclient.MaxBodyBytes, sdkclient.MaxBodyBytes+1)
		})
	}
}

// TestDataFile_GrowAfterStatBounded pins that a regular file growing past the
// limit after Stat() is still bounded by ReadAllBounded and refused before any
// network call with the same body_too_large contract (#1793).
func TestDataFile_GrowAfterStatBounded(t *testing.T) {
	const limit int64 = 512
	withDataFileLimit(t, limit)

	for _, cmdKind := range dataFileCommands {
		t.Run(cmdKind, func(t *testing.T) {
			p := writeTemp(t, t.TempDir(), "growing.json", `{"ok":true}`)
			afterDataFileStatHook = func(path string) {
				if err := os.Truncate(path, limit+100); err != nil {
					t.Errorf("grow %s after stat: %v", path, err)
				}
			}
			t.Cleanup(func() { afterDataFileStatHook = nil })

			hit, stderr, err := runBodyCommand(t, cmdKind, nil, "--data-file", p)
			if hit {
				t.Fatalf("%s dialed the server after --data-file grew past limit", cmdKind)
			}
			assertBodyTooLarge(t, err, stderr, limit, limit+1)
		})
	}
}

// TestDataFile_NonRegularAndMissingPaths pins the shared refusal table across
// `jentic execute --data-file` and `jentic api --data-file`: directories,
// character devices, and non-regular stdin aliases fail promptly as
// MISSING_ARGUMENT without hanging or dialing the network (#1793). Named FIFOs
// live in datafile_unix_test.go.
func TestDataFile_NonRegularAndMissingPaths(t *testing.T) {
	for _, cmdKind := range dataFileCommands {
		t.Run(cmdKind+"/directory", func(t *testing.T) {
			dir := t.TempDir()
			var hit bool
			var err error
			runWithTimeout(t, 5*time.Second, cmdKind+" --data-file directory", func() {
				hit, _, err = runBodyCommand(t, cmdKind, nil, "--data-file", dir)
			})
			if hit {
				t.Fatalf("%s dialed the server for a directory --data-file", cmdKind)
			}
			wantUsageError(t, err, "not a regular file")
		})

		for _, dev := range []string{os.DevNull, "/dev/zero", "/dev/stdin", "/dev/fd/0"} {
			t.Run(cmdKind+"/non_regular_"+filepath.Base(dev), func(t *testing.T) {
				fi, statErr := os.Stat(dev)
				if statErr != nil {
					t.Skipf("%s unavailable: %v", dev, statErr)
				}
				if fi.Mode().IsRegular() {
					t.Skipf("%s resolves to a regular file in this test process", dev)
				}
				var hit bool
				var err error
				runWithTimeout(t, 5*time.Second, cmdKind+" --data-file "+dev, func() {
					hit, _, err = runBodyCommand(t, cmdKind, nil, "--data-file", dev)
				})
				if hit {
					t.Fatalf("%s dialed the server for non-regular %s", cmdKind, dev)
				}
				wantUsageError(t, err, "not a regular file")
			})
		}

		t.Run(cmdKind+"/missing_file", func(t *testing.T) {
			missing := filepath.Join(t.TempDir(), "does-not-exist.json")
			hit, _, err := runBodyCommand(t, cmdKind, nil, "--data-file", missing)
			if hit {
				t.Fatalf("%s dialed the server for a missing --data-file", cmdKind)
			}
			wantUsageError(t, err, "cannot read --data-file")
			if !errors.Is(err, os.ErrNotExist) {
				t.Errorf("err = %v, want errors.Is(..., os.ErrNotExist)", err)
			}
		})
	}
}

// TestDataFile_ExplicitDashStdinPreserved verifies that rejecting non-regular
// files on --data-file leaves explicit `-d -` stdin reading intact on both
// `jentic execute` and `jentic api` (#1793).
func TestDataFile_ExplicitDashStdinPreserved(t *testing.T) {
	const payload = `{"from":"stdin"}`
	for _, cmdKind := range dataFileCommands {
		t.Run(cmdKind, func(t *testing.T) {
			r, w, err := os.Pipe()
			if err != nil {
				t.Fatal(err)
			}
			defer r.Close()
			if _, err := w.WriteString(payload); err != nil {
				t.Fatal(err)
			}
			_ = w.Close()

			var gotBody string
			var hit bool
			var cmdErr error
			withStdin(t, r, func() {
				runWithTimeout(t, 5*time.Second, cmdKind+" -d -", func() {
					hit, _, cmdErr = runBodyCommand(t, cmdKind, func(req *http.Request) {
						b, _ := io.ReadAll(req.Body)
						gotBody = string(b)
					}, "-d", "-")
				})
			})
			if cmdErr != nil {
				t.Fatalf("%s -d -: %v", cmdKind, cmdErr)
			}
			if !hit {
				t.Fatalf("%s -d - did not send request", cmdKind)
			}
			if gotBody != payload {
				t.Errorf("server received %q, want %q", gotBody, payload)
			}
		})
	}
}
