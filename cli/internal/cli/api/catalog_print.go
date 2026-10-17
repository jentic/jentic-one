package api

import (
	"context"
	"fmt"
	"time"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

func (a *app) printCatalogList(ctx context.Context, entries []catalogEntry, meta *catalogListResult) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out, st.Heading.Render("Catalog"))
	if len(entries) == 0 {
		fmt.Fprintln(a.Out, st.DotDown()+" "+st.Dim.Render("no matching entries"))
		return
	}
	for _, e := range entries {
		fmt.Fprintln(a.Out, catalogRow(st, e))
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, st.Dim.Render(catalogStatusLine(meta)))
}

// catalogRow renders one entry: a filled ring (registered) or hollow ring, the
// accent entry_id (the id every catalog command takes), the dim title when the
// source gives one, an "N other sources" note when other sources list the same
// API, and an "UPDATE AVAILABLE" marker when the entry's upstream spec has
// changed since import.
func catalogRow(st theme.Styles, e catalogEntry) string {
	glyph := st.Dim.Render(theme.SelectOff)
	if e.Registered {
		glyph = st.Success.Render(theme.SelectOn)
	}
	row := glyph + " " + st.Accent.Render(e.EntryID)
	if title := deref(e.Title); title != "" {
		row += "  " + st.Dim.Render(title)
	}
	if e.Alternates > 0 {
		row += "  " + st.Dim.Render(otherSources(e.Alternates))
	}
	if e.UpdateAvailable {
		row += "  " + st.Warn.Render("UPDATE AVAILABLE")
	}
	return row
}

// otherSources is the "N other source(s)" note for an entry that collapses
// alternates from other catalog sources.
func otherSources(n int) string {
	if n == 1 {
		return "+1 other source"
	}
	return fmt.Sprintf("+%d other sources", n)
}

func catalogStatusLine(m *catalogListResult) string {
	age := "sources never synced"
	if m.OldestSourceSyncAgeSeconds != nil {
		age = "oldest source sync " + humanizeAge(*m.OldestSourceSyncAgeSeconds)
	}
	line := fmt.Sprintf("%d entries · %d imported · %s", m.CatalogTotal, m.RegisteredCount, age)
	if m.OutdatedCount > 0 {
		line += fmt.Sprintf(" · %d update(s) available", m.OutdatedCount)
	}
	return line
}

func (a *app) printCatalogEntry(ctx context.Context, e *catalogEntry) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out, st.Heading.Render(e.EntryID))
	if title := deref(e.Title); title != "" {
		fmt.Fprintln(a.Out, "  "+st.Field("title", title))
	}
	fmt.Fprintln(a.Out, "  "+st.Field("source", e.SourceSlug))
	fmt.Fprintln(a.Out, "  "+st.Field("catalog_api_id", e.CatalogAPIID))
	if vendor := deref(e.DisplayVendor); vendor != "" {
		fmt.Fprintln(a.Out, "  "+st.Field("vendor", vendor))
	}
	if version := deref(e.Version); version != "" {
		fmt.Fprintln(a.Out, "  "+st.Field("version", version))
	}
	status := "not imported"
	dot := st.DotDown()
	if e.Registered {
		status, dot = "imported", st.DotOK()
	}
	fmt.Fprintln(a.Out, "  "+dot+" "+st.Field("status", status))
	if !e.HasSpec {
		fmt.Fprintln(a.Out, "  "+st.Field("spec", "none (listing only)"))
	}
	if e.Links.Human != "" {
		fmt.Fprintln(a.Out, "  "+st.Field("source page", e.Links.Human))
	}
	if e.Alternates > 0 {
		fmt.Fprintln(a.Out, "  "+st.Field("also listed by", otherSources(e.Alternates)))
	}
}

func (a *app) printCatalogPreview(ctx context.Context, p *catalogPreview) {
	st := theme.StylesFromContext(ctx)
	fmt.Fprintln(a.Out)
	title := cmdcore.ValueOr(p.Info.Title, "(untitled)")
	if p.Info.Version != "" {
		title += " " + p.Info.Version
	}
	fmt.Fprintln(a.Out, st.Heading.Render("Operations")+st.Dim.Render("  "+title))
	if len(p.Data) == 0 {
		fmt.Fprintln(a.Out, "  "+st.Dim.Render("no operations"))
		return
	}
	for _, op := range p.Data {
		fmt.Fprintln(a.Out, "  "+catalogOpLine(st, op))
	}
	shown := p.Offset + len(p.Data)
	if p.Truncated || shown < p.Total {
		fmt.Fprintln(a.Out, "  "+st.Dim.Render(fmt.Sprintf("… showing %d of %d operations", len(p.Data), p.Total)))
	}
}

// catalogOpLine renders "METHOD  path  summary" with the method tinted.
func catalogOpLine(st theme.Styles, op catalogPreviewOp) string {
	method := st.Accent.Render(fmt.Sprintf("%-6s", op.Method))
	line := method + " " + st.Command.Render(op.Path)
	if op.Summary != "" {
		line += "  " + st.Dim.Render(op.Summary)
	}
	return line
}

func (a *app) printImportResult(ctx context.Context, result *catalogImportResult, promoted map[string]string, noPromote bool) {
	st := theme.StylesFromContext(ctx)
	if len(result.Revisions) == 0 {
		fmt.Fprintln(a.Out, st.Warnf("Import completed but produced no revisions."))
		return
	}
	fmt.Fprintln(a.Out, st.Successf("Imported %d revision(s):", len(result.Revisions)))
	for _, rev := range result.Revisions {
		ref := fmt.Sprintf("%s/%s/%s", rev.API.Vendor, rev.API.Name, rev.API.Version)
		state := rev.State
		dot := st.DotOK()
		if outcome, ok := promoted[rev.RevisionID]; ok {
			switch outcome {
			case "live":
				state = "live"
			case rev.State:
				// unchanged (already non-draft)
			default:
				state = outcome
				dot = st.DotWarn()
			}
		} else if noPromote {
			state = rev.State + " (not promoted)"
		}
		fmt.Fprintln(a.Out, "  "+dot+" "+st.Accent.Render(ref)+"  "+st.Dim.Render(rev.RevisionID)+"  "+st.Field("state", state))
	}
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
