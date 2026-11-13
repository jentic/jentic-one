package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// heldExecutionIDSpec names the held execution a held (202) execute carries;
// get_execution_result reads it through the holds routes.
var heldExecutionIDSpec = paramSpec{name: "held_execution_id", aliases: []string{"held_execution"}, kind: paramString}

// jobIDSpec names the job an async (202) execute carries. `execution_id` is
// NOT an alias: the broker's Jentic-Execution-Id names the execution record,
// not the job — folding them would silently poll the wrong resource.
var jobIDSpec = paramSpec{name: "job_id", aliases: []string{"id", "job"}, kind: paramString}

// timeoutSpec is get_execution_result's bounded wait, in whole seconds: the
// tool re-polls until the call has finished or the timeout lapses. Absent is
// defaultPollTimeoutSeconds; zero (or negative) polls once and answers at
// once; above maxPollTimeoutSeconds clamps to it.
var timeoutSpec = paramSpec{name: "timeout", kind: paramInt}

const (
	// defaultPollTimeoutSeconds is get_execution_result's wait when timeout
	// is absent.
	defaultPollTimeoutSeconds = 30
	// maxPollTimeoutSeconds caps get_execution_result's wait, inside a
	// host's tool-call timeout.
	maxPollTimeoutSeconds = 30
)

// getExecutionResultParams declares the poll tool's argument table: exactly
// one of held_execution_id and job_id, plus the optional timeout.
var getExecutionResultParams = []paramSpec{heldExecutionIDSpec, jobIDSpec, timeoutSpec}

// pollTimeout is get_execution_result's wait for its arguments: the timeout
// argument in seconds, clamped to [0, maxPollTimeoutSeconds], or
// defaultPollTimeoutSeconds when absent.
func pollTimeout(args map[string]any) time.Duration {
	secs := defaultPollTimeoutSeconds
	if v, ok := args["timeout"].(int); ok {
		secs = min(max(v, 0), maxPollTimeoutSeconds)
	}
	return time.Duration(secs) * time.Second
}

// executeResultPayload projects an ExecuteResult onto the tool payload: the
// exact CLI envelope keys ({schema_version, status, headers, body,
// execution_id} — shared goldens compare this sub-object with the sibling
// `instance` stamp stripped), with the §3.7 size cap applied to the body AND
// an aggregate budget applied to the headers (Go's HTTP/1 transport accepts
// up to 10 MiB of response headers — without their own budget they would ride
// straight around the body cap). A capped body is replaced by its leading
// maxResultBytes bytes — sanitized to valid UTF-8, which also strips invalid
// sequences a binary body carries anywhere — as a string plus the truncation
// envelope {truncated: true, total_bytes, execution_id} so the model can
// fetch or act deliberately instead of flooding its context. Normal-sized
// responses are relayed byte-identically to the CLI envelope.
func (s *mcpServer) executeResultPayload(res *agentops.ExecuteResult) map[string]any {
	env := res.Envelope()
	headers, headersTruncated := capHeaders(env.Headers, s.headerBytesBudget())
	payload := map[string]any{
		"schema_version": env.SchemaVersion,
		"status":         env.Status,
		"headers":        headers,
		"body":           env.Body,
	}
	if headersTruncated {
		payload["headers_truncated"] = true
		s.logger.Info("execute response headers truncated", "cap", s.headerBytesBudget())
	}
	if env.ExecutionID != "" {
		payload["execution_id"] = env.ExecutionID
	}
	if int64(len(res.Body)) > s.maxResultBytes {
		payload["body"] = strings.ToValidUTF8(string(res.Body[:s.maxResultBytes]), "")
		payload["truncated"] = true
		payload["total_bytes"] = len(res.Body)
		s.logger.Info("execute result truncated", "total_bytes", len(res.Body), "cap", s.maxResultBytes)
	}
	return payload
}

// headerBytesBudget is the slice of the §3.7 cap reserved for relayed
// response headers: 8 KiB — generous for any sane API's headers — clamped to
// the whole result cap when the operator shrank --max-result-bytes below it.
func (s *mcpServer) headerBytesBudget() int64 {
	const headerBudget int64 = 8 << 10
	if s.maxResultBytes < headerBudget {
		return s.maxResultBytes
	}
	return headerBudget
}

