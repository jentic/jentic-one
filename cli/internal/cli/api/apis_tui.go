package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

const (
	apisBrowseLimit       = 50
	apisListColumn        = 40
	apisPreviewOps        = 50
	apisPreviewDescLines  = 3
	apisRevisionPageLimit = 100
)

type apisView int

const (
	viewAPIs apisView = iota
	viewRevisions
)

// confirmKind identifies a pending destructive action awaiting y/n.
type confirmKind int

const (
	confirmNone confirmKind = iota
	confirmDeleteAPI
	confirmDeleteRevision
)

// runApisBrowser opens the interactive APIs browser.
func (a *app) runApisBrowser(ctx context.Context) error {
	client, err := a.apisSession(ctx)
	if err != nil {
		return err
	}
	m := &apisBrowser{
		ctx:     ctx,
		client:  client,
		limit:   apisBrowseLimit,
		width:   90,
		height:  24,
		ops:     map[string]*operationListResult{},
		opsErr:  map[string]string{},
		loading: true,
	}
	_, err = tea.NewProgram(m).Run()
	return err
}

type apisBrowser struct {
	ctx    context.Context
	client *apiClient

	view apisView

	// APIs list
	apis       []registeredAPI
	cursor     int
	top        int
	nextCursor string
	hasMore    bool
	limit      int

	vendor      string
	searching   bool
	searchInput string

	ops        map[string]*operationListResult
	opsErr     map[string]string
	opsLoading string

	// Revisions drilldown
	revAPI     apiRef
	revs       []apiRevision
	revCursor  int
	revTop     int
	revLoading bool
	revErr     string

	// Pending destructive confirmation
	confirm       confirmKind
	confirmTarget string

	status string
	busy   bool

	loading bool
	err     string

	width, height int
	done          bool
}

// ── messages ─────────────────────────────────────────────────────────────────

type apisPageMsg struct {
	result *apiListResult
	reset  bool
	err    error
}

type apisOpsMsg struct {
	key string
	ops *operationListResult
	err error
}

type apisRevsMsg struct {
	result *revisionListResult
	err    error
}

type apisActionMsg struct {
	verb string // "promoted", "archived", "deleted revision", "deleted API"
	err  error
	back bool // return to the APIs list (after deleting the whole API)
}

// ── commands ─────────────────────────────────────────────────────────────────

func (m *apisBrowser) loadPage(reset bool) tea.Cmd {
	params := apiListParams{Vendor: m.vendor, Limit: m.limit}
	if !reset {
		params.Cursor = m.nextCursor
	}
	ctx, client := m.ctx, m.client
	return func() tea.Msg {
		res, err := client.List(ctx, params)
		return apisPageMsg{result: res, reset: reset, err: err}
	}
}

func (m *apisBrowser) loadOps(ref apiRef) tea.Cmd {
	ctx, client := m.ctx, m.client
	key := apiRefLabel(ref)
	return func() tea.Msg {
		ops, err := client.Operations(ctx, ref.Vendor, ref.Name, ref.Version, "", "", apisPreviewOps)
		return apisOpsMsg{key: key, ops: ops, err: err}
	}
}

func (m *apisBrowser) loadRevs(ref apiRef) tea.Cmd {
	ctx, client := m.ctx, m.client
	return func() tea.Msg {
		res, err := client.Revisions(ctx, ref.Vendor, ref.Name, ref.Version,
			revisionParams{Limit: apisRevisionPageLimit})
		return apisRevsMsg{result: res, err: err}
	}
}

func (m *apisBrowser) promoteRev(ref apiRef, revisionID string) tea.Cmd {
	ctx, client := m.ctx, m.client
	return func() tea.Msg {
		err := client.Promote(ctx, ref.Vendor, ref.Name, ref.Version, revisionID)
		return apisActionMsg{verb: "promoted", err: err}
	}
}

func (m *apisBrowser) archiveRev(ref apiRef, revisionID string) tea.Cmd {
	ctx, client := m.ctx, m.client
	return func() tea.Msg {
		err := client.Archive(ctx, ref.Vendor, ref.Name, ref.Version, revisionID)
		return apisActionMsg{verb: "archived", err: err}
	}
}

