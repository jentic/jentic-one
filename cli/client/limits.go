package client

import (
	"errors"
	"fmt"
	"io"
)

// MaxBodyBytes is the ceiling every whole-body read in the CLI/SDK is clamped to.
// 64 MiB comfortably covers any legitimate API request/response, OpenAPI spec, or
// release-metadata file we buffer into RAM, while denying a hostile or buggy peer
// the ability to exhaust memory by streaming an unbounded body (review round-3
// P0 / theme 2: unbounded io.ReadAll is systemic). Streaming-to-disk paths (the
// release archive download) keep their own, larger io.Copy limit — this cap is
// specifically for "read the whole thing into a []byte" call sites.
const MaxBodyBytes int64 = 64 << 20

// ErrBodyTooLarge is returned by ReadAllBounded when the source produces more
// than the allowed number of bytes. It is a sentinel so callers can distinguish
// a size refusal from an ordinary transport error with errors.Is.
var ErrBodyTooLarge = errors.New("response body exceeds maximum allowed size")

// ReadAllBounded reads from r into memory like io.ReadAll but refuses to buffer
// more than limit bytes, returning ErrBodyTooLarge instead of growing without
// bound. It is the single funnel every "read the whole body into a []byte" site
// (SDK request buffering for retries, execute response bodies, raw upstream
// bodies, release-metadata downloads) goes through so no single peer can OOM the
// process.
//
// The implementation reads limit+1 bytes: if the source had EXACTLY limit bytes
// we return them; if it had more, the extra byte trips the ceiling and we fail
// closed. A non-positive limit is treated as MaxBodyBytes so a caller can never
// accidentally disable the cap by passing 0.
func ReadAllBounded(r io.Reader, limit int64) ([]byte, error) {
	if limit <= 0 {
		limit = MaxBodyBytes
	}
	// LimitReader to limit+1 so we can detect overflow: reading the (limit+1)th
	// byte means the body was too large.
	limited := io.LimitReader(r, limit+1)
	data, err := io.ReadAll(limited)
	if err != nil {
		return data, err
	}
	if int64(len(data)) > limit {
		return nil, fmt.Errorf("%w (limit %d bytes)", ErrBodyTooLarge, limit)
	}
	return data, nil
}