// capHeaders bounds the aggregate serialized size of the relayed response
// headers to budget bytes. Headers are admitted whole in sorted key order; a
// header that would overflow the remaining budget is dropped whole (a
// half-relayed value is worse than an absent one) while smaller later headers
// still fit — one hostile fat header must not evict Content-Type. When
// everything fits (every normal response), the input map is returned
// UNTOUCHED so the shared CLI-envelope bytes cannot change.
func capHeaders(headers map[string]string, budget int64) (map[string]string, bool) {
	var total int64
	for k, v := range headers {
		total += headerCost(k, v)
	}
	if total <= budget {
		return headers, false
	}
	keys := make([]string, 0, len(headers))
	for k := range headers {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	capped := make(map[string]string, len(keys))
	var used int64
	for _, k := range keys {
		cost := headerCost(k, headers[k])
		if used+cost > budget {
			continue
		}
		used += cost
		capped[k] = headers[k]
	}
	return capped, true
}

// headerCost estimates one header's serialized JSON size (pre-escaping —
// close enough for a budget that sits far below the body cap).
func headerCost(k, v string) int64 {
	const perEntryOverhead = 6 // two quote pairs, colon, comma
	return int64(len(k) + len(v) + perEntryOverhead)
}

// handleGetExecutionResult polls a held execution or a job through the LIVE
// control-plane routes. A held_execution_id (from a held 202) reads the held
// execution itself — GET /executions/holds/{id} for where the call stands,
// plus its /result once the call has finished — and needs no jobs:*
// permission. A job_id (from an async 202) reads GET /jobs/{id}, plus
// GET /jobs/{id}/result once the job completed (agent tokens hold jobs:read
// by default — §3.4/§3.2). Either way this is the recovery path for a 202
// execute: poll until finished, never re-send the execute.
func (s *mcpServer) handleGetExecutionResult(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())

	args, err := normalizeToolArgs(req.Params.Arguments, getExecutionResultParams)
	if err != nil {
		return nil, invalidParams(err)
	}
	heldExecutionID, _ := args["held_execution_id"].(string)
	jobID, _ := args["job_id"].(string)
	if (heldExecutionID == "") == (jobID == "") {
		return nil, invalidParams(errors.New(`get_execution_result requires exactly one of "held_execution_id" ` +
			`(from a held 202 execute response) or "job_id" (aliases: "id", "job"; from an async one)`))
	}
	wait := pollTimeout(args)

	// The call deadline covers the wait on top of the control-plane budget,
	// so a full wait still leaves room for the last poll.
	cctx, cancel := context.WithTimeout(refreshActiveState(ctx), mcpCallTimeout+wait)
	defer cancel()

	poll, target := s.jobPollPayload, jobID
	if heldExecutionID != "" {
		// The review link a successful read reports stays known across the
		// wait, so a later failed read can still hand it back.
		var reviewURL string
		poll = func(ctx context.Context, id string) (map[string]any, *mcp.CallToolResult) {
			return s.heldPollPayload(ctx, id, &reviewURL)
		}
		target = heldExecutionID
	}
	payload, soft := s.pollUntilFinished(cctx, poll, target, wait)
	if soft != nil {
		return soft, nil
	}
	return s.result(refreshActiveState(cctx), payload), nil
}

// pollPayloadFunc reads one poll payload for a held execution or job id; a
// failure comes back as the soft-error result to return instead.
type pollPayloadFunc func(ctx context.Context, id string) (map[string]any, *mcp.CallToolResult)

// pollUntilFinished polls id, re-polling every heldWaitPoll until its status
// is terminal or wait lapses; a zero wait polls exactly once.
func (s *mcpServer) pollUntilFinished(
	ctx context.Context, poll pollPayloadFunc, id string, wait time.Duration,
) (map[string]any, *mcp.CallToolResult) {
	interval := s.heldWaitPoll
	if interval <= 0 {
		interval = heldWaitPoll
	}
	deadline := time.Now().Add(wait)
	for {
		payload, soft := poll(refreshActiveState(ctx), id)
		if soft != nil || isTerminalJobStatus(payload["status"]) {
			return payload, soft
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return payload, nil
		}
		select {
		case <-ctx.Done():
			return payload, nil
		case <-time.After(min(interval, remaining)):
		}
	}
}

// isTerminalJobStatus reports whether a polled status is final. A held
// execution's statuses are a subset of a job's (a dead-lettered held call
// reads as failed).
func isTerminalJobStatus(status any) bool {
	switch status {
	case catJobCompleted, catJobFailed, catJobCancelled, catJobDeadLetter:
		return true
	}
	return false
}

