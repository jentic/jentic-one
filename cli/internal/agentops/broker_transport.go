package agentops

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// BrokerTransportRecovery names how an agent recovers from a broker-origin 5xx
// or 429. The CLI actionable prose, the MCP next_tool and the retryable hint
// all key on it, so the lanes never disagree about whether a retry is safe.
type BrokerTransportRecovery string

const (
	// RecoverWait means the broker failed the call before the upstream could
	// act on it (circuit open, runner degraded, admission shed, resolution
	// overloaded, rate limit, transient credential refresh, connect failure):
	// re-sending the same call after a wait is safe for any method, because
	// the upstream never saw it.
	RecoverWait BrokerTransportRecovery = "wait"
	// RecoverTimeout means the broker ran out of time on the call. The request
	// may already have reached the upstream, so a retry is only safe when the
	// call cannot double-execute (GET/HEAD, or an Idempotency-Key).
	RecoverTimeout BrokerTransportRecovery = "timeout"
	// RecoverScheme means the broker has no runner for the upstream's URL
	// scheme: re-sending cannot succeed, the agent needs a different operation.
	RecoverScheme BrokerTransportRecovery = "scheme"
	// RecoverUnknown is any other broker 5xx. Its type does not say whether the
	// failure happened before or after the upstream call, so it is never
	// advertised as retryable.
	RecoverUnknown BrokerTransportRecovery = "unknown"
)

// brokerTransientProblemTypes are the wire types the broker emits when it fails
// a call before the upstream could act on it, and that a later identical call
// can succeed on. Matched exactly against the broker's own identifiers:
// circuit_open (CircuitOpenError, 503 + Retry-After), runner_unavailable
// (RunnerUnavailableError, 503), server_at_capacity (the admission middleware's
// 503 + Retry-After), too_many_candidates (TooManyCandidatesError, 503:
// operation resolution overloaded, before dispatch), refresh_transient_error
// (CredentialRefreshTransientError, 502: the token refresh failed before
// dispatch), upstream_connect_failed (UpstreamTimeoutError raised in the connect
// phase, 504: no request bytes were sent) and rate_limit_exceeded
// (RateLimitExceededError, 429 + Retry-After).
var brokerTransientProblemTypes = map[string]bool{
	"circuit_open":            true,
	"runner_unavailable":      true,
	"server_at_capacity":      true,
	"too_many_candidates":     true,
	"refresh_transient_error": true,
	"upstream_connect_failed": true,
	"rate_limit_exceeded":     true,
}

// brokerTimeoutProblemTypes are the wire types for a call the broker ran out of
// time on after sending it: upstream_timeout (UpstreamTimeoutError, 504: one
// attempt's read timed out) and deadline_exceeded (DeadlineExceededError, 504:
// the overall deadline).
var brokerTimeoutProblemTypes = map[string]bool{
	"upstream_timeout":  true,
	"deadline_exceeded": true,
}

// BrokerTransportRecoveryFor routes a broker-origin 5xx/429 by status and exact
// problem type. Any 429 is a wait (the status itself means "slow down, then
// retry"). The timeout types may have reached the upstream.
// runner_scheme_unsupported (RunnerSchemeUnsupportedError, 501) needs a
// different operation. Everything else is unknown, including
// upstream_response_too_large (UpstreamResponseTooLargeError, 502: the upstream
// answered, so the call ran) and upstream_transport_error (the connection failed
// mid-request).
func BrokerTransportRecoveryFor(status int, problemType string) BrokerTransportRecovery {
	switch {
	case status == http.StatusTooManyRequests || brokerTransientProblemTypes[problemType]:
		return RecoverWait
	case brokerTimeoutProblemTypes[problemType]:
		return RecoverTimeout
	case problemType == "runner_scheme_unsupported":
		return RecoverScheme
	default:
		return RecoverUnknown
	}
}

// BrokerTransportRetryable reports whether re-sending the same call is safe for
// a recovery class. retrySafe is the caller's proof that the call cannot
// double-execute (see RetrySafe); only a timeout depends on it.
func BrokerTransportRetryable(rec BrokerTransportRecovery, retrySafe bool) bool {
	switch rec {
	case RecoverWait:
		return true
	case RecoverTimeout:
		return retrySafe
	default:
		return false
	}
}

// RetrySafe reports whether re-sending a call cannot double-execute it: a
// GET/HEAD (case-insensitive), or a call carrying a non-blank Idempotency-Key
// the broker de-duplicates on. Pass the key that rides the wire (the built
// request's header), so a key supplied via a custom header counts too. The MCP
// mount's twin is jentic_one.mcp.execute.retry_safe.
func RetrySafe(method, idempotencyKey string) bool {
	return strings.TrimSpace(idempotencyKey) != "" ||
		strings.EqualFold(method, http.MethodGet) || strings.EqualFold(method, http.MethodHead)
}

