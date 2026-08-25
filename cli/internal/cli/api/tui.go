package api

import (
	"strings"

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