func (m *apisBrowser) deleteRev(ref apiRef, revisionID string) tea.Cmd {
	ctx, client := m.ctx, m.client
	return func() tea.Msg {
		err := client.DeleteRevision(ctx, ref.Vendor, ref.Name, ref.Version, revisionID)
		return apisActionMsg{verb: "deleted revision", err: err}
	}
}

func (m *apisBrowser) deleteAPI(ref apiRef) tea.Cmd {
	ctx, client := m.ctx, m.client
	return func() tea.Msg {
		err := client.DeleteAPI(ctx, ref.Vendor, ref.Name, ref.Version)
		return apisActionMsg{verb: "deleted API", err: err, back: true}
	}
}

// ── tea.Model ────────────────────────────────────────────────────────────────

func (m *apisBrowser) Init() tea.Cmd {
	return tea.Batch(tea.ClearScreen, m.loadPage(true))
}

func (m *apisBrowser) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		return m, nil
	case apisPageMsg:
		return m.onPage(msg)
	case apisOpsMsg:
		if m.opsLoading == msg.key {
			m.opsLoading = ""
		}
		if msg.err != nil {
			m.opsErr[msg.key] = msg.err.Error()
		} else {
			m.ops[msg.key] = msg.ops
		}
		return m, nil
	case apisRevsMsg:
		return m.onRevs(msg)
	case apisActionMsg:
		return m.onAction(msg)
	case tea.KeyMsg:
		return m.onKey(msg)
	}
	return m, nil
}

func (m *apisBrowser) onPage(msg apisPageMsg) (tea.Model, tea.Cmd) {
	m.loading = false
	if msg.err != nil {
		m.err = apisListErr(msg.err).Error()
		return m, nil
	}
	m.err = ""
	res := msg.result
	if msg.reset {
		m.apis = res.Data
		m.cursor = 0
		m.top = 0
	} else {
		m.apis = append(m.apis, res.Data...)
	}
	m.hasMore = res.HasMore && res.NextCursor != ""
	m.nextCursor = res.NextCursor
	return m, nil
}

func (m *apisBrowser) onRevs(msg apisRevsMsg) (tea.Model, tea.Cmd) {
	m.revLoading = false
	if msg.err != nil {
		m.revErr = msg.err.Error()
		return m, nil
	}
	m.revErr = ""
	m.revs = msg.result.Data
	if m.revCursor >= len(m.revs) {
		m.revCursor = 0
	}
	m.revTop = 0
	return m, nil
}

func (m *apisBrowser) onAction(msg apisActionMsg) (tea.Model, tea.Cmd) {
	m.busy = false
	if msg.err != nil {
		var he *HTTPError
		if errors.As(msg.err, &he) && he.StatusCode == http.StatusForbidden {
			m.status = theme.Warnf("%s: not permitted (org policy)", msg.verb)
			return m, nil
		}
		m.status = theme.Warnf("%s failed: %v", msg.verb, msg.err)
		return m, nil
	}
	m.status = theme.Successf("%s", msg.verb)
	if msg.back {
		// The whole API is gone — drop back to a freshly reloaded list.
		m.view = viewAPIs
		m.loading = true
		return m, m.loadPage(true)
	}
	// Reload the revisions to reflect the new state.
	m.revLoading = true
	return m, m.loadRevs(m.revAPI)
}

func (m *apisBrowser) onKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if m.confirm != confirmNone {
		return m.onConfirmKey(msg)
	}
	if m.searching {
		return m.onSearchKey(msg)
	}
	if m.view == viewRevisions {
		return m.onRevisionsKey(msg)
	}
	return m.onAPIsKey(msg)
}

func (m *apisBrowser) onAPIsKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
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
		if m.cursor < len(m.apis)-1 {
			m.cursor++
		}
		if m.hasMore && !m.loading && m.cursor >= len(m.apis)-3 {
			m.loading = true
			return m, m.loadPage(false)
		}
		return m, nil
	case "/":
		m.searching = true
		m.searchInput = m.vendor
		m.status = ""
		return m, nil
	case "r":
		m.loading = true
		m.status = ""
		return m, m.loadPage(true)
	case "o":
		return m, m.maybeLoadOps()
	case "enter":
		return m.openRevisions()
	case "d":
		if api, ok := m.current(); ok {
			m.confirm = confirmDeleteAPI
			m.confirmTarget = apiRefLabel(api.API)
			m.revAPI = api.API
		}
		return m, nil
	}
	return m, nil
}

