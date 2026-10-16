package ux

import (
	"errors"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
)

// fakeTTY makes prompt.Promptable see a real terminal on stdin, so only TERM
// decides the outcome.
func fakeTTY(t *testing.T) {
	t.Helper()
	orig := prompt.StdinIsTerminal
	prompt.StdinIsTerminal = func() bool { return true }
	t.Cleanup(func() { prompt.StdinIsTerminal = orig })
}

// On a TERM=dumb TTY huh's accessible mode cannot be cancelled (jentic-one#841),
// so HumanUX must fail fast with the missing-flag / --yes guidance rather than
// open a form.
func TestHumanUX_DumbTerminalDoesNotPrompt(t *testing.T) {
	t.Setenv("TERM", "dumb")
	fakeTTY(t)
	h := NewHumanUX(Palette{}, false)

	_, err := h.Ask("Name?", "name", true)
	var ce *CodedError
	if !errors.As(err, &ce) || ce.Code != CodeMissingArgument {
		t.Fatalf("Ask on TERM=dumb = %v, want %s CodedError", err, CodeMissingArgument)
	}

	ok, err := h.AskConfirm("Delete everything?")
	if ok || !errors.As(err, &ce) || ce.Code != CodeConfirmBlocked {
		t.Fatalf("AskConfirm on TERM=dumb = (%v, %v), want (false, %s)", ok, err, CodeConfirmBlocked)
	}
}

func TestHumanUX_DumbTerminalHonoursYes(t *testing.T) {
	t.Setenv("TERM", "dumb")
	fakeTTY(t)
	ok, err := NewHumanUX(Palette{}, true).AskConfirm("Delete everything?")
	if !ok || err != nil {
		t.Fatalf("AskConfirm with --yes on TERM=dumb = (%v, %v), want (true, nil)", ok, err)
	}
}
