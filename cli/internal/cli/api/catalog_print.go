package api

import (
	"fmt"
	"time"

	"github.com/jentic/jentic-one/cli/internal/theme"
)

func (a *app) printCatalogList(entries []catalogEntry, meta *catalogListResult) {
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
// accent api_id, a dim vendor when it differs, and an "UPDATE AVAILABLE" marker
// when the entry's upstream spec has changed since import.
func catalogRow(e catalogEntry) string {
	glyph := theme.Dim.Render(theme.SelectOff)
	if e.Registered {
		glyph = theme.Success.Render(theme.SelectOn)
	}
	row := glyph + " " + theme.Accent.Render(e.APIID)
	if e.Vendor != "" && e.Vendor != e.APIID {
		row += "  " + theme.Dim.Render(e.Vendor)
	}
	if e.UpdateAvailable {
		row += "  " + theme.Warn.Render("UPDATE AVAILABLE")
	}
	return row
}

func catalogStatusLine(m *catalogListResult) string {
	age := "age unknown"
	if m.ManifestAgeSeconds != nil {
		age = "cache " + humanizeAge(*m.ManifestAgeSeconds)
	}
	line := fmt.Sprintf("%d entries · %d imported · %s", m.CatalogTotal, m.RegisteredCount, age)
	if m.OutdatedCount > 0 {
		line += fmt.Sprintf(" · %d update(s) available", m.OutdatedCount)
	}
	return line
}

func (a *app) printCatalogEntry(e *catalogEntry) {
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

func (a *app) printCatalogPreview(p *catalogPreview) {
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
func catalogOpLine(op catalogPreviewOp) string {
	method := theme.Accent.Render(fmt.Sprintf("%-6s", op.Method))
	line := method + " " + theme.Command.Render(op.Path)
	if op.Summary != "" {
		line += "  " + theme.Dim.Render(op.Summary)
	}
	return line
}

func (a *app) printImportResult(result *catalogImportResult, promoted map[string]string, noPromote bool) {
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
