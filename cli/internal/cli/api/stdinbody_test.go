package api

import (
	"io"
	"os"
	"testing"
	"time"
)

// TestStdinHasPipedBody pins the #1354 gate: the implicit stdin fallback must
// read a body ONLY when stdin is a pipe or a non-empty regular file (a real
// `echo … | execute` / `execute < file`), and must decline every other shape.
// The hang shape itself — an open, dataless, never-EOF non-TTY fd, as
// inherited from a backgrounded process or an agent/harness — is pinned end to
// end by the socketpair tests in stdinbody_unix_test.go.
func TestStdinHasPipedBody(t *testing.T) {
	t.Run("nil is false", func(t *testing.T) {
		if stdinHasPipedBody(nil) {
			t.Error("nil stdin must not be treated as a piped body")
		}
	})

	t.Run("non-empty regular file is true", func(t *testing.T) {
		f, err := os.CreateTemp(t.TempDir(), "body")
		if err != nil {
			t.Fatal(err)
		}
		defer f.Close()
		if _, err := f.WriteString(`{"a":1}`); err != nil {
			t.Fatal(err)
		}
		if _, err := f.Seek(0, io.SeekStart); err != nil {
			t.Fatal(err)
		}
		if !stdinHasPipedBody(f) {
			t.Error("a non-empty regular file must be read as a body")
		}
	})

	t.Run("empty regular file is false", func(t *testing.T) {
		f, err := os.CreateTemp(t.TempDir(), "empty")
		if err != nil {
			t.Fatal(err)
		}
		defer f.Close()
		if stdinHasPipedBody(f) {
			t.Error("an empty regular file carries no body")
		}
	})

	t.Run("pipe with data written is true", func(t *testing.T) {
		r, w, err := os.Pipe()
		if err != nil {
			t.Fatal(err)
		}
		defer r.Close()
		if _, err := w.WriteString(`{"a":1}`); err != nil {
			t.Fatal(err)
		}
		_ = w.Close()
		if !stdinHasPipedBody(r) {
			t.Error("a pipe (ModeNamedPipe) must be treated as a piped body")
		}
	})

	t.Run("open dataless pipe is still a pipe (true)", func(t *testing.T) {
		// A pipe reports ModeNamedPipe regardless of whether bytes are buffered
		// yet, so the classifier returns true. That is correct: a real
		// `producer | execute` may not have flushed by the time we Stat, and
		// blocking on an explicit `|` is the least-surprising behavior (same as
		// `cat | grep`). The shape that must never block is the non-pipe idle
		// fd, covered by the socketpair tests.
		r, w, err := os.Pipe()
		if err != nil {
			t.Fatal(err)
		}
		defer r.Close()
		defer w.Close()
		if !stdinHasPipedBody(r) {
			t.Error("a pipe must classify as a body source even before data is flushed")
		}
	})

	t.Run("directory (non-regular, non-pipe) is false", func(t *testing.T) {
		d, err := os.Open(t.TempDir())
		if err != nil {
			t.Fatal(err)
		}
		defer d.Close()
		if stdinHasPipedBody(d) {
			t.Error("a directory is not a body source")
		}
	})
}

// withStdin swaps os.Stdin to f for the duration of fn and restores it after.
// The body resolvers read the global os.Stdin, so an end-to-end test must
// redirect it rather than pass a descriptor in.
func withStdin(t *testing.T, f *os.File, fn func()) {
	t.Helper()
	orig := os.Stdin
	os.Stdin = f
	defer func() { os.Stdin = orig }()
	fn()
}

// runWithTimeout runs fn and fails the test if it does not return within d.
// A regression that reads a blocking, dataless non-TTY fd would hang forever;
// this turns that into a bounded, legible test failure instead of a stuck suite.
func runWithTimeout(t *testing.T, d time.Duration, what string, fn func()) {
	t.Helper()
	done := make(chan struct{})
	go func() { defer close(done); fn() }()
	select {
	case <-done:
	case <-time.After(d):
		t.Fatalf("%s did not return within %s — it read a blocking stdin (regression of #1354)", what, d)
	}
}

// TestResolveAPIBody_IdleCharDeviceStdinHasNoBody drives resolveAPIBody end to
// end with a character-device stdin (/dev/null): a non-pipe, non-regular fd
// must yield no body. /dev/null EOFs instantly, so this pins only the no-body
// result portably; the never-blocks contract against a genuinely blocking fd
// is pinned by TestResolveAPIBody_BlockingSocketStdinDoesNotHang (unix-only).
func TestResolveAPIBody_IdleCharDeviceStdinHasNoBody(t *testing.T) {
	devnull, err := os.Open(os.DevNull)
	if err != nil {
		t.Fatalf("open %s: %v", os.DevNull, err)
	}
	defer devnull.Close()

	if stdinHasPipedBody(devnull) {
		t.Fatalf("%s (char device) must not classify as a piped body", os.DevNull)
	}

	var body io.Reader
	var rerr error
	withStdin(t, devnull, func() {
		body, rerr = resolveAPIBody(&apiOptions{})
	})
	if rerr != nil {
		t.Fatalf("resolveAPIBody: %v", rerr)
	}
	if body != nil {
		t.Errorf("body = %v, want nil (a char-device stdin is not a body source)", body)
	}
}

// TestResolveAPIBody_PipedStdinIsRead is the positive counterpart: a real
// `echo … | jentic api` (a pipe carrying data) must still be read as the body,
// so the #1354 gate does not regress the legitimate stdin path.
func TestResolveAPIBody_PipedStdinIsRead(t *testing.T) {
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	const payload = `{"piped":true}`
	if _, err := w.WriteString(payload); err != nil {
		t.Fatal(err)
	}
	_ = w.Close() // EOF so the drain returns

	var body io.Reader
	var rerr error
	withStdin(t, r, func() {
		runWithTimeout(t, 5*time.Second, "resolveAPIBody with piped stdin", func() {
			body, rerr = resolveAPIBody(&apiOptions{})
		})
	})
	if rerr != nil {
		t.Fatalf("resolveAPIBody: %v", rerr)
	}
	if body == nil {
		t.Fatal("body = nil, want the piped payload")
	}
	got, err := io.ReadAll(body)
	if err != nil {
		t.Fatalf("read resolved body: %v", err)
	}
	if string(got) != payload {
		t.Errorf("body = %q, want %q", got, payload)
	}
}

// TestResolveExecuteBody_PipedStdinIsRead pins the same positive path for
// execute's body resolver, so both body-taking commands keep the legitimate
// `echo … | …` input working.
func TestResolveExecuteBody_PipedStdinIsRead(t *testing.T) {
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	const payload = `{"piped":true}`
	if _, err := w.WriteString(payload); err != nil {
		t.Fatal(err)
	}
	_ = w.Close() // EOF so the drain returns

	var body io.Reader
	var ct string
	var rerr error
	withStdin(t, r, func() {
		runWithTimeout(t, 5*time.Second, "resolveExecuteBody with piped stdin", func() {
			body, ct, rerr = resolveExecuteBody(&executeOptions{})
		})
	})
	if rerr != nil {
		t.Fatalf("resolveExecuteBody: %v", rerr)
	}
	if ct != "" {
		t.Errorf("multipart content type = %q, want empty for a raw stdin body", ct)
	}
	if body == nil {
		t.Fatal("body = nil, want the piped payload")
	}
	got, err := io.ReadAll(body)
	if err != nil {
		t.Fatalf("read resolved body: %v", err)
	}
	if string(got) != payload {
		t.Errorf("body = %q, want %q", got, payload)
	}
}
