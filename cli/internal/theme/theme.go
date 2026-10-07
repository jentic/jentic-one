// Package theme is the single source of truth for the Jentic CLI's colour
// scheme. The palette is the company brand from the frontend theme
// (github.com/jentic/jentic-frontend-theme, ui/src/index.css accent tokens), so
// the CLI matches the web app. Every surface — help screen, install wizard, and
// command output — styles through this package.
package theme

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// Brand palette — hex values lifted straight from the company theme.
var (
	Brand  = lipgloss.Color("#A3CACC") // primary teal
	Orange = lipgloss.Color("#FDBD79")
	Yellow = lipgloss.Color("#F1E38B")
	Green  = lipgloss.Color("#5EDEB9") // mint
	Pink   = lipgloss.Color("#EDADAF")
	Blue   = lipgloss.Color("#68BAEC")
	Red    = lipgloss.Color("#DB3B0F")
	Muted  = lipgloss.Color("#689296") // primary-500 grey-teal
	White  = lipgloss.Color("#FFFFFF")
)

// Selection glyphs (radio style): a filled ring marks the active/selected
// item, a hollow ring the inactive ones. Shared by the wizard hub menu and
// the huh form selects so selection looks identical everywhere.
const (
	SelectOn  = "◉"
	SelectOff = "○"
)

// Shared styles. These are the RETIRED fixed-brand roles: they now DELEGATE to
// the dark palette's Styles() so dark output stays byte-identical while the
// migrated surfaces read palette-bound roles via StylesFromContext instead.
// Un-migrated call sites keep compiling against these package-level vars, which
// are pinned to dark. New code should prefer theme.StylesFromContext(ctx).
var (
	Heading = Themes["dark"].Styles().Heading
	Step    = Themes["dark"].Styles().Step
	Command = Themes["dark"].Styles().Command
	Dim     = Themes["dark"].Styles().Dim
	Success = Themes["dark"].Styles().Success
	Warn    = Themes["dark"].Styles().Warn
	Error   = Themes["dark"].Styles().Error
	Info    = Themes["dark"].Styles().Info
	Accent  = Themes["dark"].Styles().Accent
)

// Successf renders a printf-formatted string in the success style (dark alias).
func Successf(format string, a ...any) string {
	return Themes["dark"].Styles().Successf(format, a...)
}

// Warnf renders a printf-formatted string in the warning style (dark alias).
func Warnf(format string, a ...any) string { return Themes["dark"].Styles().Warnf(format, a...) }

// Infof renders a printf-formatted string in the info style (dark alias).
func Infof(format string, a ...any) string { return Themes["dark"].Styles().Infof(format, a...) }

// Dimf renders a printf-formatted string in the dim style (dark alias).
func Dimf(format string, a ...any) string { return Themes["dark"].Styles().Dimf(format, a...) }

// Headingf renders a printf-formatted string in the heading style (dark alias).
func Headingf(format string, a ...any) string { return Themes["dark"].Styles().Headingf(format, a...) }

// Field renders an aligned "label: value" pair with a muted label and a
// brand-coloured value, for the key/value listings commands print (dark alias).
func Field(label, value string) string { return Themes["dark"].Styles().Field(label, value) }

// logoLines is the "jentic" figlet (standard font). Kept as plain strings so
// each row can be tinted independently for a vertical gradient.
var logoLines = []string{
	"   _            _   _      ",
	"  (_) ___ _ __ | |_(_) ___ ",
	"  | |/ _ \\ '_ \\| __| |/ __|",
	"  | |  __/ | | | |_| | (__ ",
	" _/ |\\___|_| |_|\\__|_|\\___|",
	"|__/                       ",
}

// Logo renders the gradient "jentic" wordmark. Used by the help screen and the
// install wizard so the brand mark is consistent everywhere (dark alias).
func Logo() string { return LogoFor("dark") }

// LogoHeader renders the gradient wordmark with an optional block of status
// lines (e.g. version info) pinned to the top-right within totalWidth. When the
// terminal is too narrow to fit both (or rightLines is empty / width unknown),
// it falls back to just the logo. The returned string ends in a single newline
// (dark alias).
func LogoHeader(totalWidth int, rightLines []string) string {
	return LogoHeaderFor("dark", totalWidth, rightLines)
}

// VersionPanel formats the CLI and server versions as a single left-to-right
// status line for LogoHeader to pin flush against the right edge. The server
// segment shows the reported version when running, "running" if it is up but
// reports no version, or a dim "offline" when it is not reachable.
func VersionPanel(cliVersion, serverVersion string, serverRunning bool) []string {
	st := Themes["dark"].Styles()
	label := func(s string) string { return st.Dim.Render(s + " ") }

	cli := label("cli") + st.Accent.Render(orValue(cliVersion, "dev"))

	var server string
	if serverRunning {
		server = label("server") + st.Command.Render(orValue(serverVersion, "running"))
	} else {
		server = label("server") + st.Dim.Render("offline")
	}

	return []string{cli + st.Dim.Render("   ") + server}
}

// orValue returns v, or fallback when v is empty.
func orValue(v, fallback string) string {
	if strings.TrimSpace(v) == "" {
		return fallback
	}
	return v
}