func (m *apisBrowser) onRevisionsKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "ctrl+c", "q":
		m.done = true
		return m, tea.Quit
	case "b", "esc":
		m.view = viewAPIs
		m.status = ""
		return m, nil
	case "up", "k":
		if m.revCursor > 0 {
			m.revCursor--
		}
		return m, nil
	case "down", "j":
		if m.revCursor < len(m.revs)-1 {
			m.revCursor++
		}
		return m, nil
	case "p":
		return m.actOnRevision("promote")
	case "a":
		return m.actOnRevision("archive")
	case "x":
		return m.actOnRevision("delete")
	}
	return m, nil
}

func (m *apisBrowser) onConfirmKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "y", "Y":
		kind := m.confirm
		m.confirm = confirmNone
		m.busy = true
		switch kind {
		case confirmDeleteAPI:
			return m, m.deleteAPI(m.revAPI)
		case confirmDeleteRevision:
			rev, ok := m.currentRev()
			if !ok {
				m.busy = false
				return m, nil
			}
			return m, m.deleteRev(m.revAPI, rev.RevisionID)
		}
		m.busy = false
		return m, nil
	default:
		m.confirm = confirmNone
		m.status = theme.Dim.Render("cancelled")
		return m, nil
	}
}

// actOnRevision applies a lifecycle action to the selected revision, guarding
// for the right state and routing deletes through the confirm prompt.
func (m *apisBrowser) actOnRevision(action string) (tea.Model, tea.Cmd) {
	rev, ok := m.currentRev()
	if !ok || m.busy {
		return m, nil
	}
	switch action {
	case "promote":
		if rev.State != "draft" {
			m.status = theme.Dim.Render("only draft revisions can be promoted")
			return m, nil
		}
		m.busy = true
		m.status = theme.Dim.Render("promoting …")
		return m, m.promoteRev(m.revAPI, rev.RevisionID)
	case "archive":
		if rev.State != "draft" {
			m.status = theme.Dim.Render("only draft revisions can be archived")
			return m, nil
		}
		m.busy = true
		m.status = theme.Dim.Render("archiving …")
		return m, m.archiveRev(m.revAPI, rev.RevisionID)
	case "delete":
		if rev.State != "archived" {
			m.status = theme.Dim.Render("only archived revisions can be deleted")
			return m, nil
		}
		m.confirm = confirmDeleteRevision
		m.confirmTarget = "revision " + rev.RevisionID
		return m, nil
	}
	return m, nil
}

func (m *apisBrowser) openRevisions() (tea.Model, tea.Cmd) {
	api, ok := m.current()
	if !ok {
		return m, nil
	}
	m.view = viewRevisions
	m.revAPI = api.API
	m.revs = nil
	m.revCursor = 0
	m.revTop = 0
	m.revErr = ""
	m.revLoading = true
	m.status = ""
	return m, m.loadRevs(api.API)
}

// back peels one layer in the APIs view: collapse an open ops preview, then
// clear the vendor filter, then quit.
func (m *apisBrowser) back() (tea.Model, tea.Cmd) {
	if api, ok := m.current(); ok {
		key := apiRefLabel(api.API)
		if _, shown := m.ops[key]; shown {
			delete(m.ops, key)
			delete(m.opsErr, key)
			m.status = ""
			return m, nil
		}
	}
	if m.vendor != "" {
		m.vendor = ""
		m.loading = true
		m.status = ""
		return m, m.loadPage(true)
	}
	m.done = true
	return m, tea.Quit
}

func (m *apisBrowser) onSearchKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		m.searching = false
		return m, nil
	case "enter":
		m.searching = false
		m.vendor = strings.TrimSpace(m.searchInput)
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

func (m *apisBrowser) current() (registeredAPI, bool) {
	if m.cursor < 0 || m.cursor >= len(m.apis) {
		return registeredAPI{}, false
	}
	return m.apis[m.cursor], true
}

func (m *apisBrowser) currentRev() (apiRevision, bool) {
	if m.revCursor < 0 || m.revCursor >= len(m.revs) {
		return apiRevision{}, false
	}
	return m.revs[m.revCursor], true
}

