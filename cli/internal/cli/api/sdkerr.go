package api

import (
	"encoding/json"
	"fmt"
	"strings"
)

// sdkerr.go is the ARCH-21 adapter that lets the data-plane commands read
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
