package ux

import (
	"errors"
	"fmt"
	"os"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/lipgloss"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// HumanUX is the interactive mode: huh prompts, themed accents, indented JSON /
// formatted lines on stdout, a red error line on stderr.
type HumanUX struct {
	theme     theme.Palette
	assumeYes bool // --yes: skip the [y/N] prompt and proceed
}

// NewHumanUX builds the human audience with the resolved palette and the global
// --yes setting.
func NewHumanUX(t Palette, assumeYes bool) *HumanUX {
	return &HumanUX{theme: t, assumeYes: assumeYes}
}

// Ask prompts the human for a value, or fails with a missing-flag CodedError when
// the session is not promptable.
func (h *HumanUX) Ask(question, flagName string, required bool) (string, error) {
	// A TTY is necessary but not sufficient (jentic-one#841): a non-promptable human
	// session (piped, TERM=dumb) must NOT open a form that can hang uncancellably.
	// Behave like agent Ask instead — fail with the missing-flag instruction.
	if !promptable() {
		return "", &CodedError{
			Code:       CodeMissingArgument,
			Msg:        fmt.Sprintf("the required flag '--%s' was not provided", flagName),
			Actionable: fmt.Sprintf("Re-run the command with --%s <value>", flagName),
		}
	}

	var response string
	// Route through install.Input so the prompt inherits the shared brand theme and
	// the TERM=dumb-safe keymap/quit handling (the form-guard test enforces this;
	// it's also the jentic-one#841 fix promptable() guards against).
	err := install.NewForm(huh.NewGroup(
		install.Input().
			Title(question).
			Description("Equivalent to passing --" + flagName).
			Value(&response),
	)).Run()
	if err != nil {
		return "", err
	}
	if required && response == "" {
		return "", errors.New("this field is required")
	}
	return response, nil
}

// AskConfirm asks the human for [y/N] confirmation (auto-yes when --yes was set).
func (h *HumanUX) AskConfirm(warning string) (bool, error) {
	// --yes pre-authorizes: skip the prompt entirely.
	if h.assumeYes {
		return true, nil
	}
	// Non-promptable human session: reject with guidance instead of hanging.
	if !promptable() {
		return false, &CodedError{
			Code:       CodeConfirmBlocked,
			Msg:        "command requires confirmation but the session is not interactive",
			Actionable: "Re-run with the '--yes' flag to authorize this action",
		}
	}
	var confirm bool
	// install.RunConfirm applies the shared theme + quit keymap and the fixed
	// confirm handling (no swallowed first-Enter).
	err := install.RunConfirm(huh.NewConfirm().Title(warning).Value(&confirm))
	return confirm, err
}

// Render writes data to stdout: Page footers, Result status lines, or indented JSON.
func (h *HumanUX) Render(data any) {
	// Paginated results arrive wrapped in a Page: render the items, then a navigation
	// footer. Non-paginated data falls through to the type switch below.
	if page, ok := data.(Page); ok {
		fmt.Fprintln(os.Stdout, string(safeMarshalIndent(page.Items)))
		if page.HasNext() {
			hint := lipgloss.NewStyle().Foreground(h.theme.Muted)
			fmt.Fprintln(os.Stdout, hint.Render("\n"+page.NextHint()))
		}
		return
	}

	// State-changing commands hand us a Result: render one styled status line
	// ("✓ environment 'local' added") rather than a raw JSON dump.
	if res, ok := data.(Result); ok {
		fmt.Fprintln(os.Stdout, h.renderResultLine(res))
		return
	}

	// Everything else: pretty-printed JSON (data uses the default terminal color;
	// theme colors are for UI accents, not raw data).
	fmt.Fprintln(os.Stdout, string(safeMarshalIndent(data)))
}

// renderResultLine composes the one-line human confirmation for a Result.
func (h *HumanUX) renderResultLine(res Result) string {
	ok := lipgloss.NewStyle().Foreground(h.theme.Success).Bold(true)
	subject := res.Resource
	if res.Name != "" {
		subject = fmt.Sprintf("%s '%s'", res.Resource, res.Name)
	}
	line := ok.Render("✓") + " "
	if subject != "" {
		line += fmt.Sprintf("%s %s.", subject, res.Status)
	} else {
		line += res.Status
	}
	if res.Message != "" {
		line += " " + res.Message
	}
	return line
}

// ReportError writes a redacted, styled error line (and optional next step) to stderr.
func (h *HumanUX) ReportError(err error, step string) {
	// Errors MUST go to stderr: stdout is reserved for Render payloads (a single
	// stray byte corrupts a downstream parser). Redact the error path too (M6):
	// error strings routinely echo backend bodies and callers capture 2>&1.
	msg := redactString(err.Error())
	style := lipgloss.NewStyle().Foreground(h.theme.Error).Bold(true)
	fmt.Fprintf(os.Stderr, "%s\n", style.Render("✖ Error: "+msg))
	if step != "" {
		fmt.Fprintf(os.Stderr, "  Next Step: %s\n", step)
	}
}

// Theme returns the resolved human palette.
func (h *HumanUX) Theme() Palette { return h.theme }

// IsFenced reports that humans may run admin commands.
func (h *HumanUX) IsFenced() bool { return false }

// ForcesNoColor reports that human mode respects theme/color preferences.
func (h *HumanUX) ForcesNoColor() bool { return false }
