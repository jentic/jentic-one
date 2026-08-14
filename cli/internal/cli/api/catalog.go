package api

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

func newCatalogCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "catalog",
		Short: "Browse, search, and import APIs from the public catalog",
		Long: "catalog explores the Jentic public API catalog and imports specs into\n" +
			"this deployment's local registry. Run bare on a terminal to open an\n" +
			"interactive browser (search, preview operations, import in place); the\n" +
			"subcommands (list/search/show/import/outdated/refresh) are script-friendly.\n" +
			"Requires a registered agent (run `jentic register` first).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.catalogBrowse(cmd.Context())
		},
	}

	cmd.AddCommand(newCatalogListCmd(app))
	cmd.AddCommand(newCatalogSearchCmd(app))
	cmd.AddCommand(newCatalogShowCmd(app))
	cmd.AddCommand(newCatalogImportCmd(app))
	cmd.AddCommand(newCatalogOutdatedCmd(app))
	cmd.AddCommand(newCatalogRefreshCmd(app))
	return cmd
}

// ── command constructors ─────────────────────────────────────────────────────

func newCatalogListCmd(app *app) *cobra.Command {
	o := &catalogListOptions{}
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List catalog entries",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.catalogList(cmd.Context(), o, "")
		},
	}
	o.bind(cmd)
	return cmd
}

func newCatalogSearchCmd(app *app) *cobra.Command {
	o := &catalogListOptions{}
	cmd := &cobra.Command{
		Use:   "search <query>",
		Short: "Search the catalog by keyword",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			query := ""
			if len(args) == 1 {
				query = args[0]
			}
			return app.catalogList(cmd.Context(), o, query)
		},
	}
	o.bind(cmd)
	return cmd
}

func newCatalogShowCmd(app *app) *cobra.Command {
	o := &catalogShowOptions{}
	cmd := &cobra.Command{
		Use:   "show <api_id>",
		Short: "Show a catalog entry and preview its operations",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.catalogShow(cmd.Context(), o, args[0])
		},
	}
	cmd.Flags().StringVar(&o.tag, "tag", "", "only preview operations with this tag")
	cmd.Flags().IntVar(&o.limit, "limit", 0, "max operations to preview (default 200)")
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
	return cmd
}

func newCatalogImportCmd(app *app) *cobra.Command {
	o := &catalogImportOptions{}
	cmd := &cobra.Command{
		Use:   "import <api_id>",
		Short: "Import a catalog entry into the local registry (auto-promotes to live)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.catalogImport(cmd.Context(), o, args[0])
		},
	}
	cmd.Flags().BoolVar(&o.noWait, "no-wait", false, "enqueue the import and return the job id without waiting")
	cmd.Flags().BoolVar(&o.noPromote, "no-promote", false, "leave imported revisions as draft (do not promote to live)")
	cmd.Flags().DurationVar(&o.timeout, "timeout", 2*time.Minute, "how long to wait for the import job")
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
	return cmd
}

func newCatalogOutdatedCmd(app *app) *cobra.Command {
	o := &catalogListOptions{}
	cmd := &cobra.Command{
		Use:   "outdated",
		Short: "List registered entries with an upstream update available",
		Long: "outdated lists locally-registered catalog entries whose upstream spec has\n" +
			"a notified update the local revision hasn't adopted yet. Review the change,\n" +
			"then re-import to adopt it. By default, entries an operator has snoozed/muted\n" +
			"are hidden; pass --include-snoozed to list them too.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			o.outdated = true
			return app.catalogList(cmd.Context(), o, "")
		},
	}
	cmd.Flags().IntVar(&o.limit, "limit", 50, "page size (1-200)")
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
	cmd.Flags().BoolVar(&o.includeSnoozed, "include-snoozed", false,
		"also list entries whose update notification has been snoozed/muted")
	return cmd
}

func newCatalogRefreshCmd(app *app) *cobra.Command {
	return &cobra.Command{
		Use:   "refresh",
		Short: "Refresh the catalog manifest from upstream (requires org:admin)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.catalogRefresh(cmd.Context())
		},
	}
}

// ── option structs ───────────────────────────────────────────────────────────

type catalogListOptions struct {
	registered     bool
	unregistered   bool
	outdated       bool
	limit          int
	all            bool
	json           bool
	includeSnoozed bool
}

func (o *catalogListOptions) bind(cmd *cobra.Command) {
	cmd.Flags().BoolVar(&o.registered, "registered", false, "only entries already imported locally")
	cmd.Flags().BoolVar(&o.unregistered, "unregistered", false, "only entries not yet imported")
	cmd.Flags().IntVar(&o.limit, "limit", 50, "page size (1-200)")
	cmd.Flags().BoolVar(&o.all, "all", false, "follow pagination and list every matching entry")
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
}

