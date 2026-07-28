package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/catalogclient"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

func newCatalogCmd(app *App) *cobra.Command {
	ident := &identityOptions{}
	cmd := &cobra.Command{
		Use:   "catalog",
		Short: "Browse, search, and import APIs from the public catalog",
		Long: "catalog explores the Jentic public API catalog and imports specs into\n" +
			"this deployment's local registry. Run bare on a terminal to open an\n" +
			"interactive browser (search, preview operations, import in place); the\n" +
			"subcommands (list/search/show/import/refresh) are script-friendly.\n" +
			"Requires a registered agent (run `jentic register` first).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.catalogBrowse(cmd.Context(), ident)
		},
	}
	cmd.PersistentFlags().StringVar(&ident.profile, "profile", "", "profile name (default: config default_profile)")
	cmd.PersistentFlags().StringVar(&ident.baseURL, "base-url", "", "Jentic control-plane base URL")

	cmd.AddCommand(newCatalogListCmd(app, ident))
	cmd.AddCommand(newCatalogSearchCmd(app, ident))
	cmd.AddCommand(newCatalogShowCmd(app, ident))
	cmd.AddCommand(newCatalogImportCmd(app, ident))
	cmd.AddCommand(newCatalogRefreshCmd(app, ident))
	return cmd
}

// ── command constructors ─────────────────────────────────────────────────────

func newCatalogListCmd(app *App, ident *identityOptions) *cobra.Command {
	o := &catalogListOptions{}
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List catalog entries",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.catalogList(cmd.Context(), ident, o, "")
		},
	}
	o.bind(cmd)
	return cmd
}

func newCatalogSearchCmd(app *App, ident *identityOptions) *cobra.Command {
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
			return app.catalogList(cmd.Context(), ident, o, query)
		},
	}
	o.bind(cmd)
	return cmd
}

func newCatalogShowCmd(app *App, ident *identityOptions) *cobra.Command {
	o := &catalogShowOptions{}
	cmd := &cobra.Command{
		Use:   "show <api_id>",
		Short: "Show a catalog entry and preview its operations",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.catalogShow(cmd.Context(), ident, o, args[0])
		},
	}
	cmd.Flags().StringVar(&o.tag, "tag", "", "only preview operations with this tag")
	cmd.Flags().IntVar(&o.limit, "limit", 0, "max operations to preview (default 200)")
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
	return cmd
}

func newCatalogImportCmd(app *App, ident *identityOptions) *cobra.Command {
	o := &catalogImportOptions{}
	cmd := &cobra.Command{
		Use:   "import <api_id>",
		Short: "Import a catalog entry into the local registry (auto-promotes to live)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.catalogImport(cmd.Context(), ident, o, args[0])
		},
	}
	cmd.Flags().BoolVar(&o.noWait, "no-wait", false, "enqueue the import and return the job id without waiting")
	cmd.Flags().BoolVar(&o.noPromote, "no-promote", false, "leave imported revisions as draft (do not promote to live)")
	cmd.Flags().DurationVar(&o.timeout, "timeout", 2*time.Minute, "how long to wait for the import job")
	cmd.Flags().BoolVar(&o.json, "json", false, "emit JSON instead of formatted output")
	return cmd
}

func newCatalogRefreshCmd(app *App, ident *identityOptions) *cobra.Command {
	return &cobra.Command{
		Use:   "refresh",
		Short: "Refresh the catalog manifest from upstream (requires org:admin)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.catalogRefresh(cmd.Context(), ident)
		},
	}
}

// ── option structs ───────────────────────────────────────────────────────────

