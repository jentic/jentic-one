package cmdcore

import (
	"io"
	"log/slog"
	"os"
	"strings"

	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// redactingWriter funnels every slog line through the byte-level redaction
// backstop (impl/3.1 §1 M6 — "M6 applies to slog too") before it reaches the
// real stderr, so a secret that slipped into a log field/message is scrubbed on
// the way out. It is deliberately the narrow byte backstop (layer 3), matching
// what Render/ReportError already apply to payloads.
type redactingWriter struct{ w io.Writer }

func (r redactingWriter) Write(p []byte) (int, error) {
	if _, err := r.w.Write(ux.RedactBytes(p)); err != nil {
		return 0, err
	}
	// Report the caller's byte count, not the (possibly different) redacted
	// length, so slog doesn't treat a size change as a short write.
	return len(p), nil
}

// slogLevel resolves the diagnostic level (impl/3.2 §2d): default warn,
// --verbose → debug, and JENTIC_LOG_LEVEL (reserved alongside the BC-9 env set)
// overrides both for debugging automation. Unknown values fall back to warn.
func slogLevel(verbose bool) slog.Level {
	if lvl := strings.ToLower(strings.TrimSpace(os.Getenv("JENTIC_LOG_LEVEL"))); lvl != "" {
		switch lvl {
		case "debug":
			return slog.LevelDebug
		case "info":
			return slog.LevelInfo
		case "warn", "warning":
			return slog.LevelWarn
		case "error":
			return slog.LevelError
		}
		// Unknown value: ignore and fall through to the flag-derived default.
	}
	if verbose {
		return slog.LevelDebug
	}
	return slog.LevelWarn
}

// setupSlog installs the process-wide default slog handler (impl/3.2 §2d). It is
// mode-dependent and MUST be the only place that calls slog.SetDefault:
//   - destination is ALWAYS stderr (stdout belongs exclusively to Render
//     payloads — 13 §1);
//   - human mode → text handler (readable next to prose); agent/service-account
//     → JSON handler (agents capture 2>&1 and parse it next to the error
//     envelope, where prose would be noise);
//   - every line is redacted (M6 applies to slog too).
//
// It writes to app.Err (overridable in tests) rather than os.Stderr directly, so
// diagnostics honor the same stream the Audience reports errors on.
func setupSlog(app *App, mode string, verbose bool) {
	dst := app.Err
	if dst == nil {
		dst = os.Stderr
	}
	out := redactingWriter{w: dst}
	opts := &slog.HandlerOptions{Level: slogLevel(verbose)}

	var handler slog.Handler
	switch mode {
	case clictx.ModeAgent, clictx.ModeServiceAccount:
		handler = slog.NewJSONHandler(out, opts)
	default: // human (and any fail-closed default)
		handler = slog.NewTextHandler(out, opts)
	}
	slog.SetDefault(slog.New(handler))
}
