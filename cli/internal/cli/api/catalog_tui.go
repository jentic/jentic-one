package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

const (
	catalogBrowseLimit      = 50
	catalogListColumn       = 34
	catalogPreviewOps       = 50
	catalogPreviewDescLines = 3
)

type catalogFilter int

const (
	filterAll catalogFilter = iota
	filterRegistered
	filterUnregistered
)

func (f catalogFilter) label() string {
	switch f {
	case filterRegistered:
		return "imported"
	case filterUnregistered:
		return "not imported"
	default:
		return "all"
	}
}

func (f catalogFilter) next() catalogFilter { return (f + 1) % 3 }

// runCatalogBrowser opens the interactive two-column catalog browser.
func (a *app) runCatalogBrowser(ctx context.Context) error {
	client, err := a.catalogSession(ctx)
	if err != nil {
		return err
	}
	m := &catalogBrowser{
		listBrowser: listBrowser[catalogEntry]{
			st:        theme.StylesFromContext(ctx),
			themeName: theme.ThemeNameFromContext(ctx),
			listWidth: catalogListColumn,
			limit:     catalogBrowseLimit,
			width:     90,
			height:    24,
			loading:   true,
		},
		ctx:        ctx,
		client:     client,
		previews:   map[string]*catalogPreview{},
		previewErr: map[string]string{},
	}
	_, err = tea.NewProgram(m).Run()
	return err
}

// catalogBrowser is the catalog's two-column browser: the shared list of
// catalog entries (its query is the catalog search), plus the filter,
// counters, operation previews and in-flight import/refresh.
type catalogBrowser struct {
	listBrowser[catalogEntry]

	ctx    context.Context
	client *catalogClient

	total      int
	registered int
	ageSeconds *int

	filter catalogFilter

	previews       map[string]*catalogPreview
	previewErr     map[string]string
	previewLoading string

	importing  string
	refreshing bool
}

// ── messages ─────────────────────────────────────────────────────────────────

type catPageMsg struct {
	result *catalogListResult
	reset  bool
	err    error
}

type catPreviewMsg struct {
	apiID   string
	preview *catalogPreview
	err     error
}

type catImportMsg struct {
	apiID    string
	result   *catalogImportResult
	promoted map[string]string
	err      error
}

type catRefreshMsg struct {
	count int
	err   error
}

// ── commands ─────────────────────────────────────────────────────────────────

func (m *catalogBrowser) loadPage(reset bool) tea.Cmd {
	params := catalogListParams{
		Q:            m.query,
		Limit:        m.limit,
		Registered:   m.filter == filterRegistered,
		Unregistered: m.filter == filterUnregistered,
	}
	if !reset {
		params.Cursor = m.nextCursor
	}
	ctx, client := m.ctx, m.client
	return func() tea.Msg {
		res, err := client.List(ctx, params)
		return catPageMsg{result: res, reset: reset, err: err}
	}
}

func (m *catalogBrowser) refreshCache() tea.Cmd {
	ctx, client := m.ctx, m.client
	return func() tea.Msg {
		count, err := client.Refresh(ctx)
		return catRefreshMsg{count: count, err: err}
	}
}

func (m *catalogBrowser) loadPreview(apiID string) tea.Cmd {
	ctx, client := m.ctx, m.client
	return func() tea.Msg {
		p, err := client.Preview(ctx, apiID, 0, catalogPreviewOps, "")
		return catPreviewMsg{apiID: apiID, preview: p, err: err}
	}
}

func (m *catalogBrowser) importEntry(apiID string) tea.Cmd {
	ctx, client := m.ctx, m.client
	return func() tea.Msg {
		jobID, err := client.Import(ctx, apiID)
		if err != nil {
			return catImportMsg{apiID: apiID, err: err}
		}
		job, err := pollImportJobProgress(ctx, client, jobID, 2*time.Minute, nil)
		if err != nil {
			return catImportMsg{apiID: apiID, err: err}
		}
		if job.Status != catJobCompleted {
			return catImportMsg{apiID: apiID, err: fmt.Errorf("%s: %s", job.Status, cmdcore.ValueOr(deref(job.Error), "no detail"))}
		}
		result, err := client.JobResult(ctx, jobID)
		if err != nil {
			return catImportMsg{apiID: apiID, err: err}
		}
		promoted := map[string]string{}
		for _, rev := range result.Revisions {
			if rev.State != "draft" {
				promoted[rev.RevisionID] = rev.State
				continue
			}
			if e := client.Promote(ctx, rev.API.Vendor, rev.API.Name, rev.API.Version, rev.RevisionID); e != nil {
				promoted[rev.RevisionID] = "promote failed"
				continue
			}
			promoted[rev.RevisionID] = "live"
		}
		return catImportMsg{apiID: apiID, result: result, promoted: promoted}
	}
}

