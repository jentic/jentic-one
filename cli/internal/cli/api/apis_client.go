package api

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	sdkclient "github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
)

// apisvc.go is the ARCH-21 A5 adapter that keeps the `apis`/`inspect`/`execute`/
// `endpoints` command group (apis.go, apis_browser.go, inspect.go, execute.go,
// endpoints.go) on stable CLI-owned view types while the wire calls go through
// the generated control SDK. A single apiClient wrapper mirrors the old
// internal/apiclient method surface (List/Get/Revisions/Operations/Promote/
// Archive/DeleteAPI/DeleteRevision/Spec/Inspect/Reference) so the consumers change
// only their type/import names and the JSON envelope the agent parses is
// byte-identical (the view structs carry the pre-migration json tags).
//
// The generated SDK has three gaps this wrapper absorbs:
//   - The revision list + by-revision operations bodies are untyped ({}) in the
//     spec, so Revisions decodes resp.Body into the CLI's own shape.
//   - No generated request builder sets an Accept header, so Spec (YAML) and
//     Inspect (markdown/openapi) use a RequestEditorFn to negotiate content and
//     read the raw resp.Body.
//   - There is no /reference route at all, so Reference issues a raw request
//     through the SDK transport.

// ── CLI-side view types (stable json shape, ported from internal/apiclient) ───

type apiRef struct {
	Vendor  string `json:"vendor"`
	Name    string `json:"name"`
	Version string `json:"version"`
	Host    string `json:"host"`
}

type registeredAPI struct {
	API               apiRef   `json:"api"`
	DisplayName       string   `json:"display_name"`
	Description       string   `json:"description"`
	IconURL           string   `json:"icon_url"`
	CurrentRevisionID string   `json:"current_revision_id"`
	RevisionCount     int      `json:"revision_count"`
	OperationCount    int      `json:"operation_count"`
	SecuritySchemes   []string `json:"security_schemes"`
	CreatedAt         string   `json:"created_at"`
	UpdatedAt         string   `json:"updated_at"`
}

type apiListResult struct {
	Data       []registeredAPI `json:"data"`
	HasMore    bool            `json:"has_more"`
	NextCursor string          `json:"next_cursor"`
}

type revisionSource struct {
	Type        string `json:"type"`
	URL         string `json:"url"`
	Filename    string `json:"filename"`
	SubmittedBy string `json:"submitted_by"`
}

type apiRevision struct {
	RevisionID     string          `json:"revision_id"`
	API            apiRef          `json:"api"`
	Source         *revisionSource `json:"source"`
	SpecDigest     string          `json:"spec_digest"`
	OperationCount int             `json:"operation_count"`
	SubmittedBy    string          `json:"submitted_by"`
	State          string          `json:"state"`
	IsCurrent      bool            `json:"is_current"`
	PromotedAt     string          `json:"promoted_at"`
	ArchivedAt     string          `json:"archived_at"`
	CreatedAt      string          `json:"created_at"`
}

type revisionListResult struct {
	Data       []apiRevision `json:"data"`
	HasMore    bool          `json:"has_more"`
	NextCursor string        `json:"next_cursor"`
}

type apiOperation struct {
	OperationID string   `json:"operation_id"`
	Method      string   `json:"method"`
	Path        string   `json:"path"`
	API         apiRef   `json:"api"`
	RevisionID  string   `json:"revision_id"`
	Name        string   `json:"name"`
	Description string   `json:"description"`
	Tags        []string `json:"tags"`
	Deprecated  bool     `json:"deprecated"`
}

type operationListResult struct {
	Data       []apiOperation `json:"data"`
	HasMore    bool           `json:"has_more"`
	NextCursor string         `json:"next_cursor"`
}

// apiListParams / revisionParams mirror the old apiclient query option structs.
type apiListParams struct {
	Vendor string
	Cursor string
	Limit  int
}

type revisionParams struct {
	States []string
	Cursor string
	Limit  int
}

