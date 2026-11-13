package agentops

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/mail"
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
// dispatch), upstream_connect_failed (raised in the connect phase — 504
// UpstreamTimeoutError on a timeout, 502 UpstreamTransportError on a refused
// connection: no request bytes were sent) and rate_limit_exceeded
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
// answered, so the call ran), upstream_transport_error
// (UpstreamTransportError, 502: the connection failed mid-request) and
// upstream_request_invalid (500: the broker built a request its HTTP client
// refused to send, so re-sending the same call will fail the same way).
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

// maxRetryAfterSeconds caps a Retry-After wait at one day, matching
// jentic_one.shared.http.retry_after.MAX_RETRY_AFTER_S (#1991).
const maxRetryAfterSeconds = 86400

// RetryAfterSeconds parses a Retry-After header to whole seconds (matching
// jentic_one.shared.http.retry_after.parse_retry_after): non-negative ASCII
// decimal delay-seconds (a fraction rounds up; a sign, exponent or non-ASCII
// digit is rejected), or an HTTP-date converted to the seconds remaining
// (floored at zero), capped at one day (86400s). ok is false when the header is
// absent or unparseable, so callers never relay a made-up delay.
func RetryAfterSeconds(h http.Header) (float64, bool) {
	return retryAfterSecondsAt(h, time.Now())
}

func retryAfterSecondsAt(h http.Header, now time.Time) (float64, bool) {
	v := strings.TrimSpace(h.Get("Retry-After"))
	if v == "" {
		return 0, false
	}
	if secs, ok := parseDelaySeconds(v); ok {
		return float64(secs), true
	}
	if when, err := parseRetryAfterDate(v); err == nil {
		delta := when.Sub(now)
		if delta <= 0 {
			return 0, true
		}
		if delta >= maxRetryAfterSeconds*time.Second {
			return maxRetryAfterSeconds, true
		}
		secs := int(delta / time.Second)
		if delta%time.Second != 0 {
			secs++
		}
		return float64(secs), true
	}
	return 0, false
}

// retryAfterDateCaseFolder matches Python's case-insensitive weekday, month,
// and GMT parsing without changing any other date text.
var retryAfterDateCaseFolder = strings.NewReplacer(
	"mon", "Mon", "tue", "Tue", "wed", "Wed", "thu", "Thu",
	"fri", "Fri", "sat", "Sat", "sun", "Sun",
	"jan", "Jan", "feb", "Feb", "mar", "Mar", "apr", "Apr",
	"may", "May", "jun", "Jun", "jul", "Jul", "aug", "Aug",
	"sep", "Sep", "oct", "Oct", "nov", "Nov", "dec", "Dec",
	"gmt", "GMT",
)

// parseRetryAfterDate accepts the standard HTTP-date layouts and the numeric,
// colon-zone, -0000, case-insensitive, and zone-less forms that Python's
// email.utils parser accepts. A zone-less value is pinned to UTC, never the
// host's local timezone.
func parseRetryAfterDate(raw string) (time.Time, error) {
	var err error
	for _, candidate := range []string{raw, normalizeRetryAfterDate(raw)} {
		var when time.Time
		if when, err = http.ParseTime(candidate); err == nil {
			return when, nil
		}
		if when, err = mail.ParseDate(candidate); err == nil {
			return when, nil
		}
		if when, err = mail.ParseDate(candidate + " -0000"); err == nil {
			return when, nil
		}
	}
	return time.Time{}, err
}

func normalizeRetryAfterDate(raw string) string {
	normalized := retryAfterDateCaseFolder.Replace(strings.ToLower(raw))
	if len(normalized) < 6 {
		return normalized
	}
	zoneAt := len(normalized) - 6
	zone := normalized[zoneAt:]
	if (zone[0] == '+' || zone[0] == '-') && zone[3] == ':' &&
		isASCIIDigits(zone[1:3]) && isASCIIDigits(zone[4:]) {
		return normalized[:zoneAt] + zone[:3] + zone[4:]
	}
	return normalized
}

// parseDelaySeconds parses an unsigned ASCII decimal delay (^[0-9]+(\.[0-9]+)?$),
// rounding any non-zero fraction up and capping at maxRetryAfterSeconds without
// overflowing on arbitrarily long digit strings.
func parseDelaySeconds(raw string) (int, bool) {
	whole, frac, hasDot := strings.Cut(raw, ".")
	if !isASCIIDigits(whole) {
		return 0, false
	}
	if hasDot && !isASCIIDigits(frac) {
		return 0, false
	}
	whole = strings.TrimLeft(whole, "0")
	if len(whole) > 5 { // len("86400") == 5
		return maxRetryAfterSeconds, true
	}
	secs := 0
	for i := range len(whole) {
		secs = secs*10 + int(whole[i]-'0')
	}
	if strings.TrimLeft(frac, "0") != "" {
		secs++
	}
	if secs > maxRetryAfterSeconds {
		return maxRetryAfterSeconds, true
	}
	return secs, true
}

func isASCIIDigits(s string) bool {
	if s == "" {
		return false
	}
	for i := range len(s) {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
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
