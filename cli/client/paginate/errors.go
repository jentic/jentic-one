package paginate

import "fmt"

// tooManyPagesError is returned when a walk hits the maxPages safety bound,
// signalling a likely non-terminating cursor rather than a real result set. It's
// an error (not a silent truncation) so callers never mistake a truncated set for
// a complete one.
type tooManyPagesError struct {
	limit int
}

func (e *tooManyPagesError) Error() string {
	return fmt.Sprintf("pagination exceeded %d pages; aborting (suspected non-terminating cursor)", e.limit)
}
