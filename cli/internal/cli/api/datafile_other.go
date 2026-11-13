//go:build !unix

package api

import "os"

// openDataFile opens path read-only on non-Unix platforms.
func openDataFile(path string) (*os.File, error) {
	return os.Open(path) //nolint:gosec // --data-file opens the caller-supplied regular file path by contract.
}

// clearNonblock is a no-op on non-Unix platforms.
func clearNonblock(*os.File) {}
