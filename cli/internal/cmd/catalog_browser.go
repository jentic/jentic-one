package cmd

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/jentic/jentic-one/cli/internal/catalogclient"
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
func (a *App) runCatalogBrowser(ctx context.Context, ident *identityOptions) error {
	client, token, err := a.catalogSession(ctx, ident)
	if err != nil {
		return err
	}
	m := &catalogBrowser{
		ctx:        ctx,
		client:     client,
		token:      token,
		limit:      catalogBrowseLimit,
		width:      90,
		height:     24,
		previews:   map[string]*catalogclient.Preview{},
		previewErr: map[string]string{},
		loading:    true,
	}
	_, err = tea.NewProgram(m).Run()
	return err
}

type catalogBrowser struct {
	ctx    context.Context
	client *catalogclient.Client
	token  string

	entries    []catalogclient.Entry
	cursor     int
	top        int
	nextCursor string
	hasMore    bool

	total      int
	registered int
	ageSeconds *int

	query  string
	filter catalogFilter
	limit  int

	searching   bool
	searchInput string

	previews       map[string]*catalogclient.Preview
	previewErr     map[string]string
	previewLoading string

	importing  string
	refreshing bool
	status     string

	loading bool
	err     string

	width, height int
	done          bool
}

// ── messages ─────────────────────────────────────────────────────────────────

type catPageMsg struct {
	result *catalogclient.ListResult
	reset  bool
	err    error
}

type catPreviewMsg struct {
	apiID   string
	preview *catalogclient.Preview
	err     error
}

type catImportMsg struct {
	apiID    string
	result   *catalogclient.ImportResult
	promoted map[string]string
	err      error
}

type catRefreshMsg struct {
	count int
	err   error
}

// ── commands ─────────────────────────────────────────────────────────────────

func (m *catalogBrowser) loadPage(reset bool) tea.Cmd {
	params := catalogclient.ListParams{
		Q:            m.query,
		Limit:        m.limit,
		Registered:   m.filter == filterRegistered,
		Unregistered: m.filter == filterUnregistered,
	}
	if !reset {
		params.Cursor = m.nextCursor
	}
	ctx, client, token := m.ctx, m.client, m.token
	return func() tea.Msg {
		res, err := client.List(ctx, token, params)
		return catPageMsg{result: res, reset: reset, err: err}
	}
}

func (m *catalogBrowser) refreshCache() tea.Cmd {
	ctx, client, token := m.ctx, m.client, m.token
	return func() tea.Msg {
		count, err := client.Refresh(ctx, token)
		return catRefreshMsg{count: count, err: err}
	}
}

func (m *catalogBrowser) loadPreview(apiID string) tea.Cmd {
	ctx, client, token := m.ctx, m.client, m.token
	return func() tea.Msg {
		p, err := client.Preview(ctx, token, apiID, 0, catalogPreviewOps, "")
		return catPreviewMsg{apiID: apiID, preview: p, err: err}
	}
}