// BrokerTransportError classifies a 5xx or 429 the broker itself generated (its
// circuit breaker is open, a runner is degraded, it shed the call, a token
// refresh failed, it timed out or hit its deadline, it aborted an oversized
// response, it cannot carry the scheme). It is a TRANSPORT_ERROR (exit 1), not an
// upstream response that exits 0 (#1531). Details carry http_status, origin,
// problem_type, the retryable hint and, when the broker sent Retry-After,
// retry_after_s.
//
// Like BrokerError it requires Jentic-Error-Origin to say "broker" explicitly.
// The header decides, never the body: a vendor 5xx the broker mirrored is
// stamped "upstream" and is the caller's data, and a missing header keeps the
// same pass-through reading. Every error the broker generates itself is stamped
// "broker", including its timeout, deadline, response-size and refresh failures
// (#1675).
func BrokerTransportError(r *ExecuteResult, retrySafe bool) *ux.CodedError {
	if r == nil || (r.Status < 500 && r.Status != http.StatusTooManyRequests) ||
		errorOrigin(r) != errorOriginBroker {
		return nil
	}
	var problem struct {
		Type   string          `json:"type"`
		Title  string          `json:"title"`
		Detail json.RawMessage `json:"detail"`
	}
	_ = json.Unmarshal(r.Body, &problem) // best effort: a non-JSON body falls back to the status text
	reason := problemReason(problem.Detail, problem.Title, nil, r.Status)
	rec := BrokerTransportRecoveryFor(r.Status, problem.Type)
	retryable := BrokerTransportRetryable(rec, retrySafe)
	details := map[string]any{
		"http_status": r.Status,
		"origin":      errorOriginBroker,
		"retryable":   retryable,
	}
	if problem.Type != "" {
		details["problem_type"] = problem.Type
	}
	retryAfter, hasRetryAfter := RetryAfterSeconds(r.Headers)
	if hasRetryAfter {
		details["retry_after_s"] = retryAfter
	}
	return &ux.CodedError{
		Code:       ux.CodeTransportError,
		Msg:        fmt.Sprintf("the broker could not complete this call (HTTP %d): %s", r.Status, reason),
		Actionable: brokerTransportActionable(rec, retryable, retryAfter, hasRetryAfter),
		Details:    details,
	}
}

// RetryAfterSeconds parses a Retry-After header (delta-seconds, or an HTTP-date
// converted to the seconds remaining, floored at zero). ok is false when the
// header is absent or unparseable, so callers never relay a made-up delay.
func RetryAfterSeconds(h http.Header) (float64, bool) {
	v := strings.TrimSpace(h.Get("Retry-After"))
	if v == "" {
		return 0, false
	}
	if secs, err := strconv.ParseFloat(v, 64); err == nil {
		if secs < 0 || math.IsNaN(secs) || math.IsInf(secs, 0) {
			return 0, false
		}
		return secs, true
	}
	if when, err := http.ParseTime(v); err == nil {
		return math.Max(0, math.Ceil(time.Until(when).Seconds())), true
	}
	return 0, false
}

// waitPhrase renders the wait a retry should honour: the broker's Retry-After
// when it sent one, else a bounded "briefly".
func waitPhrase(retryAfter float64, ok bool) string {
	if ok {
		return fmt.Sprintf("Wait %ss (the broker's Retry-After)", strconv.FormatFloat(retryAfter, 'f', -1, 64))
	}
	return "Wait briefly"
}

// brokerTransportActionable is the CLI recovery prose for each
// BrokerTransportRecovery. The root error reporter renders it once on stderr
// (and as actionable_step in the agent envelope).
func brokerTransportActionable(rec BrokerTransportRecovery, retryable bool, retryAfter float64, hasRetryAfter bool) string {
	switch {
	case rec == RecoverWait:
		return waitPhrase(retryAfter, hasRetryAfter) + " and re-run the same `jentic execute`: the broker refused " +
			"the call before it reached the upstream API, so a retry cannot double-execute it. If it keeps " +
			"failing, report it to your operator: the broker is degraded."
	case rec == RecoverTimeout && retryable:
		return waitPhrase(retryAfter, hasRetryAfter) + " and re-run the same `jentic execute` (it is a read or " +
			"carries an Idempotency-Key, so a retry cannot double-execute it)."
	case rec == RecoverScheme:
		return "The broker has no runner for this upstream's URL scheme, so re-sending cannot succeed. Use an " +
			"operation served over http(s) — find one with `jentic search <query>`."
	default:
		return "Do not re-run this call blindly: the broker failed it in a way that may have reached the " +
			"upstream API. Verify the effect with a read first, or re-run with --idempotency-key so the broker " +
			"de-duplicates the retry. If it keeps failing, report it to your operator."
	}
}