// ── client wrapper over the generated SDK ────────────────────────────────────

type apiClient struct {
	sdk *control.ClientWithResponses
	raw *control.Client
}

// apisSession resolves the generated control client for the active context and
// wraps it (ARCH-21 A5, migrated off internal/apiclient). The raw client is
// resolved lazily for the content-negotiated (spec/inspect) and route-less
// (reference) calls.
func (a *app) apisSession(ctx context.Context) (*apiClient, error) {
	sdk, err := a.controlClient(ctx)
	if err != nil {
		return nil, err
	}
	return &apiClient{sdk: sdk}, nil
}

func (c *apiClient) rawClient(ctx context.Context) (*control.Client, error) {
	if c.raw != nil {
		return c.raw, nil
	}
	raw, err := clictx.GetControlRawClient(ctx)
	if err != nil {
		return nil, err
	}
	c.raw = raw
	return raw, nil
}

func (c *apiClient) List(ctx context.Context, p apiListParams) (*apiListResult, error) {
	params := &control.ListApisParams{}
	if p.Vendor != "" {
		params.Vendor = ptr(p.Vendor)
	}
	if p.Cursor != "" {
		params.Cursor = ptr(p.Cursor)
	}
	if p.Limit > 0 {
		params.Limit = ptr(p.Limit)
	}
	resp, err := c.sdk.ListApisWithResponse(ctx, params)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	out := &apiListResult{
		Data:       make([]registeredAPI, 0, len(resp.JSON200.Data)),
		HasMore:    resp.JSON200.HasMore,
		NextCursor: deref(resp.JSON200.NextCursor),
	}
	for _, api := range resp.JSON200.Data {
		out.Data = append(out.Data, toRegisteredAPI(api))
	}
	return out, nil
}

func (c *apiClient) Get(ctx context.Context, vendor, name, version string) (*registeredAPI, error) {
	resp, err := c.sdk.GetApiWithResponse(ctx, vendor, name, version)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	api := toRegisteredAPI(*resp.JSON200)
	return &api, nil
}

func (c *apiClient) Revisions(ctx context.Context, vendor, name, version string, p revisionParams) (*revisionListResult, error) {
	params := &control.ListApiRevisionsParams{}
	if states := nonEmpty(p.States); len(states) > 0 {
		params.State = &states
	}
	if p.Cursor != "" {
		params.Cursor = ptr(p.Cursor)
	}
	if p.Limit > 0 {
		params.Limit = ptr(p.Limit)
	}
	resp, err := c.sdk.ListApiRevisionsWithResponse(ctx, vendor, name, version, params)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	// The revision list body is untyped ({}) in the spec, so decode the raw
	// body into the CLI's stable shape ourselves.
	var out revisionListResult
	if len(resp.Body) > 0 {
		if err := json.Unmarshal(resp.Body, &out); err != nil {
			return nil, fmt.Errorf("decode revisions: %w", err)
		}
	}
	return &out, nil
}

func (c *apiClient) Operations(ctx context.Context, vendor, name, version, revisionID, cursor string, limit int) (*operationListResult, error) {
	// The by-revision operations body is untyped ({}); the current-revision one
	// is typed. Decode the raw body for the revision case, project the typed
	// body for the current case — both land on the same CLI view shape.
	if revisionID != "" {
		params := &control.ListApiRevisionOperationsParams{}
		if cursor != "" {
			params.Cursor = ptr(cursor)
		}
		if limit > 0 {
			params.Limit = ptr(limit)
		}
		resp, err := c.sdk.ListApiRevisionOperationsWithResponse(ctx, vendor, name, version, revisionID, params)
		if err := apiErrorFor(resp, err); err != nil {
			return nil, err
		}
		var out operationListResult
		if len(resp.Body) > 0 {
			if err := json.Unmarshal(resp.Body, &out); err != nil {
				return nil, fmt.Errorf("decode operations: %w", err)
			}
		}
		return &out, nil
	}

	params := &control.ListApiOperationsParams{}
	if cursor != "" {
		params.Cursor = ptr(cursor)
	}
	if limit > 0 {
		params.Limit = ptr(limit)
	}
	resp, err := c.sdk.ListApiOperationsWithResponse(ctx, vendor, name, version, params)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	out := &operationListResult{
		Data:       make([]apiOperation, 0, len(resp.JSON200.Data)),
		HasMore:    resp.JSON200.HasMore,
		NextCursor: deref(resp.JSON200.NextCursor),
	}
	for _, op := range resp.JSON200.Data {
		out.Data = append(out.Data, toAPIOperation(op))
	}
	return out, nil
}