// ── tea.Model ────────────────────────────────────────────────────────────────

func (m *catalogBrowser) Init() tea.Cmd {
	return tea.Batch(tea.ClearScreen, m.loadPage(true))
}

func (m *catalogBrowser) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		return m, nil
	case catPageMsg:
		return m.onPage(msg)
	case catPreviewMsg:
		if m.previewLoading == msg.apiID {
			m.previewLoading = ""
		}
		if msg.err != nil {
			m.previewErr[msg.apiID] = msg.err.Error()
		} else {
			m.previews[msg.apiID] = msg.preview
		}
		return m, nil
	case catImportMsg:
		return m.onImport(msg)
	case catRefreshMsg:
		return m.onRefresh(msg)
	case tea.KeyMsg:
		return m.onKey(msg)
	}
	return m, nil
}

func (m *catalogBrowser) onRefresh(msg catRefreshMsg) (tea.Model, tea.Cmd) {
	m.refreshing = false
	if msg.err != nil {
		var he *HTTPError
		if errors.As(msg.err, &he) && he.StatusCode == http.StatusForbidden {
			m.status = m.st.Warnf("refresh needs org:admin")
			return m, nil
		}
		m.status = m.st.Warnf("refresh failed: %v", msg.err)
		return m, nil
	}
	m.status = m.st.Successf("cache updated · %d entries", msg.count)
	// Reload the list from the freshly-refreshed snapshot.
	m.loading = true
	return m, m.loadPage(true)
}

func (m *catalogBrowser) onPage(msg catPageMsg) (tea.Model, tea.Cmd) {
	m.loading = false
	if msg.err != nil {
		m.err = catalogListErr(msg.err).Error()
		return m, nil
	}
	res := msg.result
	m.applyPage(res.Data, msg.reset, res.HasMore, res.NextCursor)
	m.total = res.CatalogTotal
	m.registered = res.RegisteredCount
	m.ageSeconds = res.ManifestAgeSeconds
	return m, nil
}

func (m *catalogBrowser) onImport(msg catImportMsg) (tea.Model, tea.Cmd) {
	if m.importing == msg.apiID {
		m.importing = ""
	}
	if msg.err != nil {
		m.status = m.st.Warnf("import %s failed: %v", msg.apiID, msg.err)
		return m, nil
	}
	// Mark the entry as imported and bump the counter.
	for i := range m.items {
		if m.items[i].APIID == msg.apiID {
			if !m.items[i].Registered {
				m.registered++
			}
			m.items[i].Registered = true
		}
	}
	m.status = m.st.Successf("imported %s (%d revision(s))", msg.apiID, len(msg.result.Revisions))
	return m, nil
}

func (m *catalogBrowser) onKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if m.searching {
		return m, m.onSearchKey(msg, m.reload)
	}
	if cmd, ok := m.onListKey(msg, m.loadMore); ok {
		return m, cmd
	}
	switch msg.String() {
	case "b", "esc":
		return m.back()
	case "f":
		m.filter = m.filter.next()
		m.loading = true
		m.status = ""
		return m, m.loadPage(true)
	case "r":
		if m.refreshing {
			return m, nil
		}
		m.refreshing = true
		m.status = m.st.Dim.Render("refreshing cache …")
		return m, m.refreshCache()
	case "o", "enter":
		return m, m.maybeLoadPreview()
	case "i":
		return m, m.maybeImport()
	}
	return m, nil
}

// back peels off one layer of state per press — collapse an open preview,
// then clear the search, then reset the filter — and only quits once the view
// is back at its base (no preview, no query, all entries).
func (m *catalogBrowser) back() (tea.Model, tea.Cmd) {
	if e, ok := m.current(); ok {
		if _, shown := m.previews[e.APIID]; shown {
			delete(m.previews, e.APIID)
			delete(m.previewErr, e.APIID)
			m.status = ""
			return m, nil
		}
	}
	if m.query != "" {
		m.query = ""
		m.loading = true
		m.status = ""
		return m, m.loadPage(true)
	}
	if m.filter != filterAll {
		m.filter = filterAll
		m.loading = true
		m.status = ""
		return m, m.loadPage(true)
	}
	m.done = true
	return m, tea.Quit
}

// reload re-reads the list from its first page.
func (m *catalogBrowser) reload() tea.Cmd { return m.loadPage(true) }

// loadMore reads the next page of the list.
func (m *catalogBrowser) loadMore() tea.Cmd { return m.loadPage(false) }

func (m *catalogBrowser) maybeLoadPreview() tea.Cmd {
	e, ok := m.current()
	if !ok {
		return nil
	}
	if _, cached := m.previews[e.APIID]; cached {
		return nil
	}
	if m.previewLoading == e.APIID {
		return nil
	}
	delete(m.previewErr, e.APIID)
	m.previewLoading = e.APIID
	return m.loadPreview(e.APIID)
}

