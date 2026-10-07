package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/jentic/jentic-one/cli/client/generated/control"
)

// catalogsvc.go is the ARCH-21 A4 adapter that keeps the catalog command group
// (catalog.go + the TUI catalog_browser.go) on stable CLI-owned view types while
// the wire calls go through the generated control SDK. A single catalogClient
// wrapper mirrors the old internal/catalogclient method surface
// (List/Get/Preview/Import/Job/JobResult/Promote/Refresh) so the consumers
// change only their type/import names, and the JSON envelope the agent parses is
// byte-identical (the view structs carry the pre-migration json tags).
//
// Two SDK-specific hazards are absorbed here:
//   - Umbrella api_ids (e.g. "googleapis.com/admin") carry a literal "/". The
//     generated request builders url.PathEscape the api_id path param, turning
//     "/" into "%2F", which does NOT match the backend's Starlette {api_id:path}
//     route. rawPathEditor rewrites the request back to a literal-slash path.
//   - GET /jobs/{id}/result has an untyped ({}) body in the spec, so the
//     generated JSON200 is *interface{}; JobResult decodes resp.Body itself.

// Catalog job status values (the generated SDK models Job.Status as a plain
// string, so the CLI keeps its own constants for the terminal-state switch).
const (
	catJobCompleted  = "completed"
	catJobFailed     = "failed"
	catJobCancelled  = "cancelled"
	catJobDeadLetter = "dead_letter"
)

// ── CLI-side view types (stable json shape) ──────────────────────────────────

type catalogEntryLinks struct {
	Self       string `json:"self"`
	Operations string `json:"operations"`
	Import     string `json:"import"`
	Github     string `json:"github"`
}

type catalogEntry struct {
	APIID           string            `json:"api_id"`
	Vendor          string            `json:"vendor"`
	Path            string            `json:"path"`
	SpecURL         string            `json:"spec_url"`
	Registered      bool              `json:"registered"`
	UpdateAvailable bool              `json:"update_available"`
	Links           catalogEntryLinks `json:"_links"`
}

type catalogListResult struct {
	Data               []catalogEntry `json:"data"`
	CatalogTotal       int            `json:"catalog_total"`
	RegisteredCount    int            `json:"registered_count"`
	OutdatedCount      int            `json:"outdated_count"`
	ManifestAgeSeconds *int           `json:"manifest_age_seconds"`
	HasMore            bool           `json:"has_more"`
	NextCursor         string         `json:"next_cursor"`
}

type catalogPreviewParam struct {
	Name        string `json:"name"`
	Location    string `json:"in"`
	Required    bool   `json:"required"`
	Description string `json:"description"`
}

type catalogPreviewOp struct {
	Method      string                `json:"method"`
	Path        string                `json:"path"`
	Summary     string                `json:"summary"`
	Description string                `json:"description"`
	OperationID string                `json:"operation_id"`
	Parameters  []catalogPreviewParam `json:"parameters"`
	Security    []string              `json:"security"`
	Tags        []string              `json:"tags"`
}

type catalogPreviewInfo struct {
	Title       string `json:"title"`
	Version     string `json:"version"`
	Description string `json:"description"`
}

type catalogPreview struct {
	Data            []catalogPreviewOp        `json:"data"`
	Total           int                       `json:"total"`
	Offset          int                       `json:"offset"`
	Truncated       bool                      `json:"truncated"`
	Info            catalogPreviewInfo        `json:"info"`
	SecuritySchemes map[string]map[string]any `json:"security_schemes"`
}

type catalogJob struct {
	JobID  string `json:"job_id"`
	Kind   string `json:"kind"`
	Status string `json:"status"`
	Error  string `json:"error"`
}

// catalogImportResult + its sub-shapes mirror the untyped /jobs/{id}/result body.
type catalogAPIRef struct {
	Vendor  string `json:"vendor"`
	Name    string `json:"name"`
	Version string `json:"version"`
}

