package api

import (
	"bytes"
	"fmt"
	"io"
	"os"
)

// stdinHasPipedBody reports whether stdin carries a real request body that the
// implicit body fallback may read (#1354). It is true only for the two shapes
// a deliberate `echo … | jentic …` / `jentic … < file` produces:
//
//   - a named pipe (ModeNamedPipe) — the shell-pipe case; and
//   - a regular file with non-zero size — the redirection case.
//
// Every other shape is declined so the read can never block: a TTY
// (interactive, no piped body), a character device, and — the #1354 hang — an
// inherited non-TTY fd with no writer and no EOF (a backgrounded process, or
// an agent/harness whose stdin is a socket/pty). Stat errors fail closed to
// false: without proof that a body is present, we must not risk blocking on
// the read. An explicit `-d -` bypasses this gate and blocks to EOF by design.
func stdinHasPipedBody(f *os.File) bool {
	if f == nil {
		return false
	}
	info, err := f.Stat()
	if err != nil {
		return false
	}
	mode := info.Mode()
	if mode&os.ModeNamedPipe != 0 {
		return true
	}
	if mode.IsRegular() && info.Size() > 0 {
		return true
	}
	return false
}

// readStdinBody drains os.Stdin to EOF and returns the bytes as a request
// body, or nil when stdin carries none. It blocks until EOF, so callers must
// gate the call: the implicit fallback calls it only when stdinHasPipedBody
// proves a body source is present; the explicit `-d -` calls it
// unconditionally because the caller opted in to blocking (#1354).
func readStdinBody() (io.Reader, error) {
	data, err := io.ReadAll(os.Stdin)
	if err != nil {
		return nil, fmt.Errorf("reading stdin: %w", err)
	}
	if len(data) == 0 {
		return nil, nil
	}
	return bytes.NewReader(data), nil
}