func (m *catalogBrowser) maybeImport() tea.Cmd {
	e, ok := m.current()
	if !ok || m.importing != "" {
		return nil
	}
	if e.Registered {
		m.status = m.st.Dim.Render(e.APIID + " is already imported")
		return nil
	}
	m.importing = e.APIID
	m.status = ""
	return m.importEntry(e.APIID)
}

// ── view ─────────────────────────────────────────────────────────────────────

func (m *catalogBrowser) View() string {
	footer := m.hintLine()
	if m.status != "" {
		footer = m.status + "\n" + footer
	}
	return m.view(listFrame{
		title:  "Catalog",
		header: m.headerStatus(),
		filter: m.filterLine(),
		empty:  "no matching entries",
		hint:   m.hintLine(),
		footer: footer,
		row:    m.catalogRow,
		detail: m.detailBody,
	})
}

func (m *catalogBrowser) headerStatus() string {
	age := "age unknown"
	if m.ageSeconds != nil {
		age = "cache " + humanizeAge(*m.ageSeconds)
	}
	return fmt.Sprintf("%d entries · %d imported · %s", m.total, m.registered, age)
}

func (m *catalogBrowser) filterLine() string {
	parts := []string{"filter: " + m.filter.label()}
	if m.query != "" {
		parts = append(parts, fmt.Sprintf("search: %q", m.query))
	}
	if m.searching {
		return "search: " + m.searchInput + "▏"
	}
	return strings.Join(parts, "   ")
}

func (m *catalogBrowser) catalogRow(i int) string {
	e := m.items[i]
	return m.listRow(i, e.Registered, e.APIID)
}

func (m *catalogBrowser) detailBody() string {
	e, ok := m.current()
	if !ok {
		return m.st.Dim.Render("no selection")
	}
	var b strings.Builder
	b.WriteString(m.st.Heading.Render(e.APIID) + "\n")
	if e.Vendor != "" && e.Vendor != e.APIID {
		b.WriteString(m.st.Field("vendor", e.Vendor) + "\n")
	}
	status, dot := "not imported", m.st.DotDown()
	if e.Registered {
		status, dot = "imported", m.st.DotOK()
	}
	b.WriteString(dot + " " + m.st.Field("status", status) + "\n")
	if e.SpecURL != "" {
		b.WriteString(m.st.Field("spec", truncate(e.SpecURL, m.detailWidth())) + "\n")
	}

	b.WriteString("\n")
	switch {
	case m.importing == e.APIID:
		b.WriteString(m.st.Infof("importing …"))
	case m.previewLoading == e.APIID:
		b.WriteString(m.st.Dim.Render("loading operations …"))
	case m.previewErr[e.APIID] != "":
		b.WriteString(m.st.Warnf("preview unavailable: %s", m.previewErr[e.APIID]))
	case m.previews[e.APIID] != nil:
		b.WriteString(m.previewBlock(m.previews[e.APIID]))
	default:
		b.WriteString(m.st.Dim.Render("press o to preview operations · i to import"))
	}
	return b.String()
}

func (m *catalogBrowser) previewBlock(p *catalogPreview) string {
	var b strings.Builder
	title := cmdcore.ValueOr(p.Info.Title, "(untitled)")
	if p.Info.Version != "" {
		title += " " + p.Info.Version
	}
	b.WriteString(m.st.Step.Render(title) + "\n")

	descLines := 0
	if desc := strings.TrimSpace(p.Info.Description); desc != "" {
		wrapped := wrapLines(desc, m.detailWidth(), catalogPreviewDescLines)
		descLines = len(wrapped) + 1 // wrapped rows + trailing blank line
		for _, ln := range wrapped {
			b.WriteString(m.st.Dim.Render(ln) + "\n")
		}
		b.WriteString("\n")
	}

	maxOps := m.visibleRows() - 4 - descLines
	if maxOps < 3 {
		maxOps = 3
	}
	shown := 0
	for _, op := range p.Data {
		if shown >= maxOps {
			break
		}
		line := m.st.Accent.Render(fmt.Sprintf("%-6s", op.Method)) + " " +
			m.st.Command.Render(truncate(op.Path, m.detailWidth()-8))
		b.WriteString(line + "\n")
		shown++
	}
	if shown < p.Total {
		b.WriteString(m.st.Dim.Render(fmt.Sprintf("… %d of %d operations", shown, p.Total)))
	}
	return b.String()
}

func (m *catalogBrowser) hintLine() string {
	if m.searching {
		return m.st.Dim.Render("type to search · enter apply · esc cancel")
	}
	return m.st.Dim.Render("↑/↓ move · / search · f filter · r refresh · o preview · i import · b back · q quit")
}
