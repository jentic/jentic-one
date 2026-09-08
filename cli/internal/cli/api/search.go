package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

var errSearchQueryRequired = errors.New("a search query is required (positional arg or -q)")

// errSearchUnsupported is returned when the server responds with HTTP 501,
// indicating that search is not enabled on this deployment.
var errSearchUnsupported = errors.New("search is not enabled on this deployment")

type searchOptions struct {
	query  string
	apis   []string
	limit  int
	cursor string
	all    bool
	json   bool
}

func newSearchCmd(app *app) *cobra.Command {
	opts := &searchOptions{}

	cmd := &cobra.Command{
		Use:   "search <query>",
		Short: "Search for operations by natural-language query",
		Long: "search finds API operations whose descriptions, names, or paths match a\n" +
			"query. Results are ranked by lexical (full-text) relevance. The\n" +
			"query can also be passed via -q for piping.\n\n" +
			"Output defaults to JSON when stdout is not a TTY (agent-friendly);\n" +
			"use --json to force JSON on a terminal.",
		Example: "  jentic search \"list users\"\n" +
			"  jentic search -q \"create issue\" --api github-com/api-github-com --limit 5\n" +
			"  jentic search \"list pets\" --all --json | jq '.data[].operation_id'",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) > 0 {
				opts.query = args[0]
			}
			if opts.query == "" {
				return errSearchQueryRequired
			}
			return app.searchE(cmd, opts)
		},
	}

	cmd.Flags().StringVarP(&opts.query, "query", "q", "", "search query (alternative to positional arg)")
	cmd.Flags().StringSliceVar(&opts.apis, "api", nil, "restrict to these APIs (vendor[/name[/version]], as shown in search hits and `jentic apis list`; repeatable)")
	cmd.Flags().IntVar(&opts.limit, "limit", 10, "max results per page (1-100)")
	cmd.Flags().StringVar(&opts.cursor, "cursor", "", "pagination cursor from a previous response")
	cmd.Flags().BoolVar(&opts.all, "all", false, "follow pagination and return all results")
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON output (default when stdout is not a TTY)")

	return cmd
}

func (a *app) searchE(cmd *cobra.Command, opts *searchOptions) error {
	ctx := cmd.Context()
	client, err := a.controlClient(ctx)
	if err != nil {
		return err
	}

	body := control.SearchRequest{Query: opts.query}
	if len(opts.apis) > 0 {
		body.Apis = &opts.apis
	}
	if opts.limit != 0 {
		body.Limit = &opts.limit
	}
	if opts.cursor != "" {
		body.Cursor = &opts.cursor
	}

	const maxPages = 1000

	// Non-nil so an empty result set serializes as `"data": []`, never `null` —
	// clients (and the documented `jq '.data[]'` recipe) can read the envelope
	// unconditionally. This mirrors the server's #671 guarantee on the CLI side.
	allHits := []searchHit{}
	var hasMore bool
	var nextCursor string

	for page := 0; ; page++ {
		resp, searchErr := client.SearchOperationsWithResponse(ctx, body)
		if err := apiErrorFor(resp, searchErr); err != nil {
			// A 501 means search is not enabled on this deployment; map it to the
			// friendly sentinel the command surfaces.
			var ae *HTTPError
			if errors.As(err, &ae) && ae.StatusCode == http.StatusNotImplemented {
				return errSearchUnsupported
			}
			return err
		}
		result := resp.JSON200
		for _, h := range result.Data {
			allHits = append(allHits, toSearchHit(h))
		}
		hasMore = result.HasMore
		nextCursor = deref(result.NextCursor)
		if !opts.all || !result.HasMore || nextCursor == "" {
			break
		}
		if page+1 >= maxPages {
			break
		}
		body.Cursor = &nextCursor
	}

	if cmdcore.JSONOrPretty(cmd, opts.json) {
		return cmdcore.WriteList(a.Out, allHits, nextCursor, nil)
	}

	a.printSearchResults(cmd.Context(), allHits, hasMore)
	return nil
}

// searchHit is the CLI-side projection of a generated OperationResultResponse.
// It keeps the exact JSON shape the search command has always emitted (flat
// strings, `relevance_score`, `_links.inspect`) so the agent-facing envelope and
// the golden fixtures are unchanged, while the wire call goes through the
// generated SDK.
type searchHit struct {
	Type        string      `json:"type"`
	API         searchAPI   `json:"api"`
	OperationID string      `json:"operation_id"`
	Method      string      `json:"method"`
	URL         string      `json:"url"`
	Name        string      `json:"name"`
	Description string      `json:"description"`
	Score       float64     `json:"relevance_score"`
	Links       searchLinks `json:"_links"`
}

// searchAPI is the API identity triple carried by each hit (vendor/name/version
// plus derived host), mirroring the server's ApiReferenceResponse.
type searchAPI struct {
	Vendor  string `json:"vendor"`
	Name    string `json:"name"`
	Version string `json:"version"`
	Host    string `json:"host"`
}

