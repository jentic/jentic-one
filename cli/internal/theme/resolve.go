package theme

import (
	"context"
	"os"
	"strings"

	"github.com/charmbracelet/x/term"
)

// stdoutIsTTY reports whether standard output is a terminal. It is a package var
// so tests can force either branch of the auto-no-color decision without
// redirecting a real fd. Production always uses the real os.Stdout.
var stdoutIsTTY = func() bool { return term.IsTerminal(os.Stdout.Fd()) }

// ResolveTheme applies the HUMAN-mode precedence ladder and returns the resolved
// Palette. The Stage-0 Mode gate (agent/service-account -> no-color) is applied by
// the root interceptor (impl/3.2 §2) BEFORE this function and overrides everything
// here; this function only owns the human-mode ladder (impl/1.4 §3):
//
//	--theme  >  NO_COLOR / non-TTY stdout  >  JENTIC_THEME  >  config theme  >  dark
//
// flagOverride is the --theme value (may be ""); configTheme is
// ActiveState.ThemeName (the persisted config `theme`).
func ResolveTheme(flagOverride, configTheme string) Palette {
	p, _ := ResolveThemeWithName(flagOverride, configTheme)
	return p
}

// ResolveThemeWithName is ResolveTheme that also returns the resolved theme
// NAME ("dark"/"light"/"no-color"). The name is needed for surfaces whose tint
// isn't a single palette slot — notably the logo's 6-row gradient — so they can
// look up a per-theme gradient. The precedence ladder is identical to
// ResolveTheme; an explicit --theme still wins over the auto no-color rungs.
func ResolveThemeWithName(flagOverride, configTheme string) (Palette, string) {
	if flagOverride == "" {
		if _, noColor := os.LookupEnv("NO_COLOR"); noColor || !stdoutIsTTY() {
			return Themes["no-color"], "no-color"
		}
	}
	name := firstNonEmpty(flagOverride, os.Getenv("JENTIC_THEME"), configTheme, defaultThemeName())
	if p, ok := Themes[name]; ok {
		return p, name
	}
	return Themes["dark"], "dark" // unknown name falls back rather than erroring
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

// defaultThemeName is the final rung of the theme ladder, used ONLY when
// --theme, JENTIC_THEME, and the config theme all miss (the explicit choices,
// the NO_COLOR/non-TTY branch, and the Stage-0 mode gate all short-circuit
// ahead of it). It auto-detects a light terminal background from COLORFGBG (set
// by many terminals: "fg;bg" where bg 0-6/8 is dark, 7/15 or a high value is
// light) and returns "light" for a light background, else "dark". COLORFGBG is
// used because it needs no new dependency and no terminal round-trip; when it is
// absent or unparseable we keep the historical "dark" default.
func defaultThemeName() string {
	if detectLightBackground() {
		return "light"
	}
	return "dark"
}

// detectLightBackground reports whether COLORFGBG indicates a light terminal
// background. Format is "fg;bg" (some terminals emit "fg;default;bg"); the last
// field is the background colour index. Indices 7 and 15 (and 9-15 generally)
// are the light end of the 16-colour palette. Package var so tests can force it.
var detectLightBackground = func() bool {
	v := os.Getenv("COLORFGBG")
	if v == "" {
		return false
	}
	parts := strings.Split(v, ";")
	bg := parts[len(parts)-1]
	switch bg {
	case "7", "15":
		return true
	default:
		return false
	}
}

// WithContext / FromContext carry the resolved Palette through a command context.
// This is the CONVENIENCE accessor for low-level UI helpers that hold a
// context.Context but not the Audience; the Audience (aud.Theme()) is the primary
// runtime source (impl/1.4 §3, impl/3.2). The root hook keeps both in sync.
type ctxKey string

const (
	paletteKey   ctxKey = "jentic_palette"
	themeNameKey ctxKey = "jentic_theme_name"
)

// WithContext stores the resolved Palette in the context for UI helpers that hold
// a context.Context but not the Audience.
func WithContext(ctx context.Context, p Palette) context.Context {
	return context.WithValue(ctx, paletteKey, p)
}

// FromContext returns the Palette carried in ctx, or the dark default if absent
// (or if ctx is nil — cobra hands a nil context to a command that never had one
// set, e.g. in unit tests that invoke a renderer directly).
func FromContext(ctx context.Context) Palette {
	if ctx == nil {
		return Themes["dark"]
	}
	if p, ok := ctx.Value(paletteKey).(Palette); ok {
		return p
	}
	return Themes["dark"]
}

// WithThemeName stores the resolved theme name (for logo-gradient lookup) in ctx.
func WithThemeName(ctx context.Context, name string) context.Context {
	return context.WithValue(ctx, themeNameKey, name)
}

// ThemeNameFromContext returns the resolved theme name carried in ctx, or "dark"
// when absent (or when ctx is nil) — the name surfaces (e.g. the logo gradient)
// use to tint.
func ThemeNameFromContext(ctx context.Context) string { //nolint:revive // seam API name is referenced by callers by this spelling
	if ctx == nil {
		return "dark"
	}
	if n, ok := ctx.Value(themeNameKey).(string); ok && n != "" {
		return n
	}
	return "dark"
}
