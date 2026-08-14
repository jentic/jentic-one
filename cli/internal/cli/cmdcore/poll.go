package cmdcore

import "time"

// pollCadence is the backoff schedule for an approval/refresh poll loop: the
// initial wait, the cap it grows toward, and the per-iteration step. It is
// threaded on App (rather than package-global vars) so timing is per-instance
// and tests shrink it by constructing an App with a fast cadence instead of
// mutating shared globals (AR2-5).
type pollCadence struct {
	initial time.Duration
	max     time.Duration
	step    time.Duration
}

// defaultPollCadence is the production timing: a 2s first wait growing by 1s per
// iteration up to a 10s cap. A zero-value cadence (e.g. a test App built as
// &App{…} without setting one) resolves to this via PollCadence.
var defaultPollCadence = pollCadence{
	initial: 2 * time.Second,
	max:     10 * time.Second,
	step:    1 * time.Second,
}

// PollCadence returns the App's approval-poll cadence, falling back to the
// production defaults for any field left zero. Exported so sibling command
// packages (api, ctlcmd) that embed App can share the exact same schedule.
func (a *App) PollCadence() (initial, max, step time.Duration) {
	c := a.poll
	if c.initial == 0 {
		c.initial = defaultPollCadence.initial
	}
	if c.max == 0 {
		c.max = defaultPollCadence.max
	}
	if c.step == 0 {
		c.step = defaultPollCadence.step
	}
	return c.initial, c.max, c.step
}

// SetPollCadence overrides the approval-poll schedule (tests use it to make the
// pending-path cases near-instant). Any zero argument keeps the default for
// that field.
func (a *App) SetPollCadence(initial, max, step time.Duration) {
	a.poll = pollCadence{initial: initial, max: max, step: step}
}