// heldPollPayload polls one held execution — GET /executions/holds/{id},
// plus its /result once the call has finished (completed, or failed: the
// upstream response, or the problem explaining why it never ran). The
// payload is {schema_version, held_execution_id, state, status, review_url,
// expires_at, execution_id?, result? | result_error? | truncated+total_bytes,
// agent_directive?}: state is the decision, status where the call stands,
// and agent_directive (stillHeldAgentDirective) rides only while the call
// has not finished. review_url is omitted when the control plane sends none.
// *reviewURL carries the last review link seen, so a failed read still
// names it. Kept in parity with the mounted app's held_execution_poll_payload.
func (s *mcpServer) heldPollPayload(cctx context.Context, heldExecutionID string, reviewURL *string) (map[string]any, *mcp.CallToolResult) {
	client, err := s.app.controlClient(cctx)
	if err != nil {
		s.logger.Warn("get_execution_result failed", "error", redactedErr(err))
		return nil, s.heldPollError(cctx, heldExecutionID, *reviewURL, err)
	}
	resp, callErr := client.GetHeldExecutionWithResponse(cctx, heldExecutionID)
	if err := apiErrorFor(resp, callErr); err != nil {
		var he *HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
			// The identity resolved fine — the id is wrong (or not this
			// agent's), so the recovery is re-reading the held execute
			// envelope and calling this tool again (self-pointer).
			return nil, s.softErrorNext(cctx, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("held execution %q not found", heldExecutionID),
				Actionable: "Re-check the id — it is the held_execution_id of the held (202) execute response — " +
					"and call get_execution_result again with the exact value.",
				Details: map[string]any{"held_execution_id": heldExecutionID},
			}, "get_execution_result")
		}
		s.logger.Warn("get_execution_result failed", "held_execution_id", heldExecutionID, "error", redactedErr(err))
		return nil, s.heldPollError(cctx, heldExecutionID, *reviewURL, err)
	}
	if resp.JSON200 == nil {
		return nil, s.softError(cctx, &ux.CodedError{
			Code:    ux.CodeInternalError,
			Msg:     fmt.Sprintf("unexpected backend response (status %d)", resp.StatusCode()),
			Details: map[string]any{"held_execution_id": heldExecutionID},
		})
	}

	held := resp.JSON200
	if held.ReviewUrl != "" {
		*reviewURL = held.ReviewUrl
	}
	var status any
	if held.ExecutionStatus != nil {
		status = string(*held.ExecutionStatus)
	}
	payload := map[string]any{
		"schema_version":    mcpSchemaVersion,
		"held_execution_id": held.Id,
		"state":             string(held.State),
		"status":            status,
		"expires_at":        held.ExpiresAt.UTC().Format(time.RFC3339Nano),
	}
	if held.ReviewUrl != "" {
		payload["review_url"] = held.ReviewUrl
	}
	if held.ExecutionId != nil && *held.ExecutionId != "" {
		payload["execution_id"] = *held.ExecutionId
	}
	if status == agentops.HeldStatusCompleted || status == agentops.HeldStatusFailed {
		s.attachHeldResult(cctx, client, heldExecutionID, payload)
	}
	if !isTerminalJobStatus(status) {
		payload["agent_directive"] = stillHeldAgentDirective
	}
	return payload, nil
}

// heldPollError is a failed read of a held execution as a soft error that
// still names the held execution (and its review link, when known), so the
// agent can relay the link and poll again. The poll is a GET: a transport
// failure is always retryable (§3.7 transport row).
func (s *mcpServer) heldPollError(ctx context.Context, heldExecutionID, reviewURL string, err error) *mcp.CallToolResult {
	coded := mcpCoded(classifyTransportErr(err))
	details := map[string]any{"held_execution_id": heldExecutionID}
	if reviewURL != "" {
		details["review_url"] = reviewURL
	}
	for k, v := range coded.Details {
		if _, ok := details[k]; !ok {
			details[k] = v
		}
	}
	wrapped := &ux.CodedError{
		Code:       coded.Code,
		Msg:        fmt.Sprintf("failed to read held execution %s: %s", heldExecutionID, coded.Msg),
		Actionable: coded.Actionable,
		Details:    details,
		Cause:      coded.Cause,
	}
	if coded.Code != ux.CodeTransportError {
		return s.softError(ctx, wrapped)
	}
	wrapped.Actionable = "The held call is unaffected: it may still be waiting for a human decision. " +
		"If the user has not approved it yet, show them the review_url again; then call " +
		"get_execution_result again with this held_execution_id. Do not re-send the call."
	return s.softErrorExtra(ctx, wrapped, "get_execution_result", map[string]any{"retryable": true})
}

// attachHeldResult adds a finished held call's result to the payload
// (GET /executions/holds/{id}/result), size-capped like every relayed body.
// A 409 (a failed call with no recorded result) attaches nothing; any other
// fetch failure degrades to a `result_error` note rather than failing the
// poll — the status the model asked for is already in hand.
func (s *mcpServer) attachHeldResult(ctx context.Context, client heldResultClient, heldExecutionID string, payload map[string]any) {
	resp, err := client.GetHeldExecutionResultWithResponse(ctx, heldExecutionID)
	if err := apiErrorFor(resp, err); err != nil {
		var he *HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusConflict {
			return
		}
		s.logger.Warn("get_execution_result: result fetch failed", "held_execution_id", heldExecutionID, "error", redactedErr(err))
		payload["result_error"] = "the call finished but its result could not be fetched: " + redactedErr(err)
		return
	}
	s.attachResultBody(resp.Body, payload)
}

