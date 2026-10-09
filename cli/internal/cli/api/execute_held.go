package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// heldWaitDefault is how long `execute --wait` and `jobs wait` wait for a held
// call's decision by default. A reviewer has the approval's whole window (a
// day by default) to decide, so past this the command exits 3
// (TIMEOUT_PENDING) and the call stays held: waiting again later is
// meaningful.
const heldWaitDefault = 10 * time.Minute

// jobPollTimeout bounds each control-plane poll of a waited-for job.
const jobPollTimeout = 30 * time.Second

// executeHeld answers a call the broker held for human approval. Without
// --wait it prints the held envelope (the job and its review_url) as the
// execute data, shows the review link on stderr and exits 3; with --wait it
// waits for the job to settle and prints the outcome exactly like a call that
// ran at once. It never re-sends the call.
func (a *app) executeHeld(cmd *cobra.Command, opts *executeOptions, res *agentops.ExecuteResult, held *agentops.Held) error {
	ctx := cmd.Context()
	ux.RenderHeld(ctx, a.Err, ux.HeldNotice{
		JobID: held.JobID, ReviewURL: held.Approval.ReviewURL, ExpiresAt: held.Approval.ExpiresAt, Waiting: opts.wait,
	})
	if !opts.wait {
		if err := a.executeOutput(cmd, opts, res); err != nil {
			return err
		}
		return heldPendingErr(held, "")
	}
	final, err := a.waitForExecutionJob(ctx, held.JobID, opts.timeout)
	if err != nil {
		var timedOut *jobWaitTimeoutError
		if errors.As(err, &timedOut) {
			// Still held: the held envelope is the data an agent needs to
			// wait again or relay the link.
			if outErr := a.executeOutput(cmd, opts, res); outErr != nil {
				return outErr
			}
			return heldPendingErr(held, fmt.Sprintf(" after waiting %s", opts.timeout))
		}
		return err
	}
	return a.executeOutput(cmd, opts, final)
}

// heldPendingErr is the exit-3 outcome of a call still held for approval.
func heldPendingErr(held *agentops.Held, waited string) *ux.CodedError {
	return &ux.CodedError{
		Code: ux.CodeTimeoutPending,
		Msg: fmt.Sprintf("the call is held for human approval%s (job %s); it has not run",
			waited, held.JobID),
		Actionable: fmt.Sprintf("Show the user the review_url (%s) — they approve or deny the call there. "+
			"Then run `jentic jobs wait %s` for the result. Do not re-send the call.",
			held.Approval.ReviewURL, held.JobID),
		Details: map[string]any{
			"job_id":      held.JobID,
			"approval_id": held.Approval.ID,
			"review_url":  held.Approval.ReviewURL,
			"expires_at":  held.Approval.ExpiresAt,
		},
	}
}

// jobWaitTimeoutError is the wait budget lapsing with the job not yet settled.
type jobWaitTimeoutError struct {
	job *control.JobResponse
}

func (e *jobWaitTimeoutError) Error() string {
	return fmt.Sprintf("job %s is still %s", e.job.JobId, e.job.Status)
}

// waitForExecutionJob polls an execution job until it settles or timeout
// lapses (a *jobWaitTimeoutError), then returns its outcome as the ExecuteResult
// the call would have produced synchronously. Heartbeats ride stderr so they
// never corrupt the JSON stdout an agent parses.
func (a *app) waitForExecutionJob(ctx context.Context, jobID string, timeout time.Duration) (*agentops.ExecuteResult, error) {
	client, err := a.controlClient(ctx)
	if err != nil {
		return nil, err
	}
	job, err := a.pollJob(ctx, client, jobID, timeout)
	if err != nil {
		return nil, err
	}
	return jobExecuteResult(ctx, client, job)
}

