package api

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

func newCatalogCmd(app *app) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "catalog",
		Short: "Browse, search, and import APIs from the API catalog",
		Long: "catalog explores the Jentic API catalog (the public catalog plus any other\n" +
			"catalog sources this deployment reads) and imports specs into this\n" +
			"deployment's local registry. Entries are addressed by their entry_id,\n" +
			"`{source}/{id}` (e.g. public/stripe.com): pass the entry_id from\n" +
			"`catalog list`/`catalog search` verbatim. Run bare on a terminal to open an\n" +
			"interactive browser (search, preview operations, import in place); the\n" +
			"subcommands (list/search/show/import/outdated/refresh) are script-friendly.\n" +
			"Requires a registered agent (run `jentic register` first).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.catalogBrowse(cmd.Context(), cmdcore.JSONOrPretty(cmd, false))
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
			o.json = cmdcore.JSONOrPretty(cmd, o.json)
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
			o.json = cmdcore.JSONOrPretty(cmd, o.json)
			return app.catalogList(cmd.Context(), o, query)
		},
	}
	o.bind(cmd)
	return cmd
}

func newCatalogShowCmd(app *app) *cobra.Command {
	o := &catalogShowOptions{}
	cmd := &cobra.Command{
		Use:   "show <entry_id>",
		Short: "Show a catalog entry and preview its operations",
		Long: "show prints one catalog entry and a preview of its operations. Pass the\n" +
			"entry_id from `catalog list`/`catalog search` verbatim (e.g. public/stripe.com).",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			o.json = cmdcore.JSONOrPretty(cmd, o.json)
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
		Use:   "import <entry_id>",
		Short: "Import a catalog entry into the local registry (auto-promotes to live)",
		Long: "import files an import of one catalog entry and, by default, waits for it\n" +
			"and promotes the imported revisions live. Pass the entry_id from\n" +
			"`catalog list`/`catalog search` verbatim (e.g. public/stripe.com).",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			o.json = cmdcore.JSONOrPretty(cmd, o.json)
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
			o.json = cmdcore.JSONOrPretty(cmd, o.json)
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
	var jsonFlag bool
	cmd := &cobra.Command{
		Use:   "refresh",
		Short: "Refresh the catalog manifest from upstream (requires org:admin)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.catalogRefresh(cmd.Context(), cmdcore.JSONOrPretty(cmd, jsonFlag))
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "emit JSON instead of formatted output")
	return cmd
}

// ── option structs ───────────────────────────────────────────────────────────
//
// Each json field is the resolved output decision, not the raw flag: RunE
// folds --json through cmdcore.JSONOrPretty so agent mode (and a piped stdout
// in default mode) emits the JSON envelope without needing --json.

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

// catalogBrowse mirrors apisBrowse: the TUI only runs for a person at a
// terminal; machine output or a non-interactive stdin gets the list instead.
func (a *app) catalogBrowse(ctx context.Context, asJSON bool) error {
	if asJSON || !browserInteractive() {
		return a.catalogList(ctx, &catalogListOptions{limit: 50, json: asJSON}, "")
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
			"catalog_total":                  first.CatalogTotal,
			"registered_count":               first.RegisteredCount,
			"outdated_count":                 first.OutdatedCount,
			"oldest_source_sync_age_seconds": first.OldestSourceSyncAgeSeconds,
		})
	}
	a.printCatalogList(ctx, entries, first)
	return nil
}

// ── show ─────────────────────────────────────────────────────────────────────

func (a *app) catalogShow(ctx context.Context, o *catalogShowOptions, entryID string) error {
	if err := validateEntryID(entryID); err != nil {
		return err
	}
	client, err := a.catalogSession(ctx)
	if err != nil {
		return err
	}
	entry, err := client.Get(ctx, entryID)
	if err != nil {
		return catalogEntryErr(err, entryID, "show")
	}
	limit := o.limit
	if limit <= 0 {
		limit = 200
	}
	preview, perr := client.Preview(ctx, entryID, 0, limit, o.tag)
	if perr != nil {
		perr = catalogEntryErr(perr, entryID, "show")
	}

	if o.json {
		out := map[string]any{"schema_version": apiEnvelopeSchemaVersion, "entry": entry}
		if perr == nil {
			out["preview"] = preview
		}
		return cmdcore.WriteJSON(a.Out, out)
	}

	a.printCatalogEntry(ctx, entry)
	if perr != nil {
		st := theme.StylesFromContext(ctx)
		fmt.Fprintln(a.Out, st.DotWarn()+" "+st.Warnf("operations preview unavailable: %v", perr))
		return nil
	}
	a.printCatalogPreview(ctx, preview)
	return nil
}

// ── import ───────────────────────────────────────────────────────────────────

