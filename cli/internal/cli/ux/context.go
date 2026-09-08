package ux

import (
	"context"
	"fmt"
	"os"
)

type contextKey string

const audienceKey contextKey = "jentic_audience"

// WithAudience stores the resolved Audience in the context for downstream commands.
func WithAudience(ctx context.Context, aud Audience) context.Context {
	return context.WithValue(ctx, audienceKey, aud)
}

// FromContext retrieves the Audience. If none is present it FAILS CLOSED to strict
// agent mode (never a human prompt): reaching this branch means the root
// PersistentPreRunE didn't run — a wiring bug. Silently dropping a human into
// no-prompt agent mode is confusing to debug, so warn loudly on stderr (never
// stdout — that must stay machine-parseable).
func FromContext(ctx context.Context) Audience {
	aud, ok := ctx.Value(audienceKey).(Audience)
	if !ok {
		fmt.Fprintln(os.Stderr, "warning: no Audience in context; falling back to strict agent mode (root PersistentPreRunE may not have run)")
		return NewAgentUX(false) // never auto-confirm destructive actions in the fallback
	}
	return aud
}
