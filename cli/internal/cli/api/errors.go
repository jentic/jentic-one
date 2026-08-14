package api

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// reportCoded is the error path for the context/env/identity/migrate commands:
// it routes the failure through the Audience (structured AgentError JSON on
// stderr for agents, a styled line for humans) and returns a *ux.CodedError so
// core.Run mirrors the exit code from the taxonomy AND suppresses its own generic
// "error:" line (a *ux.CodedError satisfies core.ExitCoder, so Run does not print
// it — avoiding a double report). Non-coded errors are wrapped as INTERNAL_ERROR.
//
// This is the sanctioned command-side error contract for the new surface: never
// fmt.Fprintln to stderr directly, never return a bare error (which would print
// unstructured text and skip the envelope in agent mode).
func reportCoded(aud ux.Audience, err error) error {
	if err == nil {
		return nil
	}
	coded := asCoded(err)
	aud.ReportError(coded, coded.Actionable)
	return coded
}

// asCoded coerces any error into a *ux.CodedError, preserving one that is already
// coded (so the taxonomy exit code and actionable step survive), mapping the
// SDK's typed auth failures to their taxonomy codes (AGT-7: a token-mint
// failure inside a data command must surface as NOT_AUTHENTICATED /
// PENDING_APPROVAL, not INTERNAL_ERROR), and wrapping everything else as
// INTERNAL_ERROR (exit 1) — the fail-toward-generic rule from 13 §6.
func asCoded(err error) *ux.CodedError {
	var coded *ux.CodedError
	if errors.As(err, &coded) {
		return coded
	}
	if errors.Is(err, auth.ErrNotRegistered) {
		return &ux.CodedError{
			Code:       ux.CodeNotAuthenticated,
			Msg:        err.Error(),
			Actionable: "jentic identity register",
		}
	}
	var pending *auth.PendingError
	if errors.As(err, &pending) {
		return &ux.CodedError{
			Code:       ux.CodePendingApproval,
			Msg:        err.Error(),
			Actionable: "wait for an operator to approve this identity, then retry",
		}
	}
	// AGT-22: an unconfigured machine (no XDG context / no plane URL) is a
	// recoverable RESOLVE_FAILED (exit 2, "change the ask, don't retry the same
	// call"), NOT INTERNAL_ERROR. The client constructors return the typed
	// clictx.ErrNoConfig sentinel for exactly this; give an agent the recovery
	// command instead of the terse "internal error" a bare wrap would produce.
	if errors.Is(err, clictx.ErrNoConfig) {
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        err.Error(),
			Actionable: "jentic register --url <control-plane URL>",
		}
	}
	return &ux.CodedError{Code: ux.CodeInternalError, Msg: err.Error()}
}

// sdkerr: the ARCH-21 adapter that lets the data-plane commands read
// non-2xx responses from the GENERATED control SDK (client/generated/control)
// with the same StatusCode()/Detail()/Fields() surface the hand-written clients'
// httpx.HTTPError exposed. Every generated `*HTTPResp` wrapper has a
// `StatusCode() int` and a `GetBody() []byte`; apiErrorFor turns a wrapper into
// an *HTTPError on a non-2xx (or nil on success), so the per-command mappers
// (apisListErr, apiActionErr, catalogListErr, catalogEntryErr, the TUI 403
// branches, the inline 404s) keep their exact codes/messages/hints and only
// change their errors.As target from `*xclient.HTTPError` to `*HTTPError`.

// httpResponse is the minimal surface every generated `*HTTPResp` wrapper
// satisfies. Both accessors are code-generated on every response type.
type httpResponse interface {
	StatusCode() int
	GetBody() []byte
}

// HTTPError is a non-2xx control-plane response. It mirrors the shape the
// hand-written httpx.HTTPError offered — StatusCode plus the raw problem-details
// body — so existing error-mapping logic ports unchanged. (Named HTTPError, not
// APIError, so it does not stutter as api.APIError — revive `exported`.)
type HTTPError struct {
	StatusCode int
	Body       string
}

func (e *HTTPError) Error() string {
	return fmt.Sprintf("http %d: %s", e.StatusCode, e.Detail())
}

// Detail extracts an RFC 9457 problem-details message, preferring the most
// specific key (matching the old httpx.HTTPError.Detail order exactly).
func (e *HTTPError) Detail() string {
	p := e.Fields()
	for _, k := range []string{"detail", "title", "error_description", "error"} {
		if v, ok := p[k].(string); ok && v != "" {
			return v
		}
	}
	return e.Body
}

// Fields decodes the problem-details body into a map so callers can read
// extension members. Returns an empty map when the body is not a JSON object.
func (e *HTTPError) Fields() map[string]any {
	var p map[string]any
	if json.Unmarshal([]byte(e.Body), &p) == nil {
		return p
	}
	return map[string]any{}
}

// apiErrorFor is the single check every migrated call site makes after a
// `...WithResponse` call. `transportErr` is the error the SDK method returned
// (a real transport/parse failure); if non-nil it is returned verbatim. When
// the request completed, a 2xx status yields nil (success) and any other status
// yields an *HTTPError carrying the raw body for Detail()/Fields(). Callers that
// need a typed success payload read resp.JSON2xx themselves after this returns
// nil.
func apiErrorFor(resp httpResponse, transportErr error) error {
	if transportErr != nil {
		return transportErr
	}
	if resp == nil {
		return &HTTPError{StatusCode: 0, Body: ""}
	}
	code := resp.StatusCode()
	if code >= 200 && code < 300 {
		return nil
	}
	return &HTTPError{StatusCode: code, Body: strings.TrimSpace(string(resp.GetBody()))}
}

// deref returns the pointed-to value, or the zero value when the pointer is nil.
// The generated SDK models optional/nullable members as pointers; call sites
// that project them onto the CLI's flat views use this to collapse a missing
// member to its zero value (the pre-SDK hand-written clients decoded the same
// members as bare values defaulting to "").
func deref[T any](p *T) T {
	if p == nil {
		var zero T
		return zero
	}
	return *p
}

// ptr returns a pointer to v. The generated SDK models optional request-body
// members as pointers; call sites use this to set them from a bare value while
// keeping omitempty semantics (a nil pointer stays off the wire).
func ptr[T any](v T) *T { return &v }

// strEmptyToNil returns nil for an empty string, else a pointer to s. Used for
// optional `*string` request-body members so an unset value stays omitted on
// the wire (omitempty) rather than serializing as "".
func strEmptyToNil(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