func (m *catalogBrowser) importEntry(apiID string) tea.Cmd {
	ctx, client, token := m.ctx, m.client, m.token
	return func() tea.Msg {
		jobID, err := client.Import(ctx, token, apiID)
		if err != nil {
			return catImportMsg{apiID: apiID, err: err}
		}
		job, err := pollImportJobProgress(ctx, client, token, jobID, 2*time.Minute, nil)
		if err != nil {
			return catImportMsg{apiID: apiID, err: err}
		}
		if job.Status != catalogclient.JobCompleted {
			return catImportMsg{apiID: apiID, err: fmt.Errorf("%s: %s", job.Status, valueOr(job.Error, "no detail"))}
		}
		result, err := client.JobResult(ctx, token, jobID)
		if err != nil {
			return catImportMsg{apiID: apiID, err: err}
		}
		promoted := map[string]string{}
		for _, rev := range result.Revisions {
			if rev.State != "draft" {
				promoted[rev.RevisionID] = rev.State
				continue
			}
			if e := client.Promote(ctx, token, rev.API.Vendor, rev.API.Name, rev.API.Version, rev.RevisionID); e != nil {
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
		var he *catalogclient.HTTPError
		if errors.As(msg.err, &he) && he.StatusCode == http.StatusForbidden {
			m.status = theme.Warnf("refresh needs org:admin")
			return m, nil
		}
		m.status = theme.Warnf("refresh failed: %v", msg.err)
		return m, nil
	}
	m.status = theme.Successf("cache updated · %d entries", msg.count)
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
	m.err = ""
	res := msg.result
	if msg.reset {
		m.entries = res.Data
		m.cursor = 0
		m.top = 0
	} else {
		m.entries = append(m.entries, res.Data...)
	}
	m.total = res.CatalogTotal
	m.registered = res.RegisteredCount
	m.ageSeconds = res.ManifestAgeSeconds
	m.hasMore = res.HasMore && res.NextCursor != ""
	m.nextCursor = res.NextCursor
	return m, nil
}

func (m *catalogBrowser) onImport(msg catImportMsg) (tea.Model, tea.Cmd) {
	if m.importing == msg.apiID {
		m.importing = ""
	}
	if msg.err != nil {
		m.status = theme.Warnf("import %s failed: %v", msg.apiID, msg.err)
		return m, nil
	}
	// Mark the entry as imported and bump the counter.
	for i := range m.entries {
		if m.entries[i].APIID == msg.apiID {
			if !m.entries[i].Registered {
				m.registered++
			}
			m.entries[i].Registered = true
		}
	}
	m.status = theme.Successf("imported %s (%d revision(s))", msg.apiID, len(msg.result.Revisions))
	return m, nil
}

func (m *catalogBrowser) onKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if m.searching {
		return m.onSearchKey(msg)
	}
	switch msg.String() {
	case "ctrl+c", "q":
		m.done = true
		return m, tea.Quit
	case "b", "esc":
		return m.back()
	case "up", "k":
		if m.cursor > 0 {
			m.cursor--
		}
		return m, nil
	case "down", "j":
		if m.cursor < len(m.entries)-1 {
			m.cursor++
		}
		// Prefetch the next page as the cursor nears the end.
		if m.hasMore && !m.loading && m.cursor >= len(m.entries)-3 {
			m.loading = true
			return m, m.loadPage(false)
		}
		return m, nil
	case "/":
		m.searching = true
		m.searchInput = m.query
		m.status = ""
		return m, nil
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
		m.status = theme.Dim.Render("refreshing cache …")
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

func (m *catalogBrowser) onSearchKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		m.searching = false
		return m, nil
	case "enter":
		m.searching = false
		m.query = strings.TrimSpace(m.searchInput)
		m.loading = true
		return m, m.loadPage(true)
	case "backspace":
		if m.searchInput != "" {
			m.searchInput = m.searchInput[:len(m.searchInput)-1]
		}
		return m, nil
	case "ctrl+c":
		m.done = true
		return m, tea.Quit
	default:
		if len(msg.Runes) > 0 {
			m.searchInput += string(msg.Runes)
		}
		return m, nil
	}
}

func (m *catalogBrowser) current() (catalogclient.Entry, bool) {
	if m.cursor < 0 || m.cursor >= len(m.entries) {
		return catalogclient.Entry{}, false
	}
	return m.entries[m.cursor], true
}

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
		m.status = theme.Dim.Render(e.APIID + " is already imported")
		return nil
	}
	m.importing = e.APIID
	m.status = ""
	return m.importEntry(e.APIID)
}

// ── view ─────────────────────────────────────────────────────────────────────

