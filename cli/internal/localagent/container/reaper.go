package container

import (
	"context"
	"errors"
	"sort"
	"strings"
)

// Liveness reports whether the client that created a session still runs. The
// command layer answers it from the session's lock file: a live client holds
// the lock, and the kernel releases it when the client dies, SIGKILL
// included.
type Liveness func(sessionID string) bool

// Reap tears down every session of owner whose client is no longer alive and
// returns the ids it removed. Sessions of other owners, and live ones, are
// left alone. A failed teardown is reported but does not stop the others.
func Reap(ctx context.Context, d Docker, owner string, alive Liveness) ([]string, error) {
	ids, err := sessionsOf(ctx, d, owner)
	if err != nil {
		return nil, err
	}
	var reaped []string
	var errs []error
	for _, id := range ids {
		if alive(id) {
			continue
		}
		if err := Teardown(ctx, d, id); err != nil {
			errs = append(errs, err)
			continue
		}
		reaped = append(reaped, id)
	}
	return reaped, errors.Join(errs...)
}

// sessionsOf lists the session ids of owner's containers, networks and
// volumes, sorted and deduplicated. A label value that is not a session id is
// skipped, never passed on to a filter.
func sessionsOf(ctx context.Context, d Docker, owner string) ([]string, error) {
	filter := "label=" + LabelOwner + "=" + owner
	format := `{{.Label "` + LabelSession + `"}}`
	seen := map[string]bool{}
	for _, list := range [][]string{
		{"ps", "--all", "--filter", filter, "--format", format},
		{"network", "ls", "--filter", filter, "--format", format},
		{"volume", "ls", "--filter", filter, "--format", format},
	} {
		out, err := d.Output(ctx, list...)
		if err != nil {
			return nil, err
		}
		for _, id := range strings.Fields(out) {
			if ValidSessionID(id) {
				seen[id] = true
			}
		}
	}
	ids := make([]string, 0, len(seen))
	for id := range seen {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids, nil
}
