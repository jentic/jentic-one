package theme

import (
	"context"
	"os"
)

// ResolveTheme applies the HUMAN-mode precedence ladder and returns the resolved
// Palette. The Stage-0 Mode gate (agent/service-account -> no-color) is applied by
// the root interceptor (impl/3.2 §2) BEFORE this function and overrides everything
// here; this function only owns the human-mode ladder (impl/1.4 §3):
//
//	--theme  >  JENTIC_THEME  >  NO_COLOR  >  config theme  >  dark
//
// flagOverride is the --theme value (may be ""); configTheme is
// ActiveState.ThemeName (the persisted config `theme`).
func ResolveTheme(flagOverride, configTheme string) Palette {
	// NO_COLOR wins over JENTIC_THEME/config but NOT over an explicit --theme, per
	// the no-color.org convention: its mere presence (even empty) disables colour.
	if _, noColor := os.LookupEnv("NO_COLOR"); noColor && flagOverride == "" {
		return Themes["no-color"]
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
