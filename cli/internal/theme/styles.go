package theme

import (
	"context"
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// Styles is the palette-bound set of semantic role styles every CLI surface
// renders through. It is the single seam that turns a runtime-resolved Palette
// into the lipgloss.Style roles that used to be fixed package-level vars in
// theme.go — so `--theme light`/`no-color` actually re-tint help, search,
// doctor, status, the wizard, and the logo instead of always emitting the
// dark-brand hex. Build one with Palette.Styles() (or StylesFromContext) at the
// top of a command and read st.Heading/st.Command/… instead of theme.Heading/….
//
// The role→slot mapping is fixed here so every surface agrees; the dark palette
// (registry.go) carries slot values equal to the historical fixed tokens, so a
// dark Styles() reproduces the previous output byte-for-byte (guarded by a
// golden test).
type Styles struct {
	Heading lipgloss.Style // section titles                (Primary, bold)
	Step    lipgloss.Style // numbered/step emphasis         (Step, bold)
	Command lipgloss.Style // copy-pasteable command text    (Command)
	Dim     lipgloss.Style // de-emphasised / helper text    (Muted)
	Success lipgloss.Style // success confirmations          (Success, bold)
	Warn    lipgloss.Style // warnings                       (Warning)
	Error   lipgloss.Style // errors                         (Error, bold)
	Info    lipgloss.Style // informational accents          (Secondary)
	Accent  lipgloss.Style // subtle highlight               (Accent)

	// fieldValue is the value style used by Field(); the label uses Dim. Kept
	// unexported — call st.Field(label, value) rather than composing it.
	fieldValue lipgloss.Style
}

// Styles builds the role style set from p's slots. The bold/plain choices mirror
// the historical fixed styles in theme.go exactly so dark output is unchanged.
func (p Palette) Styles() Styles {
	return Styles{
		Heading:    lipgloss.NewStyle().Bold(true).Foreground(p.Primary),
		Step:       lipgloss.NewStyle().Bold(true).Foreground(p.Step),
		Command:    lipgloss.NewStyle().Foreground(p.Command),
		Dim:        lipgloss.NewStyle().Foreground(p.Muted),
		Success:    lipgloss.NewStyle().Bold(true).Foreground(p.Success),
		Warn:       lipgloss.NewStyle().Foreground(p.Warning),
		Error:      lipgloss.NewStyle().Bold(true).Foreground(p.Error),
		Info:       lipgloss.NewStyle().Foreground(p.Secondary),
		Accent:     lipgloss.NewStyle().Foreground(p.Accent),
		fieldValue: lipgloss.NewStyle().Foreground(White),
	}
}

// StylesFromContext resolves the role style set from the Palette carried in ctx
// (dark default when absent). This is the primary accessor for command bodies
// that hold a context.Context.
func StylesFromContext(ctx context.Context) Styles {
	return FromContext(ctx).Styles()
}

// Field renders an aligned "label: value" pair — a muted label and a
// bright value — matching the historical package-level Field helper, but tinted
// from the resolved palette.
func (s Styles) Field(label, value string) string {
	return s.Dim.Render(fmt.Sprintf("%-9s ", label+":")) + s.fieldValue.Render(value)
}

// Successf renders a printf-formatted string in the Success role style — the
// palette-bound equivalent of the historical package-level theme.Successf.
func (s Styles) Successf(format string, a ...any) string {
	return s.Success.Render(fmt.Sprintf(format, a...))
}

// Warnf renders a printf-formatted string in the Warn role style.
func (s Styles) Warnf(format string, a ...any) string {
	return s.Warn.Render(fmt.Sprintf(format, a...))
}

// Infof renders a printf-formatted string in the Info role style.
func (s Styles) Infof(format string, a ...any) string {
	return s.Info.Render(fmt.Sprintf(format, a...))
}

// Dimf renders a printf-formatted string in the Dim role style.
func (s Styles) Dimf(format string, a ...any) string {
	return s.Dim.Render(fmt.Sprintf(format, a...))
}

// Headingf renders a printf-formatted string in the Heading role style.
func (s Styles) Headingf(format string, a ...any) string {
	return s.Heading.Render(fmt.Sprintf(format, a...))
}

// DotOK is the palette-bound status glyph for a present/healthy item (filled),
// mirroring the historical fixed cmdcore.DotOK but tinted from the resolved
// palette so light/no-color modes change it too.
func (s Styles) DotOK() string { return s.Success.Render("●") }

// DotWarn is the palette-bound status glyph for a degraded/warning item.
func (s Styles) DotWarn() string { return s.Warn.Render("●") }

// DotDown is the palette-bound status glyph for an absent/offline item (hollow).
func (s Styles) DotDown() string { return s.Dim.Render("○") }

// DotFail is the palette-bound status glyph for a failed item.
func (s Styles) DotFail() string { return s.Error.Render("✗") }

// LogoForContext renders the logo tinted for the theme resolved into ctx.
func LogoForContext(ctx context.Context) string {
	return LogoFor(ThemeNameFromContext(ctx))
}

// VersionPanelFor is VersionPanel tinted from the resolved Styles (palette-aware).
// It formats the CLI and server versions as a single left-to-right status line
// for LogoHeader to pin flush against the right edge. The server segment shows
// the reported version when running, "running" if it is up but reports no
// version, or a dim "offline" when it is not reachable.
func VersionPanelFor(s Styles, cliVersion, serverVersion string, serverRunning bool) []string {
	label := func(str string) string { return s.Dim.Render(str + " ") }

	cli := label("cli") + s.Accent.Render(orValue(cliVersion, "dev"))

	var server string
	if serverRunning {
		server = label("server") + s.Command.Render(orValue(serverVersion, "running"))
	} else {
		server = label("server") + s.Dim.Render("offline")
	}

	return []string{cli + s.Dim.Render("   ") + server}
}

// LogoHeaderForContext is LogoHeaderFor tinted for the theme resolved into ctx.
func LogoHeaderForContext(ctx context.Context, totalWidth int, rightLines []string) string {
	return LogoHeaderFor(ThemeNameFromContext(ctx), totalWidth, rightLines)
}

// LogoFor renders the gradient "jentic" wordmark tinted for the named theme.
// dark reproduces the historical fixed gradient byte-for-byte; light/no-color
// re-tint. Unknown names fall back to dark.
func LogoFor(name string) string {
	colors := logoGradientFor(name)
	var b strings.Builder
	for i, ln := range logoLines {
		c := colors[i%len(colors)]
		b.WriteString(lipgloss.NewStyle().Foreground(c).Bold(true).Render(ln))
		b.WriteByte('\n')
	}
	return b.String()
}

// LogoHeaderFor is LogoHeader tinted for the named theme (palette-aware).
func LogoHeaderFor(name string, totalWidth int, rightLines []string) string {
	logo := strings.TrimRight(LogoFor(name), "\n")
	if len(rightLines) == 0 {
		return logo + "\n"
	}
	right := lipgloss.JoinVertical(lipgloss.Left, rightLines...)
	gap := totalWidth - lipgloss.Width(logo) - lipgloss.Width(right)
	if totalWidth <= 0 || gap < 2 {
		return logo + "\n"
	}
	spacer := strings.Repeat(" ", gap)
	return lipgloss.JoinHorizontal(lipgloss.Top, logo, spacer, right) + "\n"
}
