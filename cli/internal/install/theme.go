package install

import (
	"os"

	"github.com/charmbracelet/bubbles/key"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/lipgloss"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// The wizard styles its output through the shared brand theme so it matches the
// help screen and the rest of the CLI. These locals are thin aliases kept for
// readability at call sites.
var (
	headingStyle = theme.Heading
	stepStyle    = theme.Step
	commandStyle = theme.Command
	mutedStyle   = theme.Dim
	successStyle = theme.Success
	warnStyle    = theme.Warn
	errorStyle   = theme.Error
)

// FormKeyMap returns the wizard's key bindings: esc (in addition to ctrl+c)
// quits. 'q' is intentionally not bound since text inputs need the letter.
func FormKeyMap() *huh.KeyMap {
	km := huh.NewDefaultKeyMap()
	km.Quit = key.NewBinding(key.WithKeys("esc", "ctrl+c"), key.WithHelp("esc", "quit"))
	return km
}

// FormTheme returns the huh theme used for the interactive form. The focused
// field is highlighted (accent title, green selection); fields that are not yet
// reachable are greyed out so only the active field draws the eye.
func FormTheme() *huh.Theme {
	t := huh.ThemeCharm()

	t.Focused.Title = t.Focused.Title.Foreground(theme.Brand).Bold(true)
	// Single-select cursor: a filled radio ring (replaces huh's default "> ").
	t.Focused.SelectSelector = t.Focused.SelectSelector.SetString(theme.SelectOn + " ").Foreground(theme.Brand)
	t.Focused.SelectedOption = t.Focused.SelectedOption.Foreground(theme.Green)

	// Multi-select: radio checkboxes for the on/off state, plus a subtle caret
	// position cursor (distinct from the checkboxes, and not the "> " default).
	t.Focused.MultiSelectSelector = t.Focused.MultiSelectSelector.SetString("▸ ").Foreground(theme.Brand)
	t.Focused.SelectedPrefix = lipgloss.NewStyle().Foreground(theme.Green).SetString(theme.SelectOn + " ")
	t.Focused.UnselectedPrefix = lipgloss.NewStyle().Foreground(theme.Muted).SetString(theme.SelectOff + " ")

	// Text inputs render a prompt glyph (huh styles it but can't set the string;
	// Input() sets PromptGlyph). Brand the focused prompt so it matches the
	// radio selector colour.
	t.Focused.TextInput.Prompt = t.Focused.TextInput.Prompt.Foreground(theme.Brand)

	// Confirm buttons (Yes/No). huh's default theme paints the focused button
	// with a fuchsia background that is off-brand; repaint both so the selected
	// answer reads as Jentic green and the other is a muted, unobtrusive button.
	t.Focused.FocusedButton = lipgloss.NewStyle().
		Foreground(theme.White).Background(theme.Green).Bold(true).Padding(0, 2)
	t.Focused.BlurredButton = lipgloss.NewStyle().
		Foreground(theme.Muted).Padding(0, 2)

	// Mute everything in blurred (not-yet-focused) fields so nothing there looks
	// selectable or active. Keep the radio glyphs (muted) so no stray "> " shows.
	muted := lipgloss.NewStyle().Foreground(theme.Muted)
	t.Blurred.Title = muted
	t.Blurred.Description = muted
	t.Blurred.SelectSelector = muted.SetString(theme.SelectOff + " ")
	t.Blurred.SelectedOption = muted
	t.Blurred.SelectedPrefix = muted.SetString(theme.SelectOn + " ")
	t.Blurred.UnselectedPrefix = muted.SetString(theme.SelectOff + " ")
	t.Blurred.UnselectedOption = muted
	t.Blurred.MultiSelectSelector = muted.SetString("  ")
	t.Blurred.FocusedButton = t.Blurred.FocusedButton.Foreground(theme.Muted)
	t.Blurred.TextInput.Prompt = muted
	t.Blurred.TextInput.Text = muted

	return t
}

// PromptGlyph is the active-field indicator placed before text inputs. It is the
// filled radio ring so a text field matches the select selector — every field in
// a form shares one look. huh styles the prompt but cannot set its string via the
// theme, so inputs must be built through Input() to pick this up.
const PromptGlyph = theme.SelectOn + " "

// NewForm builds a huh form wired to the shared brand theme and key map. Every
// interactive form in the CLI must go through this (enforced by the guard test
// in form_guard_test.go) so selectors, prompts, and quit keys stay identical
// everywhere. Callers may chain huh options (WithWidth, WithShowHelp, ...).
func NewForm(groups ...*huh.Group) *huh.Form {
	return huh.NewForm(groups...).WithTheme(FormTheme()).WithKeyMap(FormKeyMap())
}

// Input builds a text input carrying the shared prompt glyph. Use it instead of
// huh.NewInput() so the prompt matches the radio selector (enforced by the guard
// test). Callers chain the usual huh.Input options (Title, Value, Validate, ...).
func Input() *huh.Input {
	return huh.NewInput().Prompt(PromptGlyph)
}

// RunConfirm runs a standalone yes/no confirm prompt with the shared brand theme
// and quit keymap. Use it instead of huh.NewConfirm().Run() so every one-off
// confirm looks the same as the wizard's forms.
//
// It deliberately runs without huh's default tea.WithReportFocus(): when a
// confirm program starts back-to-back with another (e.g. right after the wizard
// exits), focus reporting makes the terminal emit an initial focus event whose
// handshake swallows the user's first Enter, so the prompt appears to need two
// presses. Dropping it (while keeping huh's stderr output) fixes that. We must
// re-add tea.WithOutput(os.Stderr) because WithProgramOptions replaces huh's
// defaults wholesale.
func RunConfirm(c *huh.Confirm) error {
	return NewForm(huh.NewGroup(c)).
		WithProgramOptions(tea.WithOutput(os.Stderr)).
		Run()
}

// RunForm applies the shared installer theme/keymap and runs an already-built
// multi-group form (e.g. the schema-derived config screen, impl/6.1) with the
// same terminal posture as RunConfirm — huh output on stderr so it never
// contaminates a piped stdout. The form's fields are bound by the caller, so on
// return the target struct is populated in place.
func RunForm(f *huh.Form) error {
	return f.
		WithTheme(FormTheme()).
		WithKeyMap(FormKeyMap()).
		WithProgramOptions(tea.WithOutput(os.Stderr)).
		Run()
}
