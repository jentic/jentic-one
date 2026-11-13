package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/client/paginate"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newCredentialsCmd is `jentic credentials`: read-only listing/inspection of the
// identity's credentials. The server already returns CredentialRedactedResponse
// (no live secret); the CLI redaction funnel is belt-and-braces on top. Not
// fenced — read-only, no local config mutation (impl/5.0 §6b, jentic-one#742).
func newCredentialsCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:     "credentials",
		Aliases: []string{"creds"},
		Short:   "List and inspect credentials (secrets are server-masked)",
		Args:    cobra.NoArgs,
		RunE:    func(cmd *cobra.Command, _ []string) error { return cmd.Help() },
	}
	cmd.AddCommand(newCredentialsListCmd(app), newCredentialsShowCmd(app))
	return cmd
}

func newCredentialsListCmd(_ *app) *cobra.Command {
	var vendor, cursor string
	var limit int
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List credentials",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}

			// A single-page fetch when --limit/--cursor is given; otherwise walk all.
			if limit > 0 || cursor != "" {
				items, next, ferr := fetchCredentialPage(cmd.Context(), client, vendor, cursor, limit)
				if ferr != nil {
					return reportCoded(aud, asCoded(ferr))
				}
				aud.Render(ux.NewPage(items, next))
				return nil
			}

			all, aerr := paginate.All(cmd.Context(), func(ctx context.Context, cur string) (paginate.Page[control.CredentialRedactedResponse], error) {
				items, next, ferr := fetchCredentialPage(ctx, client, vendor, cur, 0)
				return paginate.Page[control.CredentialRedactedResponse]{Items: items, Next: next}, ferr
			})
			if aerr != nil {
				return reportCoded(aud, asCoded(aerr))
			}
			aud.Render(ux.NewPage(all, ""))
			return nil
		},
	}
	cmd.Flags().StringVar(&vendor, "vendor", "", "Filter by vendor")
	cmd.Flags().IntVar(&limit, "limit", 0, "Fetch a single page of at most N credentials")
	cmd.Flags().StringVar(&cursor, "cursor", "", "Fetch the page at this opaque cursor")
	return cmd
}

func fetchCredentialPage(ctx context.Context, client *control.ClientWithResponses, vendor, cursor string, limit int) ([]control.CredentialRedactedResponse, string, error) {
	params := &control.ListCredentialsParams{}
	if vendor != "" {
		params.Vendor = &vendor
	}
	if cursor != "" {
		params.Cursor = &cursor
	}
	if limit > 0 {
		params.Limit = &limit
	}
	page, err := decodeCredentialRead[control.CredentialListResponse]("", func() (*http.Response, error) {
		return client.ListCredentials(ctx, params)
	})
	if err != nil {
		return nil, "", err
	}
	next := ""
	if page.HasMore && page.NextCursor != nil {
		next = *page.NextCursor
	}
	return page.Data, next, nil
}

func newCredentialsShowCmd(_ *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "show <credential_id>",
		Short: "Show a credential (secret-masked)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}
			cred, err := fetchCredential(cmd.Context(), client, args[0])
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}
			aud.Render(cred)
			return nil
		},
	}
	return cmd
}

func fetchCredential(ctx context.Context, client *control.ClientWithResponses, credentialID string) (*control.CredentialRedactedResponse, error) {
	return decodeCredentialRead[control.CredentialRedactedResponse](credentialID, func() (*http.Response, error) {
		return client.GetCredential(ctx, credentialID)
	})
}

// rawCredentialResp satisfies httpResponse over raw response bytes so
// apiErrorFor inspects the HTTP status before any ProblemDetail unmarshaling
// (FastAPI validation 422s emit an array `detail` that the generated
// ProblemDetail string field rejects).
type rawCredentialResp struct {
	statusCode int
	body       []byte
}

func (r *rawCredentialResp) StatusCode() int { return r.statusCode }
func (r *rawCredentialResp) GetBody() []byte { return r.body }