type catalogImportRevision struct {
	API        catalogAPIRef `json:"api"`
	RevisionID string        `json:"revision_id"`
	State      string        `json:"state"`
}

type catalogImportResult struct {
	Revisions []catalogImportRevision `json:"revisions"`
}

// catalogListParams holds the query options for the list/search/outdated calls.
type catalogListParams struct {
	Q              string
	Registered     bool
	Unregistered   bool
	Outdated       bool
	IncludeSnoozed bool
	Cursor         string
	Limit          int
}

// ── client wrapper over the generated SDK ────────────────────────────────────

type catalogClient struct {
	sdk *control.ClientWithResponses
}

// catalogSession resolves the generated control client for the active context
// and wraps it (ARCH-21 A4, migrated off internal/catalogclient).
func (a *app) catalogSession(ctx context.Context) (*catalogClient, error) {
	sdk, err := a.controlClient(ctx)
	if err != nil {
		return nil, err
	}
	return &catalogClient{sdk: sdk}, nil
}

// rawPathEditor forces the request path to be emitted with literal slashes,
// undoing the generated builder's url.PathEscape of an umbrella api_id (so
// "googleapis.com/admin" hits the backend's {api_id:path} route). Clearing
// RawPath makes net/http re-derive the escaped path from URL.Path (which holds
// the decoded, literal-slash value), and EscapedPath does not percent-encode
// path separators.
func rawPathEditor(_ context.Context, req *http.Request) error {
	req.URL.RawPath = ""
	return nil
}

func (c *catalogClient) List(ctx context.Context, p catalogListParams) (*catalogListResult, error) {
	params := &control.ListCatalogParams{}
	if p.Q != "" {
		params.Q = ptr(p.Q)
	}
	if p.Registered {
		params.RegisteredOnly = ptr(true)
	}
	if p.Unregistered {
		params.UnregisteredOnly = ptr(true)
	}
	if p.Outdated {
		params.OutdatedOnly = ptr(true)
	}
	if p.IncludeSnoozed {
		params.IncludeSnoozed = ptr(true)
	}
	if p.Cursor != "" {
		params.Cursor = ptr(p.Cursor)
	}
	if p.Limit > 0 {
		params.Limit = ptr(p.Limit)
	}
	resp, err := c.sdk.ListCatalogWithResponse(ctx, params)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	return toCatalogListResult(resp.JSON200), nil
}

func (c *catalogClient) Get(ctx context.Context, apiID string) (*catalogEntry, error) {
	resp, err := c.sdk.GetCatalogEntryWithResponse(ctx, apiID, rawPathEditor)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	e := toCatalogEntry(*resp.JSON200)
	return &e, nil
}

func (c *catalogClient) Preview(ctx context.Context, apiID string, offset, limit int, tag string) (*catalogPreview, error) {
	params := &control.PreviewCatalogOperationsParams{}
	if offset > 0 {
		params.Offset = ptr(offset)
	}
	if limit > 0 {
		params.Limit = ptr(limit)
	}
	if tag != "" {
		params.Tag = ptr(tag)
	}
	resp, err := c.sdk.PreviewCatalogOperationsWithResponse(ctx, apiID, params, rawPathEditor)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	return toCatalogPreview(resp.JSON200), nil
}

