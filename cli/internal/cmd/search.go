package cmd

import (
	"errors"
	"fmt"
	"net/url"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/searchclient"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

var errSearchQueryRequired = errors.New("a search query is required (positional arg or -q)")

type searchOptions struct {
	query  string
	apis   []string
	limit  int
	cursor string
	all    bool
	json   bool
}

func newSearchCmd(app *App) *cobra.Command {
	ident := &identityOptions{}
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
			return app.searchE(cmd, ident, opts)
		},
	}

	cmd.Flags().StringVarP(&opts.query, "query", "q", "", "search query (alternative to positional arg)")
	cmd.Flags().StringSliceVar(&opts.apis, "api", nil, "restrict to these APIs (vendor[/name[/version]], as shown in search hits and `jentic apis list`; repeatable)")
	cmd.Flags().IntVar(&opts.limit, "limit", 10, "max results per page (1-100)")
	cmd.Flags().StringVar(&opts.cursor, "cursor", "", "pagination cursor from a previous response")
	cmd.Flags().BoolVar(&opts.all, "all", false, "follow pagination and return all results")
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON output (default when stdout is not a TTY)")
	ident.bind(cmd)

	return cmd
}

func (a *App) searchE(cmd *cobra.Command, ident *identityOptions, opts *searchOptions) error {
	baseURL, token, err := a.agentSession(cmd.Context(), ident)
	if err != nil {
		return err
	}

	client := searchclient.New(baseURL)
	req := searchclient.SearchRequest{
		Query:  opts.query,
		APIs:   opts.apis,
		Limit:  opts.limit,
		Cursor: opts.cursor,
	}

	const maxPages = 1000

	// Non-nil so an empty result set serializes as `"data": []`, never `null` —
	// clients (and the documented `jq '.data[]'` recipe) can read the envelope
	// unconditionally. This mirrors the server's #671 guarantee on the CLI side.
	allHits := []searchclient.SearchHit{}
	var hasMore bool
	var nextCursor string

	for page := 0; ; page++ {
		result, searchErr := client.Search(cmd.Context(), token, req)
		if searchErr != nil {
			if errors.Is(searchErr, searchclient.ErrSearchUnsupported) {
				return searchclient.ErrSearchUnsupported
			}
			return searchErr
		}
		allHits = append(allHits, result.Data...)
		hasMore = result.HasMore
		nextCursor = result.NextCursor
		if !opts.all || !result.HasMore || result.NextCursor == "" {
			break
		}
		if page+1 >= maxPages {
			break
		}
		req.Cursor = result.NextCursor
	}

	if jsonOrPretty(cmd, opts.json) {
		return writeJSON(a.Out, map[string]any{
			"data":        allHits,
			"has_more":    hasMore,
			"next_cursor": nextCursor,
		})
	}

	a.printSearchResults(allHits, hasMore)
	return nil
}

func (a *App) printSearchResults(hits []searchclient.SearchHit, hasMore bool) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Search Results"))
	if len(hits) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("no operations match in the local registry"))
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("nothing imported yet? browse and import the public catalog first:"))
		fmt.Fprintln(a.Out, "    "+theme.Command.Render("jentic catalog search <query>")+theme.Dim.Render("   # find an importable API"))
		fmt.Fprintln(a.Out, "    "+theme.Command.Render("jentic catalog import <vendor/name>")+theme.Dim.Render(" # import it, then search again"))
		return
	}
	for _, h := range hits {
		line := theme.Accent.Render(fmt.Sprintf("%-6s", h.Method)) + " " +
			theme.Command.Render(h.URL)
		if h.Name != "" {
			line += "  " + theme.Dim.Render(h.Name)
		}
		fmt.Fprintln(a.Out, "  "+line)
		fmt.Fprintln(a.Out, "    "+theme.Dim.Render(fmt.Sprintf("api=%s  score=%.2f", h.API.String(), h.Score)))
		if id := inspectHint(h); id != "" {
			fmt.Fprintln(a.Out, "    "+theme.Dim.Render("inspect: jentic inspect '"+id+"'"))
		}
	}
	if hasMore {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("… more results available (use --all or --cursor)"))
	}
}

// inspectHint returns the identifier to show in a hit's copy-pasteable
// `jentic inspect '…'` suggestion. It prefers the resolvable METHOD-URL form
// from `_links.inspect`, but that form is only resolvable when it carries an
// absolute URL; for a server-less operation the server emits a host-relative
// link (e.g. "GET /pets"), which inspect can't resolve. In that case (or when
// the link is absent) it falls back to the registry operation_id, which the
// inspect primary-key path always resolves.
func inspectHint(h searchclient.SearchHit) string {
	if id := inspectIDFromLink(h.Links.Inspect); strings.Contains(id, "://") {
		return id
	}
	return h.OperationID
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