func (c *apiClient) Promote(ctx context.Context, vendor, name, version, revisionID string) error {
	resp, err := c.sdk.PromoteRevisionWithResponse(ctx, vendor, name, version, revisionID)
	return apiErrorFor(resp, err)
}

func (c *apiClient) Archive(ctx context.Context, vendor, name, version, revisionID string) error {
	resp, err := c.sdk.ArchiveRevisionWithResponse(ctx, vendor, name, version, revisionID)
	return apiErrorFor(resp, err)
}

func (c *apiClient) DeleteAPI(ctx context.Context, vendor, name, version string) error {
	resp, err := c.sdk.DeleteApiWithResponse(ctx, vendor, name, version)
	return apiErrorFor(resp, err)
}

func (c *apiClient) DeleteRevision(ctx context.Context, vendor, name, version, revisionID string) error {
	resp, err := c.sdk.DeleteRevisionWithResponse(ctx, vendor, name, version, revisionID)
	return apiErrorFor(resp, err)
}

// Spec downloads the OpenAPI document for the current (or a named) revision. The
// generated builder sets no Accept header and only decodes JSON, so a
// RequestEditorFn negotiates the format and the raw resp.Body is returned.
func (c *apiClient) Spec(ctx context.Context, vendor, name, version, revisionID string, yaml bool) ([]byte, error) {
	accept := "application/json"
	if yaml {
		accept = "application/yaml"
	}
	editor := acceptEditor(accept)
	if revisionID != "" {
		resp, err := c.sdk.GetApiRevisionSpecWithResponse(ctx, vendor, name, version, revisionID, &control.GetApiRevisionSpecParams{}, editor)
		if err := apiErrorFor(resp, err); err != nil {
			return nil, err
		}
		return resp.Body, nil
	}
	resp, err := c.sdk.GetApiSpecWithResponse(ctx, vendor, name, version, &control.GetApiSpecParams{}, editor)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	return resp.Body, nil
}

// Inspect resolves an operation to structural detail. format is one of "json",
// "markdown", or "openapi"; the negotiated raw body is returned. The
// operationID is sent as `id=METHOD URL` when it parses as a METHOD/URL pair
// (the form `jentic search` prints), else as `operation_id=`.
func (c *apiClient) Inspect(ctx context.Context, operationID, revisionID, format string) ([]byte, error) {
	params := &control.InspectOperationParams{}
	if method, target, ok := parseMethodURL(operationID); ok {
		params.Id = ptr(method + " " + target)
	} else {
		params.OperationId = ptr(operationID)
	}
	if revisionID != "" {
		params.RevisionId = ptr(revisionID)
	}
	resp, err := c.sdk.InspectOperationWithResponse(ctx, params, acceptEditor(inspectAccept(format)))
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	return resp.Body, nil
}

// Reference fetches the public endpoint + scope reference. There is no generated
// route for /reference/endpoints.json, so it is issued raw through the SDK
// transport (which carries the auth/session editors; the endpoint is public so a
// bearer is harmless).
func (c *apiClient) Reference(ctx context.Context) ([]byte, error) {
	raw, err := c.rawClient(ctx)
	if err != nil {
		return nil, err
	}
	resp, err := sdkclient.RawControlRequest(ctx, raw, http.MethodGet, "/reference/endpoints.json", nil, "Accept=application/json")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read reference: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, &HTTPError{StatusCode: resp.StatusCode, Body: string(body)}
	}
	return body, nil
}