func decodeCredentialRead[T any](credentialID string, do func() (*http.Response, error)) (*T, error) {
	raw, rerr := doCredentialRead(do)
	if err := apiErrorFor(raw, rerr); err != nil {
		return nil, credentialReadErr(err, credentialID)
	}
	if raw.StatusCode() != http.StatusOK {
		return nil, credentialReadErr(&HTTPError{
			StatusCode: raw.StatusCode(),
			Body:       strings.TrimSpace(string(raw.GetBody())),
		}, credentialID)
	}
	var payload T
	if err := json.Unmarshal(raw.GetBody(), &payload); err != nil {
		return nil, fmt.Errorf("decode credential response: %w", err)
	}
	return &payload, nil
}

func doCredentialRead(do func() (*http.Response, error)) (*rawCredentialResp, error) {
	rsp, err := do()
	if err != nil {
		return nil, err
	}
	if rsp == nil {
		return nil, nil
	}
	defer func() { _ = rsp.Body.Close() }()
	body, err := io.ReadAll(rsp.Body)
	if err != nil {
		return nil, fmt.Errorf("read credential response: %w", err)
	}
	return &rawCredentialResp{statusCode: rsp.StatusCode, body: body}, nil
}

// credentialReadErr maps control-plane HTTP failures on `creds list`
// (credentialID == "") and `creds show` (credentialID != "") onto the CLI's
// coded taxonomy (#1429). Transport and non-HTTP errors pass through verbatim
// for asCoded.
func credentialReadErr(err error, credentialID string) error {
	var he *HTTPError
	if !errors.As(err, &he) || he == nil {
		return err
	}
	detail := credentialProblemDetail(he)

	switch he.StatusCode {
	case http.StatusUnauthorized, http.StatusForbidden:
		msg := fmt.Sprintf("the control plane rejected this identity's credentials (http %d)", he.StatusCode)
		if detail != "" {
			msg = fmt.Sprintf("the control plane rejected this identity's credentials (http %d: %s)", he.StatusCode, detail)
		}
		return &ux.CodedError{
			Code: ux.CodeNotAuthenticated,
			Msg:  msg,
			Actionable: "Run `jentic doctor` to check your setup or `jentic register` to re-authenticate; " +
				"an agent can inspect its own bindings with `jentic whoami`.",
			Details: map[string]any{"http_status": he.StatusCode},
			Cause:   he,
		}

	case http.StatusNotFound:
		if credentialID != "" {
			msg := fmt.Sprintf("credential %q not found (status %d)", credentialID, he.StatusCode)
			if detail != "" && !strings.EqualFold(detail, http.StatusText(http.StatusNotFound)) {
				msg = fmt.Sprintf("credential %q not found (status %d): %s", credentialID, he.StatusCode, detail)
			}
			return &ux.CodedError{
				Code:       ux.CodeResolveFailed,
				Msg:        msg,
				Actionable: "Run `jentic credentials list` or `jentic whoami` to inspect available credential IDs.",
				Details:    map[string]any{"http_status": he.StatusCode},
				Cause:      he,
			}
		}
		msg := fmt.Sprintf("credentials endpoint not found (status %d)", he.StatusCode)
		if detail != "" && !strings.EqualFold(detail, http.StatusText(http.StatusNotFound)) {
			msg = fmt.Sprintf("credentials endpoint not found (status %d): %s", he.StatusCode, detail)
		}
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        msg,
			Actionable: "Verify the control-plane URL with `jentic doctor`.",
			Details:    map[string]any{"http_status": he.StatusCode},
			Cause:      he,
		}

	case http.StatusBadRequest, http.StatusUnprocessableEntity:
		if credentialID != "" {
			msg := fmt.Sprintf("invalid credential request for %q (status %d)", credentialID, he.StatusCode)
			if detail != "" {
				msg = fmt.Sprintf("invalid credential request for %q (status %d): %s", credentialID, he.StatusCode, detail)
			}
			return &ux.CodedError{
				Code:       ux.CodeMissingArgument,
				Msg:        msg,
				Actionable: "Pass a valid credential ID from `jentic credentials list` or `jentic whoami`.",
				Details:    map[string]any{"http_status": he.StatusCode},
				Cause:      he,
			}
		}
		msg := fmt.Sprintf("invalid credentials list request (status %d)", he.StatusCode)
		if detail != "" {
			msg = fmt.Sprintf("invalid credentials list request (status %d): %s", he.StatusCode, detail)
		}
		return &ux.CodedError{
			Code:       ux.CodeMissingArgument,
			Msg:        msg,
			Actionable: "Check --limit (1-200), --cursor (use next_token from a previous page), and --vendor, then retry.",
			Details:    map[string]any{"http_status": he.StatusCode},
			Cause:      he,
		}

	default:
		if detail != "" && detail != he.Detail() {
			if he.Body == "" {
				return &HTTPError{StatusCode: he.StatusCode, Body: detail}
			}
			return &ux.CodedError{
				Code:    ux.CodeInternalError,
				Msg:     fmt.Sprintf("http %d: %s", he.StatusCode, detail),
				Details: map[string]any{"http_status": he.StatusCode},
				Cause:   he,
			}
		}
		return err
	}
}

