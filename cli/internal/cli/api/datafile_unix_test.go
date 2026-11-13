//go:build unix

package api

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

// TestDataFile_RefusesWriterlessFIFO pins that a named pipe with no writer
// (both direct and via symlink, and even when swapped into place after the
// pre-flight os.Stat) never hangs open(2) and fails promptly with
// MISSING_ARGUMENT on both `jentic execute --data-file` and
// `jentic api --data-file` (#1793).
func TestDataFile_RefusesWriterlessFIFO(t *testing.T) {
	for _, cmdKind := range dataFileCommands {
		t.Run(cmdKind+"/direct_fifo", func(t *testing.T) {
			fifo := filepath.Join(t.TempDir(), "body.fifo")
			if err := syscall.Mkfifo(fifo, 0o600); err != nil {
				t.Skipf("mkfifo: %v", err)
			}
			var hit bool
			var err error
			runWithTimeout(t, 3*time.Second, cmdKind+" --data-file writer-less FIFO", func() {
				hit, _, err = runBodyCommand(t, cmdKind, nil, "--data-file", fifo)
			})
			if hit {
				t.Fatalf("%s dialed the server for a FIFO --data-file", cmdKind)
			}
			wantUsageError(t, err, "not a regular file")
		})

		t.Run(cmdKind+"/symlink_to_fifo", func(t *testing.T) {
			dir := t.TempDir()
			fifo := filepath.Join(dir, "body.fifo")
			if err := syscall.Mkfifo(fifo, 0o600); err != nil {
				t.Skipf("mkfifo: %v", err)
			}
			link := filepath.Join(dir, "link.json")
			if err := os.Symlink(fifo, link); err != nil {
				t.Skipf("symlink: %v", err)
			}
			var hit bool
			var err error
			runWithTimeout(t, 3*time.Second, cmdKind+" --data-file symlink to FIFO", func() {
				hit, _, err = runBodyCommand(t, cmdKind, nil, "--data-file", link)
			})
			if hit {
				t.Fatalf("%s dialed the server for a symlinked FIFO --data-file", cmdKind)
			}
			wantUsageError(t, err, "not a regular file")
		})

		t.Run(cmdKind+"/swapped_to_fifo_after_prestat", func(t *testing.T) {
			dir := t.TempDir()
			target := writeTemp(t, dir, "body.json", `{"ok":true}`)
			fifo := filepath.Join(dir, "replacement.fifo")
			if err := syscall.Mkfifo(fifo, 0o600); err != nil {
				t.Skipf("mkfifo: %v", err)
			}
			afterDataFilePreStatHook = func(path string) {
				if err := os.Rename(fifo, path); err != nil {
					t.Errorf("swap FIFO into %s: %v", path, err)
				}
			}
			t.Cleanup(func() { afterDataFilePreStatHook = nil })

			var hit bool
			var err error
			runWithTimeout(t, 3*time.Second, cmdKind+" --data-file swapped to writer-less FIFO", func() {
				hit, _, err = runBodyCommand(t, cmdKind, nil, "--data-file", target)
			})
			if hit {
				t.Fatalf("%s dialed the server after path was swapped to a FIFO", cmdKind)
			}
			wantUsageError(t, err, "not a regular file")
		})
	}
}