// ── helpers ──────────────────────────────────────────────────────────────────

// acceptEditor returns a RequestEditorFn that pins the Accept header, so the
// generated method (which sets none) negotiates the content type the CLI wants.
func acceptEditor(accept string) control.RequestEditorFn {
	return func(_ context.Context, req *http.Request) error {
		req.Header.Set("Accept", accept)
		return nil
	}
}

// nonEmpty drops empty strings from a slice (the states filter allows blanks).
func nonEmpty(in []string) []string {
	out := make([]string, 0, len(in))
	for _, s := range in {
		if s != "" {
			out = append(out, s)
		}
	}
	return out
}

func toAPIRef(r control.ApiReferenceResponse) apiRef {
	return apiRef{Vendor: r.Vendor, Name: r.Name, Version: r.Version, Host: deref(r.Host)}
}

func toRegisteredAPI(a control.ApiResponse) registeredAPI {
	return registeredAPI{
		API:               toAPIRef(a.Api),
		DisplayName:       deref(a.DisplayName),
		Description:       deref(a.Description),
		IconURL:           deref(a.IconUrl),
		CurrentRevisionID: deref(a.CurrentRevisionId),
		RevisionCount:     a.RevisionCount,
		OperationCount:    a.OperationCount,
		SecuritySchemes:   a.SecuritySchemes,
		CreatedAt:         formatTime(a.CreatedAt),
		UpdatedAt:         formatTime(a.UpdatedAt),
	}
}

// formatTime renders a timestamp as RFC 3339, but a ZERO time as "" — the
// pre-migration apiclient decoded these as plain strings that were empty when
// the backend omitted them, and the JSON output contract (golden-pinned) keeps
// that empty-when-absent shape rather than the zero-time sentinel.
func formatTime(t time.Time) string {
	if t.IsZero() {
		return ""
	}
	return t.Format(time.RFC3339)
}

func toAPIOperation(op control.OperationSummaryResponse) apiOperation {
	return apiOperation{
		OperationID: op.OperationId,
		Method:      op.Method,
		Path:        op.Path,
		API:         toAPIRef(op.Api),
		RevisionID:  op.RevisionId,
		Name:        deref(op.Name),
		Description: deref(op.Description),
		Tags:        deref(op.Tags),
		Deprecated:  deref(op.Deprecated),
	}
}

// parseMethodURL detects the "METHOD URL" identifier form (as printed by
// `jentic search`) and splits it into its parts. Both "GET https://host/p" and
// "GET:https://host/p" are accepted. It returns ok=false for an opaque
// operation ID. The server's /inspect endpoint resolves "METHOD URL" via the
// id= query param and opaque IDs via operation_id=. (Ported from the retired
// internal/apiclient; execute.go's parseMethodPath is a distinct broker form.)
func parseMethodURL(s string) (method, target string, ok bool) {
	s = strings.TrimSpace(s)
	var first, rest string
	if sp, r, found := strings.Cut(s, " "); found {
		first, rest = sp, r
	} else if c, r, found := strings.Cut(s, ":"); found {
		first, rest = c, r
	} else {
		return "", "", false
	}
	rest = strings.TrimSpace(rest)
	if !strings.HasPrefix(rest, "http://") && !strings.HasPrefix(rest, "https://") {
		return "", "", false
	}
	switch strings.ToUpper(first) {
	case "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS":
		return strings.ToUpper(first), rest, true
	default:
		return "", "", false
	}
}

// inspectAccept maps an inspect --format to the Accept header the server
// negotiates on (ported from the retired internal/apiclient).
func inspectAccept(format string) string {
	switch format {
	case "markdown", "md":
		return "text/markdown"
	case "openapi", "yaml":
		return "application/openapi+yaml"
	default:
		return "application/json"
	}
}
