//go:build unix

package api

import (
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

// TestBuildMultipartBodyRefusesFIFO pins that a named pipe is refused at the
// pre-flight stat: os.Open on a FIFO blocks until a writer appears, and body
// assembly runs before any request context exists, so an accepted FIFO would
// hang execute (and --dry-run) with no timeout. Unix-tagged: mkfifo(2).
func TestBuildMultipartBodyRefusesFIFO(t *testing.T) {
	fifo := filepath.Join(t.TempDir(), "upload.fifo")
	if err := syscall.Mkfifo(fifo, 0o600); err != nil {
		t.Skipf("mkfifo: %v", err)
	}
	var mp *multipartBody
	var err error
	runWithTimeout(t, 5*time.Second, "buildMultipartBody with a FIFO", func() {
		mp, err = buildMultipartBody(nil, []string{"f=@" + fifo}, 4096)
	})
	if mp != nil {
		t.Fatal("a FIFO must not produce a body")
	}
	wantUsageError(t, err, "not a regular file")
}