type credentialProblem struct {
	Detail           json.RawMessage `json:"detail"`
	Title            string          `json:"title"`
	ErrorDescription string          `json:"error_description"`
	Error            string          `json:"error"`
	Errors           json.RawMessage `json:"errors"`
}

// credentialProblemDetail extracts a human-readable problem summary from he,
// supporting RFC 9457 string `detail` + `errors` items as well as FastAPI's
// array-form validation `detail` (`[{"loc": ..., "msg": ...}]`).
func credentialProblemDetail(he *HTTPError) string {
	if he == nil {
		return ""
	}
	var p credentialProblem
	if json.Unmarshal([]byte(he.Body), &p) == nil {
		detail := credentialDetailText(p.Detail)
		if detail == "" {
			for _, candidate := range []string{p.Title, p.ErrorDescription, p.Error} {
				if trimmed := strings.TrimSpace(candidate); trimmed != "" {
					detail = trimmed
					break
				}
			}
		}
		if fieldErrs := credentialErrorItemsText(p.Errors); fieldErrs != "" {
			switch {
			case detail == "":
				detail = fieldErrs
			case !strings.Contains(detail, fieldErrs):
				detail += " (" + fieldErrs + ")"
			}
		}
		if detail != "" {
			return detail
		}
	}
	if trimmed := strings.TrimSpace(he.Body); trimmed != "" && trimmed != "{}" && trimmed != "null" {
		return trimmed
	}
	return http.StatusText(he.StatusCode)
}

func credentialDetailText(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var text string
	if json.Unmarshal(raw, &text) == nil {
		return strings.TrimSpace(text)
	}
	if items := credentialErrorItemsText(raw); items != "" {
		return items
	}
	trimmed := strings.TrimSpace(string(raw))
	if trimmed == "null" || trimmed == "[]" || trimmed == "{}" {
		return ""
	}
	return trimmed
}

func credentialErrorItemsText(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var items []struct {
		Loc       []any  `json:"loc"`
		Msg       string `json:"msg"`
		Detail    string `json:"detail"`
		Parameter string `json:"parameter"`
		Pointer   string `json:"pointer"`
	}
	if json.Unmarshal(raw, &items) != nil || len(items) == 0 {
		return ""
	}
	parts := make([]string, 0, len(items))
	for _, item := range items {
		msg := strings.TrimSpace(item.Msg)
		if msg == "" {
			msg = strings.TrimSpace(item.Detail)
		}
		if msg == "" {
			continue
		}
		where := joinLoc(item.Loc)
		if where == "" {
			where = strings.TrimSpace(item.Parameter)
		}
		if where == "" {
			where = strings.TrimSpace(item.Pointer)
		}
		if where != "" {
			parts = append(parts, where+": "+msg)
			continue
		}
		parts = append(parts, msg)
	}
	return strings.Join(parts, "; ")
}
