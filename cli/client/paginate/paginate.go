// Package paginate is a small, UX-free helper for walking cursor-paginated
// control-plane list endpoints. It lets command code consume an entire result set
// (or stream it page-by-page) without re-implementing the next-cursor loop at
// every call site (impl/3.3).
//
// It is transport-agnostic: the caller supplies a PageFn that fetches ONE page for
// a given cursor and reports the items plus the next cursor. paginate owns only
// the loop, the empty-cursor stop condition, and context cancellation.
package paginate

import "context"

// Page is one fetched page: its items and the cursor for the NEXT page. A nil/empty
// Next signals the last page.
type Page[T any] struct {
	Items []T
	Next  string
}

// PageFn fetches a single page for the given cursor. The first call receives "".
type PageFn[T any] func(ctx context.Context, cursor string) (Page[T], error)

// maxPages bounds the walk so a backend that always echoes a non-empty cursor
// (or a cursor cycle) can't spin forever. Ten thousand pages is far beyond any
// legitimate interactive/agent listing.
const maxPages = 10_000

// All walks every page and returns the concatenated items. It stops on the first
// error, when the next cursor is empty, on context cancellation, or at the
// maxPages safety bound (which returns an error rather than silently truncating).
func All[T any](ctx context.Context, fetch PageFn[T]) ([]T, error) {
	var out []T
	err := ForEach(ctx, fetch, func(page Page[T]) error {
		out = append(out, page.Items...)
		return nil
	})
	return out, err
}

// ForEach walks pages and invokes yield once per page, so callers can stream
// results (e.g. print NDJSON in agent mode) without buffering everything. A
// non-nil error from yield stops the walk and is returned.
func ForEach[T any](ctx context.Context, fetch PageFn[T], yield func(Page[T]) error) error {
	cursor := ""
	for range maxPages {
		if err := ctx.Err(); err != nil {
			return err
		}
		page, err := fetch(ctx, cursor)
		if err != nil {
			return err
		}
		if err := yield(page); err != nil {
			return err
		}
		if page.Next == "" {
			return nil
		}
		cursor = page.Next
	}
	return &tooManyPagesError{limit: maxPages}
}
