// Package catalogclient is a thin HTTP client for the Jentic control-plane
// catalog (Discover) surface: browse/search entries, preview an entry's
// operations, import an entry into the local registry, poll the resulting job,
// and promote the imported revision. It targets the same control-plane base URL
// as authclient and attaches an agent bearer token to every request.
package catalogclient

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"github.com/jentic/jentic-one/cli/internal/httpx"
)

// Job status values returned by GET /jobs/{id}.
const (
	JobQueued     = "queued"
	JobRunning    = "running"
	JobCompleted  = "completed"
	JobFailed     = "failed"
	JobCancelled  = "cancelled"
	JobDeadLetter = "dead_letter"
)

// Client talks to a single Jentic control-plane base URL.
type Client struct {
	http *httpx.Client
}

// New returns a client for the given base URL (trailing slash trimmed).
func New(baseURL string) *Client {
	return &Client{http: httpx.New(baseURL, 30*time.Second)}
}

// HTTPError is the shared problem-details transport error.
type HTTPError = httpx.HTTPError

// EntryLinks are the hypermedia links on a catalog entry (wire key "_links").
type EntryLinks struct {
	Self       string `json:"self"`
	Operations string `json:"operations"`
	Import     string `json:"import"`
	Github     string `json:"github"`
}

// Entry is a single browsable catalog entry.
type Entry struct {
	APIID      string     `json:"api_id"`
	Vendor     string     `json:"vendor"`
	Path       string     `json:"path"`
	SpecURL    string     `json:"spec_url"`
	Registered bool       `json:"registered"`
	Links      EntryLinks `json:"_links"`
}

// ListResult is a page of catalog entries plus whole-manifest status counters.
type ListResult struct {
	Data               []Entry `json:"data"`
	CatalogTotal       int     `json:"catalog_total"`
	RegisteredCount    int     `json:"registered_count"`
	ManifestAgeSeconds *int    `json:"manifest_age_seconds"`
	HasMore            bool    `json:"has_more"`
	NextCursor         string  `json:"next_cursor"`
}

// PreviewParam is a slimmed operation parameter (wire key "in" for location).
type PreviewParam struct {
	Name        string `json:"name"`
	Location    string `json:"in"`
	Required    bool   `json:"required"`
	Description string `json:"description"`
}

// PreviewOp is a slimmed operation in a catalog spec preview.
type PreviewOp struct {
	Method      string         `json:"method"`
	Path        string         `json:"path"`
	Summary     string         `json:"summary"`
	Description string         `json:"description"`
	OperationID string         `json:"operation_id"`
	Parameters  []PreviewParam `json:"parameters"`
	Security    []string       `json:"security"`
	Tags        []string       `json:"tags"`
}

// PreviewInfo is the spec's info block.
type PreviewInfo struct {
	Title       string `json:"title"`
	Version     string `json:"version"`
	Description string `json:"description"`
}

// Preview is a capped, offset-paginated operation preview for an entry.
type Preview struct {
	Data            []PreviewOp               `json:"data"`
	Total           int                       `json:"total"`
	Offset          int                       `json:"offset"`
	Truncated       bool                      `json:"truncated"`
	Info            PreviewInfo               `json:"info"`
	SecuritySchemes map[string]map[string]any `json:"security_schemes"`
}

// Job is the relevant slice of a GET /jobs/{id} response.
type Job struct {
	JobID  string `json:"job_id"`
	Kind   string `json:"kind"`
	Status string `json:"status"`
	Error  string `json:"error"`
}

// APIRef is the (vendor, name, version) triple of an imported revision.
type APIRef struct {
	Vendor  string `json:"vendor"`
	Name    string `json:"name"`
	Version string `json:"version"`
}

// ImportRevision is a single revision produced by an import job.
type ImportRevision struct {
	API        APIRef `json:"api"`
	RevisionID string `json:"revision_id"`
	State      string `json:"state"`
}

// ImportResult is the result body of a completed import job.
type ImportResult struct {
	Revisions []ImportRevision `json:"revisions"`
}