func (m *apisBrowser) maybeLoadOps() tea.Cmd {
	api, ok := m.current()
	if !ok {
		return nil
	}
	key := apiRefLabel(api.API)
	if _, cached := m.ops[key]; cached {
		return nil
	}
	if m.opsLoading == key {
		return nil
	}
	delete(m.opsErr, key)
	m.opsLoading = key
	return m.loadOps(api.API)
}

// ── view ─────────────────────────────────────────────────────────────────────

func (m *apisBrowser) View() string {
	if m.done {
		return ""
	}
	var b strings.Builder
	b.WriteString(theme.Logo())
	b.WriteByte('\n')

	if m.view == viewRevisions {
		return m.revisionsView(&b)
	}

	b.WriteString(theme.Heading.Render("APIs"))
	b.WriteString(theme.Dim.Render("  " + m.headerStatus()))
	b.WriteByte('\n')
	b.WriteString(theme.Dim.Render(m.filterLine()))
	b.WriteString("\n\n")

	if m.err != "" {
		b.WriteString(theme.Error.Render(m.err) + "\n\n")
		b.WriteString(m.hintLine())
		return b.String()
	}
	if m.loading && len(m.apis) == 0 {
		b.WriteString(theme.Dim.Render("loading …") + "\n\n")
		b.WriteString(m.hintLine())
		return b.String()
	}
	if len(m.apis) == 0 {
		b.WriteString(theme.Dim.Render("no APIs imported yet — try `jentic catalog`") + "\n\n")
		b.WriteString(m.hintLine())
		return b.String()
	}

	body := lipgloss.JoinHorizontal(lipgloss.Top, m.listColumn(), m.detailColumn())
	b.WriteString(body)
	b.WriteString("\n\n")
	b.WriteString(m.footer())
	return b.String()
}

func (m *apisBrowser) footer() string {
	var b strings.Builder
	if m.confirm != confirmNone {
		b.WriteString(theme.Warnf("Delete %s? (y/n)", m.confirmTarget) + "\n")
	} else if m.status != "" {
		b.WriteString(m.status + "\n")
	}
	b.WriteString(m.hintLine())
	return b.String()
}

func (m *apisBrowser) headerStatus() string {
	n := len(m.apis)
	if m.hasMore {
		return fmt.Sprintf("%d+ loaded", n)
	}
	return fmt.Sprintf("%d API(s)", n)
}

func (m *apisBrowser) filterLine() string {
	if m.searching {
		return "vendor: " + m.searchInput + "▏"
	}
	if m.vendor != "" {
		return fmt.Sprintf("vendor: %q", m.vendor)
	}
	return "all vendors"
}

func (m *apisBrowser) visibleRows() int {
	return browserVisibleRows(m.height)
}

func (m *apisBrowser) listColumn() string {
	return renderListColumn(m.cursor, &m.top, m.visibleRows(), len(m.apis), apisListColumn, m.hasMore, m.listRow)
}

func (m *apisBrowser) listRow(i int) string {
	api := m.apis[i]
	glyph := theme.Dim.Render(theme.SelectOff)
	if api.CurrentRevisionID != "" {
		glyph = theme.Success.Render(theme.SelectOn)
	}
	name := truncate(apiRefLabel(api.API), apisListColumn-3)
	if i == m.cursor {
		return glyph + " " + theme.Accent.Render(name)
	}
	return glyph + " " + lipgloss.NewStyle().Foreground(theme.White).Render(name)
}

func (m *apisBrowser) detailColumn() string {
	return lipgloss.NewStyle().
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(theme.Muted).
		BorderLeft(true).
		PaddingLeft(2).
		Render(m.detailBody())
}

