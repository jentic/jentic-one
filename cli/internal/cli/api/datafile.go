package api

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"os"

	sdkclient "github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// dataFileBodyLimit is the request-body ceiling for --data-file on both
// `jentic execute` and `jentic api`. Kept as a package variable so tests can
// exercise small byte caps alongside the real sdkclient.MaxBodyBytes ceiling.
var dataFileBodyLimit = sdkclient.MaxBodyBytes

// Test hooks (nil in production) for deterministic race-window coverage:
// afterDataFilePreStatHook fires between pre-flight os.Stat and openDataFile,
// and afterDataFileStatHook fires between f.Stat and sdkclient.ReadAllBounded.
var (
	afterDataFilePreStatHook func(path string)
	afterDataFileStatHook    func(path string)
)

// readDataFile reads path as a bounded regular-file request body (#1793),
// shared by `jentic execute --data-file` and `jentic api --data-file`.
func readDataFile(path string) (io.Reader, error) {
	return readDataFileBounded(path, dataFileBodyLimit)
}

// readDataFileBounded validates that path is a regular file within limit bytes
// and buffers its contents via sdkclient.ReadAllBounded.
//
// Pre-flight os.Stat refuses directories, FIFOs, character devices, sockets,
// and non-regular stdin aliases (/dev/stdin, /dev/fd/0 backed by a pipe, TTY,
// or socket) without opening them — avoiding flag side effects on inherited
// descriptors and socket reopen errors while failing before any request context
// exists (#1354, #1793). Opening with O_NONBLOCK on Unix and re-checking
// f.Stat() on the opened descriptor closes the stat-to-open swap window so a
// writer-less FIFO swapped into place never blocks in open(2), and
// ReadAllBounded bounds a regular file that grows past limit after Stat().
func readDataFileBounded(path string, limit int64) (io.Reader, error) {
	if limit <= 0 {
		limit = sdkclient.MaxBodyBytes
	}
	fi, err := os.Stat(path)
	if err != nil {
		return nil, dataFileReadError(path, err)
	}
	if err := checkRegularDataFile(path, fi, limit); err != nil {
		return nil, err
	}

	if afterDataFilePreStatHook != nil {
		afterDataFilePreStatHook(path)
	}

	f, err := openDataFile(path)
	if err != nil {
		return nil, dataFileReadError(path, err)
	}
	defer func() { _ = f.Close() }()

	fi, err = f.Stat()
	if err != nil {
		return nil, dataFileReadError(path, err)
	}
	if err := checkRegularDataFile(path, fi, limit); err != nil {
		return nil, err
	}
	clearNonblock(f)

	if afterDataFileStatHook != nil {
		afterDataFileStatHook(path)
	}

	data, err := sdkclient.ReadAllBounded(f, limit)
	if errors.Is(err, sdkclient.ErrBodyTooLarge) {
		return nil, dataFileTooLargeError(path, limit+1, limit)
	}
	if err != nil {
		return nil, dataFileReadError(path, err)
	}
	return bytes.NewReader(data), nil
}

func checkRegularDataFile(path string, fi os.FileInfo, limit int64) error {
	if fi.IsDir() {
		return &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        fmt.Sprintf("--data-file %s is a directory, not a regular file", path),
			Actionable: "Point --data-file at a regular file, or use -d - to read from stdin.",
		}
	}
	if !fi.Mode().IsRegular() {
		return &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        fmt.Sprintf("--data-file %s is not a regular file (pipe, device, or stdin alias)", path),
			Actionable: "Write the request body to a regular file first, or use -d - to read from stdin.",
		}
	}
	if fi.Size() > limit {
		return dataFileTooLargeError(path, fi.Size(), limit)
	}
	return nil
}

// dataFileTooLargeError returns the coded size refusal for --data-file,
// matching multipartTooLargeError's MISSING_ARGUMENT + details.reason="body_too_large"
// machine contract (#1793). totalBytes is a lower bound when overflow is
// caught mid-read by ReadAllBounded.
func dataFileTooLargeError(path string, totalBytes, limit int64) error {
	return &ux.CodedError{
		Code: ux.CodeMissingArgument,
		Msg: fmt.Sprintf("--data-file %s is %d bytes, over the %d-byte (%d MiB) request body limit",
			path, totalBytes, limit, limit>>20),
		Actionable: "Pass a smaller request body file; --data-file cannot exceed the 64 MiB transport ceiling.",
		Details:    map[string]any{"reason": "body_too_large", "total_bytes": totalBytes, "limit_bytes": limit},
	}
}

func dataFileReadError(path string, err error) error {
	return &ux.CodedError{
		Code:       ux.CodeMissingArgument,
		Msg:        fmt.Sprintf("cannot read --data-file %s: %v", path, err),
		Actionable: "Check that the file exists and is readable, then retry with --data-file <path>.",
		Cause:      err,
	}
}