type catalogShowOptions struct {
	tag   string
	limit int
	json  bool
}

type catalogImportOptions struct {
	noWait    bool
	noPromote bool
	timeout   time.Duration
	json      bool
}

// ── auth ─────────────────────────────────────────────────────────────────────
//
// catalogSession lives in catalogsvc.go (ARCH-21 A4): it wraps the generated
// control SDK in the CLI-owned catalogClient view.

// ── browse (bare) ────────────────────────────────────────────────────────────

func (a *app) catalogBrowse(ctx context.Context) error {
	if !term.IsTerminal(os.Stdin.Fd()) {
		return a.catalogList(ctx, &catalogListOptions{limit: 50}, "")
	}
	return a.runCatalogBrowser(ctx)
}

// ── list / search ────────────────────────────────────────────────────────────

func (a *app) catalogList(ctx context.Context, o *catalogListOptions, query string) error {
	client, err := a.catalogSession(ctx)
	if err != nil {
		return err
	}
	limit := o.limit
	if limit <= 0 {
		limit = 50
	}
	params := catalogListParams{
		Q:              query,
		Registered:     o.registered,
		Unregistered:   o.unregistered,
		Outdated:       o.outdated,
		IncludeSnoozed: o.includeSnoozed,
		Limit:          limit,
	}

	entries := []catalogEntry{}
	var first *catalogListResult
	var nextCursor string
	for {
		page, err := client.List(ctx, params)
		if err != nil {
			return catalogListErr(err)
		}
		if first == nil {
			first = page
		}
		entries = append(entries, page.Data...)
		nextCursor = ""
		if page.HasMore {
			nextCursor = page.NextCursor
		}
		if !o.all || !page.HasMore || page.NextCursor == "" {
			break
		}
		params.Cursor = page.NextCursor
	}

	if o.json {
		return cmdcore.WriteList(a.Out, entries, nextCursor, map[string]any{
			"catalog_total":        first.CatalogTotal,
			"registered_count":     first.RegisteredCount,
			"outdated_count":       first.OutdatedCount,
			"manifest_age_seconds": first.ManifestAgeSeconds,
		})
	}
	a.printCatalogList(entries, first)
	return nil
}

// ── show ─────────────────────────────────────────────────────────────────────

func (a *app) catalogShow(ctx context.Context, o *catalogShowOptions, apiID string) error {
	client, err := a.catalogSession(ctx)
	if err != nil {
		return err
	}
	entry, err := client.Get(ctx, apiID)
	if err != nil {
		return catalogEntryErr(err, apiID)
	}
	limit := o.limit
	if limit <= 0 {
		limit = 200
	}
	preview, perr := client.Preview(ctx, apiID, 0, limit, o.tag)

	if o.json {
		out := map[string]any{"entry": entry}
		if perr == nil {
			out["preview"] = preview
		}
		return cmdcore.WriteJSON(a.Out, out)
	}

	a.printCatalogEntry(entry)
	if perr != nil {
		fmt.Fprintln(a.Out, cmdcore.DotWarn()+" "+theme.Warnf("operations preview unavailable: %v", perr))
		return nil
	}
	a.printCatalogPreview(preview)
	return nil
}

// ── import ───────────────────────────────────────────────────────────────────

func (a *app) catalogImport(ctx context.Context, o *catalogImportOptions, apiID string) error {
	client, err := a.catalogSession(ctx)
	if err != nil {
		return err
	}
	jobID, err := client.Import(ctx, apiID)
	if err != nil {
		return catalogEntryErr(err, apiID)
	}

	if o.noWait {
		if o.json {
			// AGT-23: stamp schema_version on the ad-hoc import envelope like the
			// sanctioned ux wrappers, so an agent can branch on the shape.
			return cmdcore.WriteJSON(a.Out, map[string]any{"schema_version": apiEnvelopeSchemaVersion, "job_id": jobID, "status": "queued"})
		}
		fmt.Fprintln(a.Out, theme.Successf("Import queued: job %s", jobID))
		fmt.Fprintln(a.Out, theme.Dim.Render("Re-run without --no-wait to track it to completion."))
		return nil
	}

	if !o.json {
		fmt.Fprintln(a.Out, theme.Infof("Importing %s …", apiID))
	}
	job, err := a.pollImportJob(ctx, client, jobID, o.timeout)
	if err != nil {
		return err
	}
	if job.Status != catJobCompleted {
		return fmt.Errorf("import %s: %s", job.Status, cmdcore.ValueOr(job.Error, "no detail"))
	}

	result, err := client.JobResult(ctx, jobID)
	if err != nil {
		return err
	}

	promoted := map[string]string{}
	if !o.noPromote {
		promoted = a.promoteRevisions(ctx, client, result)
	}

	if o.json {
		return cmdcore.WriteJSON(a.Out, map[string]any{
			"schema_version": apiEnvelopeSchemaVersion,
			"job_id":         jobID,
			"status":         job.Status,
			"revisions":      result.Revisions,
			"promoted":       promoted,
		})
	}
	a.printImportResult(result, promoted, o.noPromote)
	return nil
}