// heldResultClient is the one generated-client method attachHeldResult
// needs — a seam so tests can drive the result fetch without the whole SDK.
type heldResultClient interface {
	GetHeldExecutionResultWithResponse(ctx context.Context, heldExecutionID string, reqEditors ...control.RequestEditorFn) (*control.GetHeldExecutionResultHTTPResp, error)
}

// jobPollPayload polls one job — GET /jobs/{id}, plus GET /jobs/{id}/result
// once the job completed. A failure comes back as the soft-error result to
// return instead.
func (s *mcpServer) jobPollPayload(cctx context.Context, jobID string) (map[string]any, *mcp.CallToolResult) {
	client, err := s.app.controlClient(cctx)
	if err != nil {
		s.logger.Warn("get_execution_result failed", "error", redactedErr(err))
		return nil, s.softError(cctx, err)
	}
	resp, jobErr := client.GetJobWithResponse(cctx, jobID)
	if err := apiErrorFor(resp, jobErr); err != nil {
		var he *HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
			// The identity resolved fine — the job id is wrong, so the
			// get_started default would be a dead end. The recovery is
			// re-reading the async execute envelope and calling this tool
			// again (self-pointer, deliberate).
			return nil, s.softErrorNext(cctx, &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg:  fmt.Sprintf("job %q not found", jobID),
				Actionable: "Re-check the job id — it is carried by the async (202) execute response — and call " +
					"get_execution_result again with the exact value.",
			}, "get_execution_result")
		}
		s.logger.Warn("get_execution_result failed", "job_id", jobID, "error", redactedErr(err))
		// §3.7 transport row: a transport failure on the RECOVERY path ("the
		// control plane is briefly down, poll again") must come back as a
		// retryable TRANSPORT_ERROR with the get_started pointer, never as
		// INTERNAL_ERROR. The poll is a GET — re-polling is always safe.
		return nil, s.executeTransportError(cctx, classifyTransportErr(err), true)
	}
	if resp.JSON200 == nil {
		return nil, s.softError(cctx, &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  fmt.Sprintf("unexpected backend response (status %d)", resp.StatusCode()),
		})
	}

	job := resp.JSON200
	payload := map[string]any{
		"schema_version": mcpSchemaVersion,
		"job_id":         job.JobId,
		"kind":           job.Kind,
		"status":         job.Status,
	}
	if job.Error != nil && *job.Error != "" {
		payload["error"] = *job.Error
	}
	if job.ExecutionId != nil && *job.ExecutionId != "" {
		payload["execution_id"] = *job.ExecutionId
	}
	if job.Status == catJobCompleted {
		s.attachJobResult(cctx, client, jobID, payload)
	}
	return payload, nil
}

// attachJobResult adds the completed job's result document to the payload
// (GET /jobs/{id}/result), size-capped like every relayed body. A result
// fetch failure degrades to a `result_error` note rather than failing the
// poll — the status the model asked for is already in hand.
func (s *mcpServer) attachJobResult(ctx context.Context, client jobResultClient, jobID string, payload map[string]any) {
	resp, err := client.GetJobResultWithResponse(ctx, jobID)
	if err := apiErrorFor(resp, err); err != nil {
		s.logger.Warn("get_execution_result: result fetch failed", "job_id", jobID, "error", redactedErr(err))
		payload["result_error"] = "the job completed but its result could not be fetched: " + redactedErr(err)
		return
	}
	s.attachResultBody(resp.Body, payload)
}

// attachResultBody sets payload["result"] from a fetched result body: the
// decoded JSON, else the text; a body over maxResultBytes is its leading
// maxResultBytes bytes, sanitized to valid UTF-8, with {truncated, total_bytes}.
func (s *mcpServer) attachResultBody(body []byte, payload map[string]any) {
	if int64(len(body)) > s.maxResultBytes {
		payload["result"] = strings.ToValidUTF8(string(body[:s.maxResultBytes]), "")
		payload["truncated"] = true
		payload["total_bytes"] = len(body)
		return
	}
	var result any
	if len(body) > 0 && json.Unmarshal(body, &result) == nil {
		payload["result"] = result
	} else if len(body) > 0 {
		payload["result"] = string(body)
	}
}

// jobResultClient is the one generated-client method attachJobResult needs —
// a seam so tests can drive the result fetch without the whole SDK surface.
type jobResultClient interface {
	GetJobResultWithResponse(ctx context.Context, jobID string, reqEditors ...control.RequestEditorFn) (*control.GetJobResultHTTPResp, error)
}
