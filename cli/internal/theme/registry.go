package theme

import "github.com/charmbracelet/lipgloss"

// Themes is the registry of built-in palettes, keyed by the names persisted in
// config (Context/Config theme) and accepted by --theme / JENTIC_THEME. The three
// keys are the closed set (14 BC-9): "dark", "light", "no-color". ResolveTheme
// falls back to "dark" for any unknown name.
//
// The "dark"/"light" accents intentionally reuse the brand tokens (theme.go) so
// the runtime UI matches the banner; "no-color" zeroes every slot with
// lipgloss.NoColor{} so machine consumers never see an ANSI escape.
var Themes = map[string]Palette{
	"dark": {
		Primary:   Brand,  // teal
		Secondary: Blue,   // #68BAEC
		Error:     Red,    // #DB3B0F
		Success:   Green,  // mint
		Warning:   Orange, // #FDBD79
		Muted:     Muted,  // grey-teal
	},
	"light": {
		Primary:   lipgloss.Color("#4B0082"), // dark indigo
		Secondary: lipgloss.Color("#008B8B"), // dark cyan
		Error:     lipgloss.Color("#B22222"), // firebrick
		Success:   lipgloss.Color("#228B22"), // forest green
		Warning:   lipgloss.Color("#B8860B"), // dark goldenrod
		Muted:     lipgloss.Color("#A9A9A9"), // dark grey
	},
	"no-color": {
		Primary:   lipgloss.NoColor{},
		Secondary: lipgloss.NoColor{},
		Error:     lipgloss.NoColor{},
		Success:   lipgloss.NoColor{},
		Warning:   lipgloss.NoColor{},
		Muted:     lipgloss.NoColor{},
	},
}