func (m *apisBrowser) detailBody() string {
	api, ok := m.current()
	if !ok {
		return theme.Dim.Render("no selection")
	}
	var b strings.Builder
	b.WriteString(theme.Heading.Render(apiRefLabel(api.API)) + "\n")
	if api.DisplayName != "" {
		b.WriteString(theme.Field("name", api.DisplayName) + "\n")
	}
	state, dot := "no live revision", cmdcore.DotDown()
	if api.CurrentRevisionID != "" {
		state, dot = "live", cmdcore.DotOK()
	}
	b.WriteString(dot + " " + theme.Field("status", state) + "\n")
	b.WriteString(theme.Field("revisions", strconv.Itoa(api.RevisionCount)) + "  " +
		theme.Field("operations", strconv.Itoa(api.OperationCount)) + "\n")
	if len(api.SecuritySchemes) > 0 {
		b.WriteString(theme.Field("auth", truncate(strings.Join(api.SecuritySchemes, ", "), m.detailWidth())) + "\n")
	}

	if desc := strings.TrimSpace(api.Description); desc != "" {
		b.WriteString("\n")
		for _, ln := range wrapLines(desc, m.detailWidth(), apisPreviewDescLines) {
			b.WriteString(theme.Dim.Render(ln) + "\n")
		}
	}

	b.WriteString("\n")
	key := apiRefLabel(api.API)
	switch {
	case m.opsLoading == key:
		b.WriteString(theme.Dim.Render("loading operations …"))
	case m.opsErr[key] != "":
		b.WriteString(theme.Warnf("operations unavailable: %s", m.opsErr[key]))
	case m.ops[key] != nil:
		b.WriteString(m.opsBlock(m.ops[key]))
	default:
		b.WriteString(theme.Dim.Render("press o to preview operations · enter to manage revisions"))
	}
	return b.String()
}

func (m *apisBrowser) detailWidth() int {
	w := m.width - apisListColumn - 6
	if w < 20 {
		w = 20
	}
	return w
}

func (m *apisBrowser) opsBlock(ops *operationListResult) string {
	var b strings.Builder
	b.WriteString(theme.Step.Render("Operations") + "\n")
	if len(ops.Data) == 0 {
		b.WriteString(theme.Dim.Render("no operations"))
		return b.String()
	}
	maxOps := m.visibleRows() - 6
	if maxOps < 3 {
		maxOps = 3
	}
	shown := 0
	for _, op := range ops.Data {
		if shown >= maxOps {
			break
		}
		b.WriteString(theme.Accent.Render(fmt.Sprintf("%-6s", op.Method)) + " " +
			theme.Command.Render(truncate(op.Path, m.detailWidth()-8)) + "\n")
		shown++
	}
	if shown < len(ops.Data) || ops.HasMore {
		b.WriteString(theme.Dim.Render(fmt.Sprintf("… %d shown", shown)))
	}
	return b.String()
}

// ── revisions view ───────────────────────────────────────────────────────────

func (m *apisBrowser) revisionsView(b *strings.Builder) string {
	b.WriteString(theme.Heading.Render("Revisions"))
	b.WriteString(theme.Dim.Render("  " + apiRefLabel(m.revAPI)))
	b.WriteString("\n\n")

	switch {
	case m.revErr != "":
		b.WriteString(theme.Error.Render(m.revErr) + "\n\n")
	case m.revLoading && len(m.revs) == 0:
		b.WriteString(theme.Dim.Render("loading …") + "\n\n")
	case len(m.revs) == 0:
		b.WriteString(theme.Dim.Render("no revisions") + "\n\n")
	default:
		rows := m.visibleRows()
		if m.revCursor < m.revTop {
			m.revTop = m.revCursor
		}
		if m.revCursor >= m.revTop+rows {
			m.revTop = m.revCursor - rows + 1
		}
		end := m.revTop + rows
		if end > len(m.revs) {
			end = len(m.revs)
		}
		for i := m.revTop; i < end; i++ {
			cursor := "  "
			if i == m.revCursor {
				cursor = theme.Accent.Render("› ")
			}
			b.WriteString(cursor + revisionLine(m.revs[i]) + "\n")
		}
		b.WriteString("\n")
	}

	b.WriteString(m.footer())
	return b.String()
}

func (m *apisBrowser) hintLine() string {
	if m.confirm != confirmNone {
		return theme.Dim.Render("y confirm · any other key cancel")
	}
	if m.searching {
		return theme.Dim.Render("type a vendor · enter apply · esc cancel")
	}
	if m.view == viewRevisions {
		return theme.Dim.Render("↑/↓ move · p promote · a archive · x delete · b back · q quit")
	}
	return theme.Dim.Render("↑/↓ move · / vendor · o preview · enter revisions · d delete · r refresh · b back · q quit")
}
