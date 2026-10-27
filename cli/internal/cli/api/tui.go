package api

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// browserChrome is the number of terminal rows reserved for the logo, title,
// status, filter, spacer, and footer/hint surrounding a browser's scrolling
// list: logo (6) + blank (1) + title/status (1) + filter (1) + spacer (1) +
// footer/hint (3) ≈ 13.
const browserChrome = 13

// browserVisibleRows is the height of the scrolling list region for a browser
// at the given terminal height, clamped to a sensible [3, 20] range.
func browserVisibleRows(height int) int {
	rows := height - browserChrome
	if rows < 3 {
		rows = 3
	}
	if rows > 20 {
		rows = 20
	}
	return rows
}

// renderListColumn renders the single scrolling column shared by the API and
// catalog browsers. It clamps *top so the cursor stays within the visible
// window, renders each visible row via rowFn, appends a "↓ more" affordance when
// further pages exist, and fixes the column width.
func renderListColumn(st theme.Styles, cursor int, top *int, rows, n, width int, hasMore bool, rowFn func(i int) string) string {
	if cursor < *top {
		*top = cursor
	}
	if cursor >= *top+rows {
		*top = cursor - rows + 1
	}
	end := *top + rows
	if end > n {
		end = n
	}

	lines := make([]string, 0, end-*top+1)
	for i := *top; i < end; i++ {
		lines = append(lines, rowFn(i))
	}
	if hasMore && end >= n {
		lines = append(lines, st.Dim.Render("  ↓ more"))
	}
	return lipgloss.NewStyle().Width(width).Render(strings.Join(lines, "\n"))
}

// listBrowser is the state and behaviour the two-column TUI browsers
// (`jentic apis`, `jentic catalog`) share over a paged list of T: the cursor
// and scroll window, cursor paging, the search input, and the
// loading/error/empty frame around the list and detail columns. Each browser
// embeds it and supplies its rows, detail pane and hints (listFrame).
type listBrowser[T any] struct {
	// st is the palette-bound style set (resolved from ctx at construction);
	// themeName the resolved theme name for the logo gradient, so a browser
	// re-tints under --theme light/no-color like every other surface.
	st        theme.Styles
	themeName string

	// listWidth is the width of the scrolling list column.
	listWidth int

	items      []T
	cursor     int
	top        int
	nextCursor string
	hasMore    bool
	limit      int

	// query is the applied search; searchInput the text being typed while
	// searching.
	query       string
	searching   bool
	searchInput string

	status  string
	loading bool
	err     string

	width, height int
	done          bool
}

// listFrame is what one browser renders inside the shared frame.
type listFrame struct {
	title  string // the heading, e.g. "Catalog"
	header string // the dim status beside the heading
	filter string // the filter/search line under the heading
	empty  string // shown when the list has no items
	hint   string // the key hint line
	footer string // the status/confirm lines and hint under the columns
	// row renders list item i; detail renders the selected item's pane.
	row    func(i int) string
	detail func() string
}

// current is the item under the cursor.
func (l *listBrowser[T]) current() (T, bool) {
	if l.cursor < 0 || l.cursor >= len(l.items) {
		var zero T
		return zero, false
	}
	return l.items[l.cursor], true
}

// applyPage installs one page of items: a reset replaces the list and homes
// the cursor, otherwise the page is appended.
func (l *listBrowser[T]) applyPage(items []T, reset, hasMore bool, nextCursor string) {
	l.loading = false
	l.err = ""
	if reset {
		l.items = items
		l.cursor = 0
		l.top = 0
	} else {
		l.items = append(l.items, items...)
	}
	l.hasMore = hasMore && nextCursor != ""
	l.nextCursor = nextCursor
}

