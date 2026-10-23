package agentops

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/client/generated/control"
)

func heldWith(state control.HeldExecutionState, status string) *HeldExecution {
	h := &HeldExecution{Id: "held_1", State: state}
	if status != "" {
		s := control.HeldExecutionDetailResponseExecutionStatus(status)
		h.ExecutionStatus = &s
	}
	return h
}

func TestHeldExecutionFinished(t *testing.T) {
	cases := []struct {
		name   string
		state  control.HeldExecutionState
		status string
		want   bool
	}{
		{"held and pending", control.HeldExecutionStatePending, HeldStatusHeld, false},
		{"approved, queued", control.HeldExecutionStateApproved, HeldStatusQueued, false},
		{"approved, running", control.HeldExecutionStateApproved, HeldStatusRunning, false},
		{"completed", control.HeldExecutionStateApproved, HeldStatusCompleted, true},
		{"denied, failed", control.HeldExecutionStateDenied, HeldStatusFailed, true},
		{"withdrawn, cancelled", control.HeldExecutionStateWithdrawn, HeldStatusCancelled, true},
		// No execution status: the decision alone says whether to keep waiting.
		{"no status, pending", control.HeldExecutionStatePending, "", false},
		{"no status, denied", control.HeldExecutionStateDenied, "", true},
		{"no status, withdrawn", control.HeldExecutionStateWithdrawn, "", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := HeldExecutionFinished(heldWith(tc.state, tc.status)); got != tc.want {
				t.Errorf("HeldExecutionFinished = %v, want %v", got, tc.want)
			}
		})
	}
}

// reads answers each read with the next held execution (the last repeats).
func reads(seq ...*HeldExecution) (func(context.Context) (*HeldExecution, error), *int) {
	n := 0
	return func(context.Context) (*HeldExecution, error) {
		h := seq[min(n, len(seq)-1)]
		n++
		return h, nil
	}, &n
}

func TestPollHeldExecution_ReturnsTheFinishedRead(t *testing.T) {
	read, n := reads(
		heldWith(control.HeldExecutionStatePending, HeldStatusHeld),
		heldWith(control.HeldExecutionStateApproved, HeldStatusRunning),
		heldWith(control.HeldExecutionStateApproved, HeldStatusCompleted),
	)
	var observed int
	held, err := PollHeldExecution(context.Background(), read, HeldPoll{
		Timeout: time.Minute, Delay: time.Millisecond, MaxDelay: time.Millisecond,
		Observe: func(*HeldExecution, time.Duration) { observed++ },
	})
	if err != nil || HeldExecutionStatus(held) != HeldStatusCompleted {
		t.Fatalf("PollHeldExecution = %v, %v; want the completed read", held, err)
	}
	if *n != 3 || observed != 3 {
		t.Errorf("reads=%d observed=%d, want 3 each", *n, observed)
	}
}

func TestPollHeldExecution_ZeroTimeoutReadsOnce(t *testing.T) {
	read, n := reads(heldWith(control.HeldExecutionStatePending, HeldStatusHeld))
	held, err := PollHeldExecution(context.Background(), read, HeldPoll{Delay: time.Hour})
	var timedOut *HeldWaitTimeoutError
	if !errors.As(err, &timedOut) || timedOut.Held != held || held == nil {
		t.Fatalf("err = %v, want a *HeldWaitTimeoutError carrying the last read", err)
	}
	if *n != 1 {
		t.Errorf("reads = %d, want 1", *n)
	}
}

func TestPollHeldExecution_CanceledContextKeepsTheLastRead(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	read, _ := reads(heldWith(control.HeldExecutionStatePending, HeldStatusHeld))
	held, err := PollHeldExecution(ctx, read, HeldPoll{Timeout: time.Minute, Delay: time.Hour})
	var canceled *HeldWaitCanceledError
	if !errors.As(err, &canceled) || !errors.Is(err, context.Canceled) || held == nil {
		t.Fatalf("held=%v err=%v, want a *HeldWaitCanceledError over context.Canceled with the last read", held, err)
	}
}

func TestPollHeldExecution_ReadErrorIsReturnedAsIs(t *testing.T) {
	boom := errors.New("boom")
	_, err := PollHeldExecution(context.Background(),
		func(context.Context) (*HeldExecution, error) { return nil, boom },
		HeldPoll{Timeout: time.Minute})
	if !errors.Is(err, boom) {
		t.Fatalf("err = %v, want the read error", err)
	}
}

type fakeHeldGetter struct {
	resp *control.GetHeldExecutionHTTPResp
	err  error
}

func (f fakeHeldGetter) GetHeldExecutionWithResponse(
	context.Context, string, ...control.RequestEditorFn,
) (*control.GetHeldExecutionHTTPResp, error) {
	return f.resp, f.err
}

func httpResp(code int, body string, doc *HeldExecution) *control.GetHeldExecutionHTTPResp {
	return &control.GetHeldExecutionHTTPResp{
		Body:         []byte(body),
		HTTPResponse: &http.Response{StatusCode: code},
		JSON200:      doc,
	}
}

func TestGetHeldExecution(t *testing.T) {
	doc := heldWith(control.HeldExecutionStatePending, HeldStatusHeld)
	transport := errors.New("connection refused")

	held, err := GetHeldExecution(context.Background(), fakeHeldGetter{resp: httpResp(200, "{}", doc)}, "held_1")
	if err != nil || held != doc {
		t.Fatalf("200: held=%v err=%v", held, err)
	}

	_, err = GetHeldExecution(context.Background(), fakeHeldGetter{resp: httpResp(404, "", nil)}, "held_1")
	var notFound *HeldNotFoundError
	if !errors.As(err, &notFound) || notFound.ID != "held_1" {
		t.Errorf("404: err = %v, want *HeldNotFoundError", err)
	}

	_, err = GetHeldExecution(context.Background(), fakeHeldGetter{resp: httpResp(503, ` {"detail":"down"} `, nil)}, "held_1")
	var readErr *HeldReadError
	var he *HTTPError
	if !errors.As(err, &readErr) || !errors.As(err, &he) || he.StatusCode != http.StatusServiceUnavailable || he.Detail() != "down" {
		t.Errorf("503: err = %v, want a *HeldReadError over the *HTTPError", err)
	}

	_, err = GetHeldExecution(context.Background(), fakeHeldGetter{err: transport}, "held_1")
	if !errors.As(err, &readErr) || !errors.Is(err, transport) {
		t.Errorf("transport: err = %v, want a *HeldReadError over the transport error", err)
	}

	_, err = GetHeldExecution(context.Background(), fakeHeldGetter{resp: httpResp(200, "", nil)}, "held_1")
	var unexpected *HeldUnexpectedResponseError
	if !errors.As(err, &unexpected) || unexpected.Status != 200 {
		t.Errorf("200 without a document: err = %v, want *HeldUnexpectedResponseError", err)
	}

	_, err = GetHeldExecution(context.Background(), fakeHeldGetter{}, "held_1")
	if !errors.As(err, &unexpected) || unexpected.Status != 0 {
		t.Errorf("no response: err = %v, want *HeldUnexpectedResponseError with status 0", err)
	}
}