// String renders the API reference as the canonical vendor/name/version slug.
func (a searchAPI) String() string {
	if a.Vendor == "" && a.Name == "" && a.Version == "" {
		return ""
	}
	return a.Vendor + "/" + a.Name + "/" + a.Version
}

type searchLinks struct {
	Inspect string `json:"inspect"`
}

// toSearchHit projects a generated OperationResultResponse onto the flat CLI
// view. Optional (pointer) members collapse to their empty string, preserving
// the pre-migration output where name/description/host were always present.
func toSearchHit(h control.OperationResultResponse) searchHit {
	var typ string
	if h.Type != nil {
		typ = string(*h.Type)
	}
	return searchHit{
		Type:        typ,
		API:         searchAPI{Vendor: h.Api.Vendor, Name: h.Api.Name, Version: h.Api.Version, Host: deref(h.Api.Host)},
		OperationID: h.OperationId,
		Method:      h.Method,
		URL:         h.Url,
		Name:        deref(h.Name),
		Description: deref(h.Description),
		Score:       float32ToFloat64(h.RelevanceScore),
		Links:       searchLinks{Inspect: h.UnderscoreLinks.Inspect},
	}
}

// float32ToFloat64 widens a float32 to float64 via its SHORTEST round-tripping
// decimal, so a score the server sent as 0.8 (decoded by the generated SDK into
// a float32) re-serializes as 0.8 rather than the 0.80000001192… artifact a
// naive float64(f) conversion would surface. This keeps the search JSON
// byte-identical to the pre-SDK output the golden fixtures pin.
func float32ToFloat64(f float32) float64 {
	v, err := strconv.ParseFloat(strconv.FormatFloat(float64(f), 'g', -1, 32), 64)
	if err != nil {
		return float64(f)
	}
	return v
}

func (a *app) printSearchResults(ctx context.Context, hits []searchHit, hasMore bool) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out, st.Heading.Render("Search Results"))
	if len(hits) == 0 {
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("no operations match in the local registry"))
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("nothing imported yet? browse and import the public catalog first:"))
		fmt.Fprintln(a.Out, "    "+st.Command.Render("jentic catalog search <query>")+st.Dim.Render("   # find an importable API"))
		fmt.Fprintln(a.Out, "    "+st.Command.Render("jentic catalog import <vendor/name>")+st.Dim.Render(" # import it, then search again"))
		return
	}
	for _, h := range hits {
		line := st.Accent.Render(fmt.Sprintf("%-6s", h.Method)) + " " +
			st.Command.Render(h.URL)
		if h.Name != "" {
			line += "  " + st.Dim.Render(h.Name)
		}
		fmt.Fprintln(a.Out, "  "+line)
		fmt.Fprintln(a.Out, "    "+st.Dim.Render(fmt.Sprintf("api=%s  score=%.2f", h.API.String(), h.Score)))
		if id := inspectHint(h); id != "" {
			fmt.Fprintln(a.Out, "    "+st.Dim.Render("inspect: jentic inspect "+colonizeInspectID(id)))
		}
	}
	if hasMore {
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("… more results available (use --all or --cursor)"))
	}
}

// inspectHint returns the identifier to show in a hit's copy-pasteable
// `jentic inspect '…'` suggestion. It prefers the resolvable METHOD-URL form
// from `_links.inspect`, but that form is only resolvable when it carries an
// absolute URL; for a server-less operation the server emits a host-relative
// link (e.g. "GET /pets"), which inspect can't resolve. In that case (or when
// the link is absent) it falls back to the registry operation_id, which the
// inspect primary-key path always resolves.
func inspectHint(h searchHit) string {
	if id := inspectIDFromLink(h.Links.Inspect); strings.Contains(id, "://") {
		return id
	}
	return h.OperationID
}

// colonizeInspectID renders an inspect identifier in the canonical, shell-safe
// colon form for display: the METHOD-URL space form the server emits ("GET
// https://…") becomes "GET:https://…", matching what `inspect`/`execute` --help
// and their examples show (and the only form the local execute METHOD:/path
// parser understands directly). An opaque operation_id (no leading METHOD +
// space) is returned unchanged.
func colonizeInspectID(id string) string {
	method, rest, ok := strings.Cut(id, " ")
	if !ok || !strings.Contains(rest, "://") {
		return id
	}
	switch strings.ToUpper(method) {
	case "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS":
		return method + ":" + rest
	default:
		return id
	}
}

// inspectIDFromLink extracts the inspect identifier from a hit's _links.inspect
// URL (e.g. "/inspect?id=GET%20https://api/x" -> "GET https://api/x"), returning
// "" when there is no link. Falls back to the raw (still-encoded) link only if
// it can't be parsed at all.
func inspectIDFromLink(link string) string {
	if link == "" {
		return ""
	}
	u, err := url.Parse(link)
	if err != nil {
		return link
	}
	if id := u.Query().Get("id"); id != "" {
		return id
	}
	return strings.TrimPrefix(link, "/inspect?id=")
}
