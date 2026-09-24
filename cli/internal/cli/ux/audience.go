package ux

import (
	"os"

	"github.com/charmbracelet/x/term"
)

// Audience is the sole gatekeeper between a command's business logic and the
// caller's terminal. Commands must NEVER call fmt.Println / huh directly — they go
// through an Audience so the CLI shape-shifts by mode (impl/3.1 §1).
type Audience interface {
	// Ask prompts for missing information. Humans get a TUI prompt; agents get a
	// typed CodedError explaining the missing flag (no interactive fallback).
	Ask(question, flagName string, required bool) (string, error)

	// AskConfirm prompts for a destructive action. Humans get [y/N]; agents
	// auto-reject unless the global --yes was passed.
	AskConfirm(warning string) (bool, error)

	// Render writes a success payload to stdout. Intentionally VOID: the only
	// realistic failure is a marshal error on an exotic value (channel/func) — a
	// programmer bug — which marshalRedacted turns into a guaranteed-encodable error
	// envelope, so stdout always carries exactly one valid JSON document. Callers
	// treat Render as a terminal "this succeeded".
	Render(data any)

	// ReportError writes a failure to stderr (never stdout — stdout is reserved for
	// Render). Humans get a red line; agents get the structured error envelope. Both
	// pass the byte-level redaction backstop (review M6).
	ReportError(err error, actionableNextStep string)

	// Theme returns the active palette.
	Theme() Palette

	// IsFenced reports whether this mode is forbidden from state-mutating admin
	// commands (the root interceptor enforces it — impl/3.2 §2).
	IsFenced() bool

	// ForcesNoColor reports whether ANSI must be suppressed regardless of theme, to
	// protect downstream machine parsers.
	ForcesNoColor() bool
}

// promptable reports whether the current session may open an interactive prompt.
// A TTY is necessary but NOT sufficient (jentic-one#841): TERM=dumb makes huh
// switch to an accessible input loop that ignores context cancellation, so Ctrl-C
// is swallowed on pty harnesses (emacs shell, expect, agent sandboxes). The rule
// is TTY AND TERM != "dumb" (mode is checked by the caller — only HumanUX prompts).
// A non-promptable human session behaves like agent Ask: fail with the missing-flag
// instruction instead of hanging on a prompt.
func promptable() bool {
	if os.Getenv("TERM") == "dumb" {
		return false
	}
	return term.IsTerminal(os.Stdin.Fd())
}
