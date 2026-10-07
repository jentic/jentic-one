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

	// Command / Accent / Step are additional runtime slots the CLI's semantic
	// roles need but that don't collapse cleanly onto the six above (e.g. Command
	// is green and Success is also green+bold, but Step is yellow+bold and Accent
	// is plain yellow — distinct from Warning's orange). They are carried on the
	// Palette so `theme light` can re-tint them too; the dark values equal the
	// historical fixed brand tokens so dark output is unchanged.
	Command lipgloss.TerminalColor
	Accent  lipgloss.TerminalColor
	Step    lipgloss.TerminalColor
}
