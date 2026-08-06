package cmd

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/client/paginate"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// newHistoryCmd is the `jentic history` root: read-only queries over the control
// plane's execution records (impl/5.0 §2). Not fenced — it mutates nothing.
func newHistoryCmd(app *App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "history",
		Short: "Query and export execution history",
		Long: "history reads the control plane's execution records. `export` walks a\n" +
			"trace's executions (cursor-paginated) and emits a versioned JSON document an\n" +
			"agent can template a workflow from.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error { return cmd.Help() },
	}
	cmd.AddCommand(newHistoryExportCmd(app))
	return cmd
}

type historyExportOptions struct {
	trace           string
	from            string
	to              string
	api             string
	includeFailures bool
	limit           int
	cursor          string
	out             string
}

func newHistoryExportCmd(_ *App) *cobra.Command {
	opts := &historyExportOptions{}
	cmd := &cobra.Command{
		Use:   "export",
		Short: "Export the execution history of a trace",
		Long: "export walks every execution for --trace (following cursor pagination to\n" +
			"completion) and renders a versioned export document. Failed executions\n" +
			"(HTTP >= 400) are excluded unless --include-failures is set. Use --limit/\n" +
			"--cursor to fetch a single page instead of the full walk.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			client, err := clictx.GetControlClient(cmd.Context())
			if err != nil {
				return reportCoded(aud, err)
			}

			from, to, terr := parseTimeWindow(opts.from, opts.to)
			if terr != nil {
				return reportCoded(aud, &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        terr.Error(),
					Actionable: "Use RFC3339 timestamps, e.g. 2026-08-01T00:00:00Z.",
				})
			}

			records, nextCursor, err := collectExecutions(cmd.Context(), client, opts, from, to)
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}

			filtered := filterFailures(records, opts.includeFailures)

			// A single-page fetch (--limit/--cursor) renders a Page so the caller can
			// keep walking; a full walk renders the terminal Export envelope.
			if opts.limit > 0 || opts.cursor != "" {
				page := ux.NewPage(filtered, nextCursor)
				return renderMaybeToFile(aud, opts.out, page)
			}
			return renderMaybeToFile(aud, opts.out, ux.Export{
				TraceID: opts.trace,
				Items:   filtered,
				Count:   len(filtered),
			})
		},
	}
	cmd.Flags().StringVar(&opts.trace, "trace", "", "Trace ID to export (required)")
	cmd.Flags().StringVar(&opts.from, "from", "", "Only executions at/after this RFC3339 time")
	cmd.Flags().StringVar(&opts.to, "to", "", "Only executions at/before this RFC3339 time")
	cmd.Flags().StringVar(&opts.api, "api", "", "Filter by API id")
	cmd.Flags().BoolVar(&opts.includeFailures, "include-failures", false, "Include failed executions (HTTP >= 400)")
	cmd.Flags().IntVar(&opts.limit, "limit", 0, "Fetch a single page of at most N records instead of the full walk")
	cmd.Flags().StringVar(&opts.cursor, "cursor", "", "Fetch the page at this opaque cursor instead of the full walk")
	cmd.Flags().StringVarP(&opts.out, "output", "o", "", "Write the export to this file instead of stdout")
	mustMarkRequired(cmd, "trace")
	return cmd
}

// collectExecutions fetches execution records for the trace. With --limit/--cursor
// it fetches exactly one page (returning the next cursor); otherwise it walks every
// page to completion via paginate.All.
func collectExecutions(ctx context.Context, client *control.ClientWithResponses, opts *historyExportOptions, from, to *time.Time) ([]control.ExecutionResponse, string, error) {
	baseParams := func(cursor *string) *control.ListExecutionsParams {
		p := &control.ListExecutionsParams{TraceId: &opts.trace, Cursor: cursor, From: from, To: to}
		if opts.api != "" {
			p.Api = &opts.api
		}
		if opts.limit > 0 {
			p.Limit = &opts.limit
		}
		return p
	}
	fetch := func(cursor *string) (*control.ExecutionListResponse, error) {
		resp, err := client.ListExecutionsWithResponse(ctx, baseParams(cursor))
		if err != nil {
			return nil, err
		}
		if resp.JSON200 == nil {
			return nil, fmt.Errorf("unexpected backend response (status %d)", resp.StatusCode())
		}
		return resp.JSON200, nil
	}

	// Single page: honor --cursor as the starting point.
	if opts.limit > 0 || opts.cursor != "" {
		var start *string
		if opts.cursor != "" {
			start = &opts.cursor
		}
		body, err := fetch(start)
		if err != nil {
			return nil, "", err
		}
		next := ""
		if body.HasMore && body.NextCursor != nil {
			next = *body.NextCursor
		}
		return body.Data, next, nil
	}

	// Full walk.
	all, err := paginate.All(ctx, func(_ context.Context, cursor string) (paginate.Page[control.ExecutionResponse], error) {
		var c *string
		if cursor != "" {
			c = &cursor
		}
		body, err := fetch(c)
		if err != nil {
			return paginate.Page[control.ExecutionResponse]{}, err
		}
		next := ""
		if body.HasMore && body.NextCursor != nil {
			next = *body.NextCursor
		}
		return paginate.Page[control.ExecutionResponse]{Items: body.Data, Next: next}, nil
	})
	return all, "", err
}

// filterFailures drops executions whose http_status is >= 400 unless include is
// set. A nil status is "not a failure" (the field is optional in the spec).
func filterFailures(records []control.ExecutionResponse, include bool) []control.ExecutionResponse {
	if include {
		return records
	}
	filtered := make([]control.ExecutionResponse, 0, len(records))
	for _, r := range records {
		if r.HttpStatus != nil && *r.HttpStatus >= 400 {
			continue
		}
		filtered = append(filtered, r)
	}
	return filtered
}

// parseTimeWindow parses optional RFC3339 --from/--to bounds.
func parseTimeWindow(from, to string) (*time.Time, *time.Time, error) {
	parse := func(label, s string) (*time.Time, error) {
		if s == "" {
			return nil, nil
		}
		t, err := time.Parse(time.RFC3339, s)
		if err != nil {
			return nil, fmt.Errorf("invalid --%s time %q: %w", label, s, err)
		}
		return &t, nil
	}
	f, err := parse("from", from)
	if err != nil {
		return nil, nil, err
	}
	t, err := parse("to", to)
	if err != nil {
		return nil, nil, err
	}
	return f, t, nil
}

// renderMaybeToFile renders through the Audience (stdout) unless -o was given, in
// which case it writes the redacted JSON document to the file. Writing to a file
// is inherently the machine (agent) shape — a human -o still gets valid JSON.
func renderMaybeToFile(aud ux.Audience, path string, v any) error {
	if path == "" {
		aud.Render(v)
		return nil
	}
	data := ux.MarshalForFile(v)
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return reportCoded(aud, &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  fmt.Sprintf("writing export to %s: %v", path, err),
		})
	}
	return nil
}