// pollImportJob polls the import job until it reaches a terminal state, the
// deadline passes, or the context is cancelled. It emits a periodic heartbeat
// on stderr once the import runs longer than a couple of seconds, so a slow
// import (cold control plane, large spec, slow upstream fetch) reads as
// "still working" rather than a frozen hang — the silent wait is what made a
// slow import look stuck and get killed. Heartbeats go to stderr so they never
// corrupt the JSON stdout the agent parses.
func (a *app) pollImportJob(
	ctx context.Context, client *catalogClient, jobID string, timeout time.Duration,
) (*catalogJob, error) {
	return pollImportJobProgress(ctx, client, jobID, timeout, a.Err)
}

// pollImportJobProgress polls with an optional progress sink. When `progress` is
// non-nil it emits a heartbeat there once the import runs past a couple of
// seconds; pass nil for a silent poll (the TUI browser, which owns the screen).
func pollImportJobProgress(
	ctx context.Context,
	client *catalogClient,
	jobID string,
	timeout time.Duration,
	progress io.Writer,
) (*catalogJob, error) {
	if timeout <= 0 {
		timeout = 2 * time.Minute
	}
	start := time.Now()
	deadline := start.Add(timeout)
	delay := time.Second
	const heartbeatAfter = 2 * time.Second
	nextHeartbeat := start.Add(heartbeatAfter)
	for {
		job, err := client.Job(ctx, jobID)
		if err != nil {
			return nil, err
		}
		switch job.Status {
		case catJobCompleted, catJobFailed, catJobCancelled, catJobDeadLetter:
			return job, nil
		}
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("timed out after %s waiting for import job %s", timeout, jobID)
		}
		if now := time.Now(); progress != nil && now.After(nextHeartbeat) {
			fmt.Fprintln(progress, theme.Dimf("  still importing (%ds elapsed) …", int(now.Sub(start).Seconds())))
			nextHeartbeat = now.Add(3 * time.Second)
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(delay):
		}
		if delay < 5*time.Second {
			delay += time.Second
		}
	}
}

// promoteRevisions promotes each draft revision to live, returning a map of
// revision_id -> outcome ("live" or an error string) for reporting.
func (a *app) promoteRevisions(ctx context.Context, client *catalogClient, result *catalogImportResult) map[string]string {
	out := map[string]string{}
	for _, rev := range result.Revisions {
		if rev.State != "draft" {
			out[rev.RevisionID] = rev.State
			continue
		}
		if err := client.Promote(ctx, rev.API.Vendor, rev.API.Name, rev.API.Version, rev.RevisionID); err != nil {
			out[rev.RevisionID] = "promote failed: " + err.Error()
			continue
		}
		out[rev.RevisionID] = "live"
	}
	return out
}

// ── refresh ──────────────────────────────────────────────────────────────────

func (a *app) catalogRefresh(ctx context.Context) error {
	client, err := a.catalogSession(ctx)
	if err != nil {
		return err
	}
	count, err := client.Refresh(ctx)
	if err != nil {
		var he *HTTPError
		if errors.As(err, &he) && he.StatusCode == http.StatusForbidden {
			return fmt.Errorf("refresh requires org:admin: %s", he.Detail())
		}
		return catalogListErr(err)
	}
	fmt.Fprintln(a.Out, theme.Successf("Catalog refreshed: %d entries", count))
	return nil
}

// ── helpers ──────────────────────────────────────────────────────────────────

// catalogListErr maps a missing route to a friendly "not available" message.
func catalogListErr(err error) error {
	var he *HTTPError
	if errors.As(err, &he) && (he.StatusCode == http.StatusNotFound || he.StatusCode == http.StatusNotImplemented) {
		return fmt.Errorf("catalog not available on this server (HTTP %d)", he.StatusCode)
	}
	return err
}

// catalogEntryErr maps a 404 to a clear "entry not found" message.
func catalogEntryErr(err error, apiID string) error {
	var he *HTTPError
	if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
		return fmt.Errorf("catalog entry %q not found", apiID)
	}
	return err
}
