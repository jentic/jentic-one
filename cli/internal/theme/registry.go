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
		Command:   Green,  // mint (matches the historical Command style)
		Accent:    Yellow, // #F1E38B
		Step:      Yellow, // #F1E38B (rendered bold by the Step style)
	},
	"light": {
		Primary:   lipgloss.Color("#4B0082"), // dark indigo   12.95:1 on white
		Secondary: lipgloss.Color("#00696A"), // dark cyan      ≥4.5 (was #008B8B, 4.15)
		Error:     lipgloss.Color("#B22222"), // firebrick      6.68:1
		Success:   lipgloss.Color("#1B7A1B"), // forest green   ≥4.5 (was #228B22, 4.39)
		Warning:   lipgloss.Color("#946A00"), // dark goldenrod ≥4.5 (was #B8860B, 3.25)
		Muted:     lipgloss.Color("#6B6B6B"), // grey           ≈5.0 (was #A9A9A9, 2.35)
		Command:   lipgloss.Color("#1B7A1B"), // green, readable on white
		Accent:    lipgloss.Color("#8A5A00"), // amber, readable on white (yellow is invisible)
		Step:      lipgloss.Color("#8A5A00"), // amber, bold via the Step style
	},
	"no-color": {
		Primary:   lipgloss.NoColor{},
		Secondary: lipgloss.NoColor{},
		Error:     lipgloss.NoColor{},
		Success:   lipgloss.NoColor{},
		Warning:   lipgloss.NoColor{},
		Muted:     lipgloss.NoColor{},
		Command:   lipgloss.NoColor{},
		Accent:    lipgloss.NoColor{},
		Step:      lipgloss.NoColor{},
	},
}

// logoGradients holds the top-to-bottom 6-row logo gradient per theme. dark
// reproduces the historical fixed gradient (theme.go logoColors) exactly so the
// wordmark is byte-identical to pre-palette output; light uses white-readable
// tints; no-color zeroes every row so machine consumers see no ANSI escape.
// logoGradientFor falls back to dark for unknown names.
var logoGradients = map[string][]lipgloss.TerminalColor{
	"dark": {Blue, Green, Brand, Yellow, Orange, Pink},
	"light": {
		lipgloss.Color("#005A9E"), // blue
		lipgloss.Color("#1B7A1B"), // green
		lipgloss.Color("#4B0082"), // indigo (brand analogue on white)
		lipgloss.Color("#8A5A00"), // amber
		lipgloss.Color("#B25A00"), // orange
		lipgloss.Color("#A11D5B"), // pink/magenta
	},
	"no-color": {
		lipgloss.NoColor{},
		lipgloss.NoColor{},
		lipgloss.NoColor{},
		lipgloss.NoColor{},
		lipgloss.NoColor{},
		lipgloss.NoColor{},
	},
}

func logoGradientFor(name string) []lipgloss.TerminalColor {
	if g, ok := logoGradients[name]; ok {
		return g
	}
	return logoGradients["dark"]
}