type catalogListOptions struct {
	registered   bool
	unregistered bool
	limit        int
	all          bool
	json         bool
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

// catalogSession resolves the active profile's agent token and returns a
// catalog client bound to the control-plane base URL.
func (a *App) catalogSession(ctx context.Context, ident *identityOptions) (*catalogclient.Client, string, error) {
	baseURL, token, err := a.agentSession(ctx, ident)
	if err != nil {
		return nil, "", err
	}
	return catalogclient.New(baseURL), token, nil
}

// ── browse (bare) ────────────────────────────────────────────────────────────

func (a *App) catalogBrowse(ctx context.Context, ident *identityOptions) error {
	if !term.IsTerminal(os.Stdin.Fd()) {
		return a.catalogList(ctx, ident, &catalogListOptions{limit: 50}, "")
	}
	return a.runCatalogBrowser(ctx, ident)
}

// ── list / search ────────────────────────────────────────────────────────────

func (a *App) catalogList(ctx context.Context, ident *identityOptions, o *catalogListOptions, query string) error {
	client, token, err := a.catalogSession(ctx, ident)
	if err != nil {
		return err
	}
	limit := o.limit
	if limit <= 0 {
		limit = 50
	}
	params := catalogclient.ListParams{
		Q:            query,
		Registered:   o.registered,
		Unregistered: o.unregistered,
		Limit:        limit,
	}

	var entries []catalogclient.Entry
	var first *catalogclient.ListResult
	for {
		page, err := client.List(ctx, token, params)
		if err != nil {
			return catalogListErr(err)
		}
		if first == nil {
			first = page
		}
		entries = append(entries, page.Data...)
		if !o.all || !page.HasMore || page.NextCursor == "" {
			break
		}
		params.Cursor = page.NextCursor
	}

	if o.json {
		return writeJSON(a.Out, map[string]any{
			"data":                 entries,
			"catalog_total":        first.CatalogTotal,
			"registered_count":     first.RegisteredCount,
			"manifest_age_seconds": first.ManifestAgeSeconds,
		})
	}
	a.printCatalogList(entries, first)
	return nil
}

func (a *App) printCatalogList(entries []catalogclient.Entry, meta *catalogclient.ListResult) {
	fmt.Fprintln(a.Out, theme.Heading.Render("Catalog"))
	if len(entries) == 0 {
		fmt.Fprintln(a.Out, dotDown()+" "+theme.Dim.Render("no matching entries"))
		return
	}
	for _, e := range entries {
		fmt.Fprintln(a.Out, catalogRow(e))
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render(catalogStatusLine(meta)))
}

// catalogRow renders one entry: a filled ring (registered) or hollow ring, the
// accent api_id, and a dim vendor when it differs.
func catalogRow(e catalogclient.Entry) string {
	glyph := theme.Dim.Render(theme.SelectOff)
	if e.Registered {
		glyph = theme.Success.Render(theme.SelectOn)
	}
	row := glyph + " " + theme.Accent.Render(e.APIID)
	if e.Vendor != "" && e.Vendor != e.APIID {
		row += "  " + theme.Dim.Render(e.Vendor)
	}
	return row
}

func catalogStatusLine(m *catalogclient.ListResult) string {
	age := "age unknown"
	if m.ManifestAgeSeconds != nil {
		age = "cache " + humanizeAge(*m.ManifestAgeSeconds)
	}
	return fmt.Sprintf("%d entries · %d imported · %s", m.CatalogTotal, m.RegisteredCount, age)
}

// ── show ─────────────────────────────────────────────────────────────────────

func (a *App) catalogShow(ctx context.Context, ident *identityOptions, o *catalogShowOptions, apiID string) error {
	client, token, err := a.catalogSession(ctx, ident)
	if err != nil {
		return err
	}
	entry, err := client.Get(ctx, token, apiID)
	if err != nil {
		return catalogEntryErr(err, apiID)
	}
	limit := o.limit
	if limit <= 0 {
		limit = 200
	}
	preview, perr := client.Preview(ctx, token, apiID, 0, limit, o.tag)

	if o.json {
		out := map[string]any{"entry": entry}
		if perr == nil {
			out["preview"] = preview
		}
		return writeJSON(a.Out, out)
	}

	a.printCatalogEntry(entry)
	if perr != nil {
		fmt.Fprintln(a.Out, dotWarn()+" "+theme.Warnf("operations preview unavailable: %v", perr))
		return nil
	}
	a.printCatalogPreview(preview)
	return nil
}

func (a *App) printCatalogEntry(e *catalogclient.Entry) {
	fmt.Fprintln(a.Out, theme.Heading.Render(e.APIID))
	if e.Vendor != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("vendor", e.Vendor))
	}
	status := "not imported"
	dot := dotDown()
	if e.Registered {
		status, dot = "imported", dotOK()
	}
	fmt.Fprintln(a.Out, "  "+dot+" "+theme.Field("status", status))
	fmt.Fprintln(a.Out, "  "+theme.Field("spec_url", valueOr(e.SpecURL, "-")))
	if e.Links.Github != "" {
		fmt.Fprintln(a.Out, "  "+theme.Field("github", e.Links.Github))
	}
}

