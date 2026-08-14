package theme

import "github.com/charmbracelet/lipgloss"

// Palette is the semantic colour set the Audience layer (impl/3.1) styles UI
// accents from. It is deliberately SEPARATE from the brand palette/styles in
// theme.go: those are fixed company-brand tokens for the banner and installer
// wizard, whereas a Palette is mode/preference-resolved at runtime (dark, light,
// or no-color) and threads through HumanUX.
//
// Fields are lipgloss.TerminalColor (the interface), NOT the concrete
// lipgloss.Color: the no-color palette assigns lipgloss.NoColor{} — a different
// concrete type — which does not fit a lipgloss.Color-typed field. TerminalColor
// is the interface both satisfy and what every lipgloss Style setter accepts.
type Palette struct {
	Primary   lipgloss.TerminalColor
	Secondary lipgloss.TerminalColor
	Error     lipgloss.TerminalColor
	Success   lipgloss.TerminalColor
	Warning   lipgloss.TerminalColor
	Muted     lipgloss.TerminalColor
}
