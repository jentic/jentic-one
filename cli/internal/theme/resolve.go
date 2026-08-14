package theme

import (
	"context"
	"os"

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
	// An explicit --theme is the operator saying "I want THIS", so it wins even
	// over auto-detection and NO_COLOR. Absent that, disable colour when either
	// (a) NO_COLOR is set (no-color.org: mere presence, even empty, disables it),
	// or (b) stdout is not a terminal (OPS-1): a piped/redirected human run
	// (`jentic … | jq`, `> out.txt`) must not smuggle ANSI escapes into the
	// consumer, matching the universal CLI convention.
	if flagOverride == "" {
		if _, noColor := os.LookupEnv("NO_COLOR"); noColor || !stdoutIsTTY() {
			return Themes["no-color"]
		}
	}
	name := firstNonEmpty(flagOverride, os.Getenv("JENTIC_THEME"), configTheme, "dark")
	if p, ok := Themes[name]; ok {
		return p
	}
	return Themes["dark"] // unknown name falls back rather than erroring
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

// WithContext / FromContext carry the resolved Palette through a command context.
// This is the CONVENIENCE accessor for low-level UI helpers that hold a
// context.Context but not the Audience; the Audience (aud.Theme()) is the primary
// runtime source (impl/1.4 §3, impl/3.2). The root hook keeps both in sync.
type ctxKey string

const paletteKey ctxKey = "jentic_palette"

// WithContext stores the resolved Palette in the context for UI helpers that hold
// a context.Context but not the Audience.
func WithContext(ctx context.Context, p Palette) context.Context {
	return context.WithValue(ctx, paletteKey, p)
}

// FromContext returns the Palette carried in ctx, or the dark default if absent.
func FromContext(ctx context.Context) Palette {
	if p, ok := ctx.Value(paletteKey).(Palette); ok {
		return p
	}
	return Themes["dark"]
}