func (a *App) printCatalogPreview(p *catalogclient.Preview) {
	fmt.Fprintln(a.Out)
	title := valueOr(p.Info.Title, "(untitled)")
	if p.Info.Version != "" {
		title += " " + p.Info.Version
	}
	fmt.Fprintln(a.Out, theme.Heading.Render("Operations")+theme.Dim.Render("  "+title))
	if len(p.Data) == 0 {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render("no operations"))
		return
	}
	for _, op := range p.Data {
		fmt.Fprintln(a.Out, "  "+catalogOpLine(op))
	}
	shown := p.Offset + len(p.Data)
	if p.Truncated || shown < p.Total {
		fmt.Fprintln(a.Out, "  "+theme.Dim.Render(fmt.Sprintf("… showing %d of %d operations", len(p.Data), p.Total)))
	}
}

// catalogOpLine renders "METHOD  path  summary" with the method tinted.
func catalogOpLine(op catalogclient.PreviewOp) string {
	method := theme.Accent.Render(fmt.Sprintf("%-6s", op.Method))
	line := method + " " + theme.Command.Render(op.Path)
	if op.Summary != "" {
		line += "  " + theme.Dim.Render(op.Summary)
	}
	return line
}

// ── import ───────────────────────────────────────────────────────────────────

func (a *App) catalogImport(ctx context.Context, ident *identityOptions, o *catalogImportOptions, apiID string) error {
	client, token, err := a.catalogSession(ctx, ident)
	if err != nil {
		return err
	}
	jobID, err := client.Import(ctx, token, apiID)
	if err != nil {
		return catalogEntryErr(err, apiID)
	}

	if o.noWait {
		if o.json {
			return writeJSON(a.Out, map[string]any{"job_id": jobID, "status": "queued"})
		}
		fmt.Fprintln(a.Out, theme.Successf("Import queued: job %s", jobID))
		fmt.Fprintln(a.Out, theme.Dim.Render("Re-run without --no-wait to track it to completion."))
		return nil
	}

	if !o.json {
		fmt.Fprintln(a.Out, theme.Infof("Importing %s …", apiID))
	}
	job, err := a.pollImportJob(ctx, client, token, jobID, o.timeout)
	if err != nil {
		return err
	}
	if job.Status != catalogclient.JobCompleted {
		return fmt.Errorf("import %s: %s", job.Status, valueOr(job.Error, "no detail"))
	}

	result, err := client.JobResult(ctx, token, jobID)
	if err != nil {
		return err
	}

	promoted := map[string]string{}
	if !o.noPromote {
		promoted = a.promoteRevisions(ctx, client, token, result)
	}

	if o.json {
		return writeJSON(a.Out, map[string]any{
			"job_id":    jobID,
			"status":    job.Status,
			"revisions": result.Revisions,
			"promoted":  promoted,
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
func (a *App) pollImportJob(
	ctx context.Context, client *catalogclient.Client, token, jobID string, timeout time.Duration,
) (*catalogclient.Job, error) {
	return pollImportJobProgress(ctx, client, token, jobID, timeout, a.Err)
}

// pollImportJobProgress polls with an optional progress sink. When `progress` is
// non-nil it emits a heartbeat there once the import runs past a couple of
// seconds; pass nil for a silent poll (the TUI browser, which owns the screen).
func pollImportJobProgress(
	ctx context.Context,
	client *catalogclient.Client,
	token, jobID string,
	timeout time.Duration,
	progress io.Writer,
) (*catalogclient.Job, error) {
	if timeout <= 0 {
		timeout = 2 * time.Minute
	}
	start := time.Now()
	deadline := start.Add(timeout)
	delay := time.Second
	const heartbeatAfter = 2 * time.Second
	nextHeartbeat := start.Add(heartbeatAfter)
	for {
		job, err := client.Job(ctx, token, jobID)
		if err != nil {
			return nil, err
		}
		switch job.Status {
		case catalogclient.JobCompleted, catalogclient.JobFailed,
			catalogclient.JobCancelled, catalogclient.JobDeadLetter:
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
func (a *App) promoteRevisions(ctx context.Context, client *catalogclient.Client, token string, result *catalogclient.ImportResult) map[string]string {
	out := map[string]string{}
	for _, rev := range result.Revisions {
		if rev.State != "draft" {
			out[rev.RevisionID] = rev.State
			continue
		}
		if err := client.Promote(ctx, token, rev.API.Vendor, rev.API.Name, rev.API.Version, rev.RevisionID); err != nil {
			out[rev.RevisionID] = "promote failed: " + err.Error()
			continue
		}
		out[rev.RevisionID] = "live"
	}
	return out
}

func (a *App) printImportResult(result *catalogclient.ImportResult, promoted map[string]string, noPromote bool) {
	if len(result.Revisions) == 0 {
		fmt.Fprintln(a.Out, theme.Warnf("Import completed but produced no revisions."))
		return
	}
	fmt.Fprintln(a.Out, theme.Successf("Imported %d revision(s):", len(result.Revisions)))
	for _, rev := range result.Revisions {
		ref := fmt.Sprintf("%s/%s/%s", rev.API.Vendor, rev.API.Name, rev.API.Version)
		state := rev.State
		dot := dotOK()
		if outcome, ok := promoted[rev.RevisionID]; ok {
			switch outcome {
			case "live":
				state = "live"
			case rev.State:
				// unchanged (already non-draft)
			default:
				state = outcome
				dot = dotWarn()
			}
		} else if noPromote {
			state = rev.State + " (not promoted)"
		}
		fmt.Fprintln(a.Out, "  "+dot+" "+theme.Accent.Render(ref)+"  "+theme.Dim.Render(rev.RevisionID)+"  "+theme.Field("state", state))
	}
}

// ── refresh ──────────────────────────────────────────────────────────────────

func (a *App) catalogRefresh(ctx context.Context, ident *identityOptions) error {
	client, token, err := a.catalogSession(ctx, ident)
	if err != nil {
		return err
	}
	count, err := client.Refresh(ctx, token)
	if err != nil {
		var he *catalogclient.HTTPError
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
	var he *catalogclient.HTTPError
	if errors.As(err, &he) && (he.StatusCode == http.StatusNotFound || he.StatusCode == http.StatusNotImplemented) {
		return fmt.Errorf("catalog not available on this server (HTTP %d)", he.StatusCode)
	}
	return err
}

// catalogEntryErr maps a 404 to a clear "entry not found" message.
func catalogEntryErr(err error, apiID string) error {
	var he *catalogclient.HTTPError
	if errors.As(err, &he) && he.StatusCode == http.StatusNotFound {
		return fmt.Errorf("catalog entry %q not found", apiID)
	}
	return err
}

// humanizeAge renders a seconds duration as a compact "Nm"/"Nh"/"Nd old" label.
func humanizeAge(seconds int) string {
	d := time.Duration(seconds) * time.Second
	switch {
	case d < time.Minute:
		return "just now"
	case d < time.Hour:
		return fmt.Sprintf("%dm old", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh old", int(d.Hours()))
	default:
		return fmt.Sprintf("%dd old", int(d.Hours())/24)
	}
}

func writeJSON(w io.Writer, v any) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}
