package agentops

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/client/generated/control"
)

// HeldExecution is one held execution as the control plane reports it
// (GET /executions/holds/{id}).
type HeldExecution = control.HeldExecutionDetailResponse

// HeldExecutionStatus is where a held call stands (its execution_status), or
// "" when the control plane reports none.
func HeldExecutionStatus(h *HeldExecution) string {
	if h == nil || h.ExecutionStatus == nil {
		return ""
	}
	return string(*h.ExecutionStatus)
}

// HeldExecutionFinished reports whether a held call has nothing left to wait
// for: it ran, failed or was withdrawn — or it carries no execution status
// and its decision is no longer pending. Every surface that follows a held
// call (`jentic execute --wait`, `jentic executions poll`, the MCP
// get_execution_result tool and the held short wait) decides "finished" here.
func HeldExecutionFinished(h *HeldExecution) bool {
	status := HeldExecutionStatus(h)
	if status == "" {
		return h.State != control.HeldExecutionStatePending
	}
	return IsFinishedHeldStatus(status)
}

// HeldExecutionGetter is the one generated-client method a held read needs —
// a seam so tests can drive it without the whole SDK surface.
type HeldExecutionGetter interface {
	GetHeldExecutionWithResponse(
		ctx context.Context, heldExecutionID string, reqEditors ...control.RequestEditorFn,
	) (*control.GetHeldExecutionHTTPResp, error)
}

// HeldNotFoundError is a held execution id the control plane does not know —
// or one outside the caller's visibility (a 404 either way).
type HeldNotFoundError struct {
	ID string
}

func (e *HeldNotFoundError) Error() string {
	return fmt.Sprintf("held execution %q not found", e.ID)
}

// HeldReadError is a failed read of a held execution: Err is the transport
// error, or the *HTTPError of a non-2xx answer other than 404. The held
// call's own state is unknown, unlike an outcome the control plane reported.
type HeldReadError struct {
	ID  string
	Err error
}

func (e *HeldReadError) Error() string {
	return fmt.Sprintf("failed to read held execution %s: %v", e.ID, e.Err)
}

func (e *HeldReadError) Unwrap() error { return e.Err }

// HeldUnexpectedResponseError is a 2xx read of a held execution that carried
// no held execution document.
type HeldUnexpectedResponseError struct {
	ID     string
	Status int
}

func (e *HeldUnexpectedResponseError) Error() string {
	return fmt.Sprintf("unexpected backend response reading held execution %s (status %d)", e.ID, e.Status)
}

// GetHeldExecution reads one held execution. A 404 is a *HeldNotFoundError,
// any other failure a *HeldReadError, and a 2xx with no document a
// *HeldUnexpectedResponseError.
func GetHeldExecution(ctx context.Context, client HeldExecutionGetter, heldExecutionID string) (*HeldExecution, error) {
	resp, err := client.GetHeldExecutionWithResponse(ctx, heldExecutionID)
	if err != nil {
		return nil, &HeldReadError{ID: heldExecutionID, Err: err}
	}
	if resp == nil {
		return nil, &HeldReadError{ID: heldExecutionID, Err: &HTTPError{}}
	}
	code := resp.StatusCode()
	if code == http.StatusNotFound {
		return nil, &HeldNotFoundError{ID: heldExecutionID}
	}
	if code < 200 || code >= 300 {
		return nil, &HeldReadError{
			ID:  heldExecutionID,
			Err: &HTTPError{StatusCode: code, Body: strings.TrimSpace(string(resp.Body))},
		}
	}
	if resp.JSON200 == nil {
		return nil, &HeldUnexpectedResponseError{ID: heldExecutionID, Status: code}
	}
	return resp.JSON200, nil
}

// HeldPoll is the schedule of one held-execution wait.
type HeldPoll struct {
	// Timeout bounds the whole wait; zero reads the held execution once.
	Timeout time.Duration
	// Delay is the first wait between reads; it grows by Step after each
	// wait up to MaxDelay (a zero Step keeps a fixed interval).
	Delay, MaxDelay, Step time.Duration
	// Observe, when set, runs after every successful read with the time
	// elapsed since the wait began (to learn links, or print a heartbeat).
	Observe func(held *HeldExecution, elapsed time.Duration)
}

// HeldWaitTimeoutError is the wait budget lapsing with the held call not yet
// finished; Held is the last read.
type HeldWaitTimeoutError struct {
	Held *HeldExecution
}

func (e *HeldWaitTimeoutError) Error() string {
	return fmt.Sprintf("held execution %s is still %s", e.Held.Id, HeldExecutionStatus(e.Held))
}

// HeldWaitCanceledError is the caller's context ending mid-wait; Held is the
// last read and Err the context's error.
type HeldWaitCanceledError struct {
	Held *HeldExecution
	Err  error
}

func (e *HeldWaitCanceledError) Error() string {
	return fmt.Sprintf("wait for held execution %s: %v", e.Held.Id, e.Err)
}

func (e *HeldWaitCanceledError) Unwrap() error { return e.Err }

// PollHeldExecution reads a held execution through read until it finishes
// (HeldExecutionFinished) and returns that read. The wait lapsing is a
// *HeldWaitTimeoutError and the context ending a *HeldWaitCanceledError,
// each carrying the last read; a failed read returns read's error as is.
func PollHeldExecution(
	ctx context.Context, read func(context.Context) (*HeldExecution, error), p HeldPoll,
) (*HeldExecution, error) {
	start := time.Now()
	deadline := start.Add(p.Timeout)
	delay := p.Delay
	for {
		held, err := read(ctx)
		if err != nil {
			return nil, err
		}
		if p.Observe != nil {
			p.Observe(held, time.Since(start))
		}
		if HeldExecutionFinished(held) {
			return held, nil
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return held, &HeldWaitTimeoutError{Held: held}
		}
		select {
		case <-ctx.Done():
			return held, &HeldWaitCanceledError{Held: held, Err: ctx.Err()}
		case <-time.After(min(delay, remaining)):
		}
		if delay < p.MaxDelay {
			delay = min(delay+p.Step, p.MaxDelay)
		}
	}
}