// pollJob polls GET /jobs/{id} on the App's shared approval cadence until the
// job is terminal or timeout lapses.
func (a *app) pollJob(ctx context.Context, client *control.ClientWithResponses, jobID string, timeout time.Duration) (*control.JobResponse, error) {
	start := time.Now()
	deadline := start.Add(timeout)
	delay, maxDelay, step := a.PollCadence()
	const heartbeatAfter = 2 * time.Second
	nextHeartbeat := start.Add(heartbeatAfter)
	for {
		job, err := getJob(ctx, client, jobID)
		if err != nil {
			return nil, err
		}
		if agentops.IsTerminalJobStatus(job.Status) {
			return job, nil
		}
		if time.Now().After(deadline) {
			return nil, &jobWaitTimeoutError{job: job}
		}
		if now := time.Now(); now.After(nextHeartbeat) {
			fmt.Fprintln(a.Err, theme.StylesFromContext(ctx).Dimf(
				"  waiting for job %s (%s, %ds elapsed) …", jobID, job.Status, int(now.Sub(start).Seconds())))
			nextHeartbeat = now.Add(5 * time.Second)
		}
		select {
		case <-ctx.Done():
			return nil, fmt.Errorf("wait for job %s: %w", jobID, ctx.Err())
		case <-time.After(min(delay, max(time.Until(deadline), 0))):
		}
		if delay < maxDelay {
			delay += step
		}
	}
}

// getJob reads one job; an unknown id is RESOLVE_FAILED.
func getJob(ctx context.Context, client *control.ClientWithResponses, jobID string) (*control.JobResponse, error) {
	cctx, cancel := context.WithTimeout(ctx, jobPollTimeout)
	defer cancel()
	resp, callErr := client.GetJobWithResponse(cctx, jobID)
	if err := apiErrorFor(resp, callErr); err != nil {
		var he *HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
			return nil, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("job %q not found", jobID),
				Actionable: "Re-check the job id — a held execute prints it as job_id — and run " +
					"`jentic jobs wait <job_id>` with the exact value.",
			}
		}
		return nil, fmt.Errorf("failed to read job %s: %w", jobID, classifyTransportErr(err))
	}
	if resp.JSON200 == nil {
		return nil, &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  fmt.Sprintf("unexpected backend response reading job %s (status %d)", jobID, resp.StatusCode()),
		}
	}
	return resp.JSON200, nil
}

// jobExecuteResult maps a terminal execution job onto the ExecuteResult its
// call would have produced synchronously (agentops.ExecutionResultFromJob),
// or the coded error for a job that settled without one.
func jobExecuteResult(ctx context.Context, client *control.ClientWithResponses, job *control.JobResponse) (*agentops.ExecuteResult, error) {
	executionID := ""
	if job.ExecutionId != nil {
		executionID = *job.ExecutionId
	}
	switch job.Status {
	case agentops.JobCancelled:
		return nil, &ux.CodedError{
			Code: ux.CodeBrokerDenied,
			Msg:  fmt.Sprintf("job %s was cancelled (a held call's approval was withdrawn); the call never ran", job.JobId),
			Actionable: "Nothing ran. Re-send the call only if it is still wanted; " +
				"a held one files a new approval.",
			Details: map[string]any{"job_id": job.JobId, "job_status": job.Status},
		}
	case agentops.JobDeadLetter:
		return nil, &ux.CodedError{
			Code:       ux.CodeTransportError,
			Msg:        fmt.Sprintf("job %s was abandoned by the worker (%s)", job.JobId, valueOr(ptrValue(job.Error), "no error recorded")),
			Actionable: "Check `jentic history` for whether the call reached the upstream before re-sending it.",
			Details:    map[string]any{"job_id": job.JobId, "job_status": job.Status},
		}
	}
	cctx, cancel := context.WithTimeout(ctx, jobPollTimeout)
	defer cancel()
	resp, callErr := client.GetJobResultWithResponse(cctx, job.JobId)
	if err := apiErrorFor(resp, callErr); err != nil {
		var he *HTTPError
		if !errors.As(err, &he) || he.StatusCode != http.StatusConflict {
			return nil, fmt.Errorf("failed to read the result of job %s: %w", job.JobId, classifyTransportErr(err))
		}
		// A failed job with no recorded result: fall through to the job error.
	} else if res, ok := agentops.ExecutionResultFromJob(resp.HTTPResponse.Header.Get("Content-Type"), resp.Body, executionID); ok {
		return res, nil
	}
	return nil, &ux.CodedError{
		Code: ux.CodeTransportError,
		Msg: fmt.Sprintf("job %s %s without an upstream response (%s)", job.JobId, job.Status,
			valueOr(ptrValue(job.Error), "no error recorded")),
		Actionable: "The call did not get an upstream answer (a network or upstream failure). " +
			"Check `jentic history`, then re-send it if it is still wanted.",
		Details: map[string]any{"job_id": job.JobId, "job_status": job.Status},
	}
}

func ptrValue(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}