// ListParams holds the query options for List.
type ListParams struct {
	Q            string
	Registered   bool
	Unregistered bool
	Cursor       string
	Limit        int
}

// List returns a keyset page of catalog entries.
func (c *Client) List(ctx context.Context, token string, p ListParams) (*ListResult, error) {
	q := url.Values{}
	if p.Q != "" {
		q.Set("q", p.Q)
	}
	if p.Registered {
		q.Set("registered_only", "true")
	}
	if p.Unregistered {
		q.Set("unregistered_only", "true")
	}
	if p.Cursor != "" {
		q.Set("cursor", p.Cursor)
	}
	if p.Limit > 0 {
		q.Set("limit", strconv.Itoa(p.Limit))
	}
	path := "/catalog"
	if len(q) > 0 {
		path += "?" + q.Encode()
	}
	var out ListResult
	if err := c.http.Do(ctx, http.MethodGet, path, token, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Get resolves a single catalog entry by api_id.
func (c *Client) Get(ctx context.Context, token, apiID string) (*Entry, error) {
	var out Entry
	if err := c.http.Do(ctx, http.MethodGet, catalogPath(apiID, "", nil), token, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Preview fetches a capped, offset-paginated operation preview for an entry.
func (c *Client) Preview(ctx context.Context, token, apiID string, offset, limit int, tag string) (*Preview, error) {
	q := url.Values{}
	if offset > 0 {
		q.Set("offset", strconv.Itoa(offset))
	}
	if limit > 0 {
		q.Set("limit", strconv.Itoa(limit))
	}
	if tag != "" {
		q.Set("tag", tag)
	}
	var out Preview
	if err := c.http.Do(ctx, http.MethodGet, catalogPath(apiID, "/operations", q), token, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Import enqueues an async import of a catalog entry; it returns the job id.
func (c *Client) Import(ctx context.Context, token, apiID string) (string, error) {
	var out struct {
		JobID string `json:"job_id"`
	}
	if err := c.http.Do(ctx, http.MethodPost, catalogPath(apiID, ":import", nil), token, nil, &out); err != nil {
		return "", err
	}
	return out.JobID, nil
}

// Job fetches the current state of an import job.
func (c *Client) Job(ctx context.Context, token, jobID string) (*Job, error) {
	var out Job
	if err := c.http.Do(ctx, http.MethodGet, "/jobs/"+url.PathEscape(jobID), token, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// JobResult fetches the result body of a completed import job.
func (c *Client) JobResult(ctx context.Context, token, jobID string) (*ImportResult, error) {
	var out ImportResult
	if err := c.http.Do(ctx, http.MethodGet, "/jobs/"+url.PathEscape(jobID)+"/result", token, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Promote publishes a draft revision (archiving the current one).
func (c *Client) Promote(ctx context.Context, token, vendor, name, version, revisionID string) error {
	path := "/apis/" + url.PathEscape(vendor) + "/" + url.PathEscape(name) + "/" +
		url.PathEscape(version) + "/revisions/" + url.PathEscape(revisionID) + ":promote"
	return c.http.Do(ctx, http.MethodPost, path, token, nil, nil)
}

// Refresh forces a manifest refresh (requires org:admin) and returns the count.
func (c *Client) Refresh(ctx context.Context, token string) (int, error) {
	var out struct {
		Count int `json:"count"`
	}
	if err := c.http.Do(ctx, http.MethodPost, "/catalog:refresh", token, nil, &out); err != nil {
		return 0, err
	}
	return out.Count, nil
}

// catalogPath builds a /catalog/{api_id}{suffix} path. The api_id is
// interpolated raw (slashes preserved): the backend route uses Starlette's
// {api_id:path} converter and matches literal "/", so percent-encoding the
// slash (e.g. via url.PathEscape) would NOT match for umbrella vendors like
// "googleapis.com/admin".
func catalogPath(apiID, suffix string, q url.Values) string {
	return "/catalog/" + apiID + suffix + httpx.Query(q)
}