// onListKey handles the keys every list browser shares — quit, cursor moves
// (prefetching the next page through loadMore as the cursor nears the end)
// and "/" to start a search — and reports false for any other key.
func (l *listBrowser[T]) onListKey(msg tea.KeyMsg, loadMore func() tea.Cmd) (tea.Cmd, bool) {
	switch msg.String() {
	case "ctrl+c", "q":
		l.done = true
		return tea.Quit, true
	case "up", "k":
		if l.cursor > 0 {
			l.cursor--
		}
		return nil, true
	case "down", "j":
		if l.cursor < len(l.items)-1 {
			l.cursor++
		}
		if l.hasMore && !l.loading && l.cursor >= len(l.items)-3 {
			l.loading = true
			return loadMore(), true
		}
		return nil, true
	case "/":
		l.searching = true
		l.searchInput = l.query
		l.status = ""
		return nil, true
	}
	return nil, false
}

// onSearchKey edits the search input; enter applies it as the query and
// reloads the list through reload.
func (l *listBrowser[T]) onSearchKey(msg tea.KeyMsg, reload func() tea.Cmd) tea.Cmd {
	switch msg.String() {
	case "esc":
		l.searching = false
		return nil
	case "enter":
		l.searching = false
		l.query = strings.TrimSpace(l.searchInput)
		l.loading = true
		return reload()
	case "backspace":
		if l.searchInput != "" {
			l.searchInput = l.searchInput[:len(l.searchInput)-1]
		}
		return nil
	case "ctrl+c":
		l.done = true
		return tea.Quit
	default:
		if len(msg.Runes) > 0 {
			l.searchInput += string(msg.Runes)
		}
		return nil
	}
}

func (l *listBrowser[T]) visibleRows() int {
	return browserVisibleRows(l.height)
}

// detailWidth is the width left for the detail pane beside the list.
func (l *listBrowser[T]) detailWidth() int {
	w := l.width - l.listWidth - 6
	if w < 20 {
		w = 20
	}
	return w
}

// listRow renders one list row: a selected/unselected glyph (on) and the
// label, accented under the cursor.
func (l *listBrowser[T]) listRow(i int, on bool, label string) string {
	glyph := l.st.Dim.Render(theme.SelectOff)
	if on {
		glyph = l.st.Success.Render(theme.SelectOn)
	}
	name := truncate(label, l.listWidth-3)
	if i == l.cursor {
		return glyph + " " + l.st.Accent.Render(name)
	}
	return glyph + " " + lipgloss.NewStyle().Foreground(theme.White).Render(name)
}

// view renders the browser: the logo (Init clears the screen, wiping the
// global PersistentPreRun banner, so it is redrawn here), the heading and
// filter line, then the error, loading or empty state — or the list and
// detail columns and the footer.
func (l *listBrowser[T]) view(f listFrame) string {
	if l.done {
		return ""
	}
	var b strings.Builder
	b.WriteString(theme.LogoFor(l.themeName))
	b.WriteByte('\n')
	b.WriteString(l.st.Heading.Render(f.title))
	b.WriteString(l.st.Dim.Render("  " + f.header))
	b.WriteByte('\n')
	b.WriteString(l.st.Dim.Render(f.filter))
	b.WriteString("\n\n")

	switch {
	case l.err != "":
		b.WriteString(l.st.Error.Render(l.err) + "\n\n")
		b.WriteString(f.hint)
		return b.String()
	case l.loading && len(l.items) == 0:
		b.WriteString(l.st.Dim.Render("loading …") + "\n\n")
		b.WriteString(f.hint)
		return b.String()
	case len(l.items) == 0:
		b.WriteString(l.st.Dim.Render(f.empty) + "\n\n")
		b.WriteString(f.hint)
		return b.String()
	}

	list := renderListColumn(l.st, l.cursor, &l.top, l.visibleRows(), len(l.items), l.listWidth, l.hasMore, f.row)
	detail := lipgloss.NewStyle().
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(theme.Muted).
		BorderLeft(true).
		PaddingLeft(2).
		Render(f.detail())
	b.WriteString(lipgloss.JoinHorizontal(lipgloss.Top, list, detail))
	b.WriteString("\n\n")
	b.WriteString(f.footer)
	return b.String()
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