func (m *catalogBrowser) View() string {
	if m.done {
		return ""
	}

	var b strings.Builder
	// Brand the full-screen browser: Init clears the screen, wiping the global
	// PersistentPreRun banner, so the logo is redrawn here (flush top, one blank
	// line beneath — the spacing used by every other branded surface).
	b.WriteString(theme.Logo())
	b.WriteByte('\n')
	b.WriteString(theme.Heading.Render("Catalog"))
	b.WriteString(theme.Dim.Render("  " + m.headerStatus()))
	b.WriteByte('\n')
	b.WriteString(theme.Dim.Render(m.filterLine()))
	b.WriteString("\n\n")

	if m.err != "" {
		b.WriteString(theme.Error.Render(m.err) + "\n\n")
		b.WriteString(m.hintLine())
		return b.String()
	}
	if m.loading && len(m.entries) == 0 {
		b.WriteString(theme.Dim.Render("loading …") + "\n\n")
		b.WriteString(m.hintLine())
		return b.String()
	}
	if len(m.entries) == 0 {
		b.WriteString(theme.Dim.Render("no matching entries") + "\n\n")
		b.WriteString(m.hintLine())
		return b.String()
	}

	body := lipgloss.JoinHorizontal(lipgloss.Top, m.listColumn(), m.detailColumn())
	b.WriteString(body)
	b.WriteString("\n\n")
	if m.status != "" {
		b.WriteString(m.status + "\n")
	}
	b.WriteString(m.hintLine())
	return b.String()
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

func (m *catalogBrowser) visibleRows() int {
	return browserVisibleRows(m.height)
}

func (m *catalogBrowser) listColumn() string {
	return renderListColumn(m.cursor, &m.top, m.visibleRows(), len(m.entries), catalogListColumn, m.hasMore, m.listRow)
}

func (m *catalogBrowser) listRow(i int) string {
	e := m.entries[i]
	glyph := theme.Dim.Render(theme.SelectOff)
	if e.Registered {
		glyph = theme.Success.Render(theme.SelectOn)
	}
	name := truncate(e.APIID, catalogListColumn-3)
	if i == m.cursor {
		return glyph + " " + theme.Accent.Render(name)
	}
	return glyph + " " + lipgloss.NewStyle().Foreground(theme.White).Render(name)
}

func (m *catalogBrowser) detailColumn() string {
	detail := m.detailBody()
	return lipgloss.NewStyle().
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(theme.Muted).
		BorderLeft(true).
		PaddingLeft(2).
		Render(detail)
}

func (m *catalogBrowser) detailBody() string {
	e, ok := m.current()
	if !ok {
		return theme.Dim.Render("no selection")
	}
	var b strings.Builder
	b.WriteString(theme.Heading.Render(e.APIID) + "\n")
	if e.Vendor != "" && e.Vendor != e.APIID {
		b.WriteString(theme.Field("vendor", e.Vendor) + "\n")
	}
	status, dot := "not imported", dotDown()
	if e.Registered {
		status, dot = "imported", dotOK()
	}
	b.WriteString(dot + " " + theme.Field("status", status) + "\n")
	if e.SpecURL != "" {
		b.WriteString(theme.Field("spec", truncate(e.SpecURL, m.detailWidth())) + "\n")
	}

	b.WriteString("\n")
	switch {
	case m.importing == e.APIID:
		b.WriteString(theme.Infof("importing …"))
	case m.previewLoading == e.APIID:
		b.WriteString(theme.Dim.Render("loading operations …"))
	case m.previewErr[e.APIID] != "":
		b.WriteString(theme.Warnf("preview unavailable: %s", m.previewErr[e.APIID]))
	case m.previews[e.APIID] != nil:
		b.WriteString(m.previewBlock(m.previews[e.APIID]))
	default:
		b.WriteString(theme.Dim.Render("press o to preview operations · i to import"))
	}
	return b.String()
}

func (m *catalogBrowser) detailWidth() int {
	w := m.width - catalogListColumn - 6
	if w < 20 {
		w = 20
	}
	return w
}

func (m *catalogBrowser) previewBlock(p *catalogclient.Preview) string {
	var b strings.Builder
	title := valueOr(p.Info.Title, "(untitled)")
	if p.Info.Version != "" {
		title += " " + p.Info.Version
	}
	b.WriteString(theme.Step.Render(title) + "\n")

	descLines := 0
	if desc := strings.TrimSpace(p.Info.Description); desc != "" {
		wrapped := wrapLines(desc, m.detailWidth(), catalogPreviewDescLines)
		descLines = len(wrapped) + 1 // wrapped rows + trailing blank line
		for _, ln := range wrapped {
			b.WriteString(theme.Dim.Render(ln) + "\n")
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
		line := theme.Accent.Render(fmt.Sprintf("%-6s", op.Method)) + " " +
			theme.Command.Render(truncate(op.Path, m.detailWidth()-8))
		b.WriteString(line + "\n")
		shown++
	}
	if shown < p.Total {
		b.WriteString(theme.Dim.Render(fmt.Sprintf("… %d of %d operations", shown, p.Total)))
	}
	return b.String()
}

func (m *catalogBrowser) hintLine() string {
	if m.searching {
		return theme.Dim.Render("type to search · enter apply · esc cancel")
	}
	return theme.Dim.Render("↑/↓ move · / search · f filter · r refresh · o preview · i import · b back · q quit")
}

// wrapLines word-wraps s to width-rune lines, collapsing whitespace, and caps
// the result at maxLines (the final kept line gets an ellipsis if text remains).
func wrapLines(s string, width, maxLines int) []string {
	if width < 1 || maxLines < 1 {
		return nil
	}
	var lines []string
	var cur string
	for _, w := range strings.Fields(s) {
		switch {
		case cur == "":
			cur = w
		case len([]rune(cur))+1+len([]rune(w)) <= width:
			cur += " " + w
		default:
			lines = append(lines, cur)
			cur = w
		}
	}
	if cur != "" {
		lines = append(lines, cur)
	}
	// Cap to maxLines, marking the final kept line when text overflows.
	if len(lines) > maxLines {
		lines = lines[:maxLines]
		lines[maxLines-1] = truncate(lines[maxLines-1]+" …", width)
	}
	return lines
}

// truncate shortens s to n runes with a trailing ellipsis when it overflows.
func truncate(s string, n int) string {
	if n <= 1 {
		return s
	}
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n-1]) + "…"
}