func (c *catalogClient) Import(ctx context.Context, apiID string) (string, error) {
	resp, err := c.sdk.ImportCatalogEntryWithResponse(ctx, apiID, rawPathEditor)
	if err := apiErrorFor(resp, err); err != nil {
		return "", err
	}
	if resp.JSON202 == nil {
		return "", fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	return resp.JSON202.JobId, nil
}

func (c *catalogClient) Job(ctx context.Context, jobID string) (*catalogJob, error) {
	resp, err := c.sdk.GetJobWithResponse(ctx, jobID)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	j := resp.JSON200
	return &catalogJob{JobID: j.JobId, Kind: j.Kind, Status: j.Status, Error: deref(j.Error)}, nil
}

func (c *catalogClient) JobResult(ctx context.Context, jobID string) (*catalogImportResult, error) {
	resp, err := c.sdk.GetJobResultWithResponse(ctx, jobID)
	if err := apiErrorFor(resp, err); err != nil {
		return nil, err
	}
	// The spec models this body as untyped ({}), so the generated JSON200 is
	// *interface{}; decode the raw body into the CLI's stable shape ourselves.
	var out catalogImportResult
	if len(resp.Body) > 0 {
		if err := json.Unmarshal(resp.Body, &out); err != nil {
			return nil, fmt.Errorf("decode import job result: %w", err)
		}
	}
	return &out, nil
}

func (c *catalogClient) Promote(ctx context.Context, vendor, name, version, revisionID string) error {
	resp, err := c.sdk.PromoteRevisionWithResponse(ctx, vendor, name, version, revisionID)
	return apiErrorFor(resp, err)
}

func (c *catalogClient) Refresh(ctx context.Context) (int, error) {
	resp, err := c.sdk.RefreshCatalogWithResponse(ctx)
	if err := apiErrorFor(resp, err); err != nil {
		return 0, err
	}
	if resp.JSON200 == nil {
		return 0, fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
	}
	return resp.JSON200.Count, nil
}

// ── projections (generated -> CLI view, preserving json shape) ────────────────

func toCatalogEntry(e control.CatalogEntryResponse) catalogEntry {
	return catalogEntry{
		APIID:           e.ApiId,
		Vendor:          deref(e.Vendor),
		Path:            deref(e.Path),
		SpecURL:         deref(e.SpecUrl),
		Registered:      e.Registered,
		UpdateAvailable: deref(e.UpdateAvailable),
		Links: catalogEntryLinks{
			Self:       e.UnderscoreLinks.Self,
			Operations: e.UnderscoreLinks.Operations,
			Import:     e.UnderscoreLinks.Import,
			Github:     deref(e.UnderscoreLinks.Github),
		},
	}
}

func toCatalogListResult(r *control.CatalogListResponse) *catalogListResult {
	out := &catalogListResult{
		Data:               make([]catalogEntry, 0, len(r.Data)),
		CatalogTotal:       r.CatalogTotal,
		RegisteredCount:    r.RegisteredCount,
		OutdatedCount:      deref(r.OutdatedCount),
		ManifestAgeSeconds: r.ManifestAgeSeconds,
		HasMore:            deref(r.HasMore),
		NextCursor:         deref(r.NextCursor),
	}
	for _, e := range r.Data {
		out.Data = append(out.Data, toCatalogEntry(e))
	}
	return out
}

func toCatalogPreview(p *control.OperationPreviewListResponse) *catalogPreview {
	out := &catalogPreview{
		Data:      make([]catalogPreviewOp, 0, len(p.Data)),
		Total:     p.Total,
		Offset:    p.Offset,
		Truncated: p.Truncated,
		Info: catalogPreviewInfo{
			Title:       deref(p.Info.Title),
			Version:     deref(p.Info.Version),
			Description: deref(p.Info.Description),
		},
		SecuritySchemes: p.SecuritySchemes,
	}
	for _, op := range p.Data {
		params := make([]catalogPreviewParam, 0, len(op.Parameters))
		for _, pp := range op.Parameters {
			params = append(params, catalogPreviewParam{
				Name: pp.Name, Location: pp.In, Required: pp.Required, Description: pp.Description,
			})
		}
		out.Data = append(out.Data, catalogPreviewOp{
			Method:      op.Method,
			Path:        op.Path,
			Summary:     op.Summary,
			Description: op.Description,
			OperationID: deref(op.OperationId),
			Parameters:  params,
			Security:    op.Security,
			Tags:        op.Tags,
		})
	}
	return out
}