func (a *app) catalogImport(ctx context.Context, o *catalogImportOptions, entryID string) error {
	if err := validateEntryID(entryID); err != nil {
		return err
	}
	client, err := a.catalogSession(ctx)
	if err != nil {
		return err
	}
	jobID, err := client.Import(ctx, entryID)
	if err != nil {
		return catalogEntryErr(err, entryID, "import")
	}

	if o.noWait {
		if o.json {
			// AGT-23: stamp schema_version on the ad-hoc import envelope like the
			// sanctioned ux wrappers, so an agent can branch on the shape.
			return cmdcore.WriteJSON(a.Out, map[string]any{"schema_version": apiEnvelopeSchemaVersion, "job_id": jobID, "status": "queued"})
		}
		fmt.Fprintln(a.Out, theme.StylesFromContext(ctx).Successf("Import queued: job %s", jobID))
		fmt.Fprintln(a.Out, theme.StylesFromContext(ctx).Dim.Render("Re-run without --no-wait to track it to completion."))
		return nil
	}

	if !o.json {
		fmt.Fprintln(a.Out, theme.StylesFromContext(ctx).Infof("Importing %s …", entryID))
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
		revisions := result.Revisions
		if revisions == nil {
			revisions = []catalogImportRevision{}
		}
		return cmdcore.WriteJSON(a.Out, map[string]any{
			"schema_version": apiEnvelopeSchemaVersion,
			"job_id":         jobID,
			"status":         job.Status,
			"revisions":      revisions,
			"promoted":       promoted,
		})
	}
	a.printImportResult(ctx, result, promoted, o.noPromote)
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
			fmt.Fprintln(progress, theme.StylesFromContext(ctx).Dimf("  still importing (%ds elapsed) …", int(now.Sub(start).Seconds())))
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

func (a *app) catalogRefresh(ctx context.Context, asJSON bool) error {
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
	if asJSON {
		return cmdcore.WriteJSON(a.Out, ux.Result{
			SchemaVersion: apiEnvelopeSchemaVersion,
			Status:        ux.StatusUpdated,
			Resource:      "catalog",
			Message:       fmt.Sprintf("Catalog refreshed: %d entries", count),
			Fields:        map[string]any{"count": count},
		})
	}
	fmt.Fprintln(a.Out, theme.StylesFromContext(ctx).Successf("Catalog refreshed: %d entries", count))
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

// catalogEntryErr maps a 404 to a RESOLVE_FAILED "entry not found", and a
// catalog source's 410 / 503 to the coded errors the MCP import_api tool uses
// (not retryable / retryable after the source's Retry-After). A hidden entry
// and a missing one get the same 404 from the server; when the id isn't in
// entry_id form (an old-style "stripe.com"), the 404 body's did_you_mean names
// the caller's visible entry for it, and the hint re-runs the same subcommand
// with that entry_id. The CLI never guesses a source prefix itself.
func catalogEntryErr(err error, entryID, subcommand string) error {
	var he *HTTPError
	if !errors.As(err, &he) {
		return err
	}
	switch he.StatusCode {
	case http.StatusNotFound:
		suggestion := didYouMean(he)
		coded := &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        fmt.Sprintf("catalog entry %q not found", entryID),
			Actionable: "Find the entry with `jentic catalog search <keyword>` and pass its entry_id verbatim.",
		}
		if suggestion != "" {
			coded.Msg += fmt.Sprintf(" — did you mean %q?", suggestion)
			coded.Actionable = fmt.Sprintf("jentic catalog %s %s", subcommand, shellQuote(suggestion))
			coded.Details = map[string]any{"did_you_mean": suggestion}
		}
		return coded
	case http.StatusGone:
		return &ux.CodedError{
			Code:       ux.CodeResolveFailed,
			Msg:        valueOr(problemDetail([]byte(he.Body)), fmt.Sprintf("The source of catalog entry '%s' no longer has its spec.", entryID)),
			Actionable: "Pick another catalog entry for this API; this one has no importable spec.",
			Cause:      err,
		}
	case http.StatusServiceUnavailable:
		if catalogProblemType(he) == "catalog_source_unavailable" {
			wait := retryAfterSeconds(he.RetryAfter)
			msg := valueOr(problemDetail([]byte(he.Body)), fmt.Sprintf("The catalog source of '%s' is unavailable; retry later.", entryID))
			return &ux.CodedError{
				Code:       ux.CodeTransportError,
				Msg:        fmt.Sprintf("%s Retry in %d seconds.", msg, wait),
				Actionable: fmt.Sprintf("Wait %d seconds, then retry.", wait),
				Details:    map[string]any{"retryable": true, "retry_after_s": wait},
				Cause:      err,
			}
		}
	}
	return err
}

// didYouMean reads the catalog 404 body's did_you_mean (the visible entry_id
// for an id given in the old catalog_api_id form), or "" when absent. A
// suggestion that isn't a well-formed entry_id (checkEntryID) is dropped: it
// is relayed into a runnable hint, so only an id that can name an entry is.
func didYouMean(he *HTTPError) string {
	s, _ := he.Fields()["did_you_mean"].(string)
	s = strings.TrimSpace(s)
	if s == "" || checkEntryID(s) != nil {
		return ""
	}
	return s
}

// shellQuote renders s as one POSIX shell word: bare when it holds only
// characters no shell treats specially, else single-quoted ('\” for a quote).
func shellQuote(s string) string {
	if s != "" && strings.IndexFunc(s, func(r rune) bool {
		return (r < 'a' || r > 'z') && (r < 'A' || r > 'Z') && (r < '0' || r > '9') && !strings.ContainsRune("/._-:@+=,", r)
	}) < 0 {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
