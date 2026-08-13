package ux

import (
	"errors"
	"fmt"
	"os"
	"reflect"
	"sort"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/lipgloss"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
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
	// Route through prompt.Input so the prompt inherits the shared brand theme and
	// the TERM=dumb-safe keymap/quit handling (the form-guard test enforces this;
	// it's also the jentic-one#841 fix promptable() guards against).
	err := prompt.NewForm(huh.NewGroup(
		prompt.Input().
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
	// prompt.RunConfirm applies the shared theme + quit keymap and the fixed
	// confirm handling (no swallowed first-Enter).
	err := prompt.RunConfirm(huh.NewConfirm().Title(warning).Value(&confirm))
	return confirm, err
}

// Render writes data to stdout: Page footers, Result status lines, or indented JSON.
func (h *HumanUX) Render(data any) {
	// Paginated results arrive wrapped in a Page: render the items, then a navigation
	// footer. Non-paginated data falls through to the type switch below.
	if page, ok := data.(Page); ok {
		// Config-list commands (context/env/identity list) pass []map[string]any
		// rows; give humans the styled list treatment (UX-4) instead of the raw
		// JSON dump their V1 predecessors already improved on. API-payload pages
		// (typed slices) keep the JSON rendering — that data is the payload.
		if rows, isRows := page.Items.([]map[string]any); isRows {
			h.renderMapList(rows)
		} else {
			fmt.Fprintln(os.Stdout, string(safeMarshalIndent(page.Items)))
		}
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
		h.renderResultFields(res.Fields)
		return
	}

	// Everything else: pretty-printed JSON (data uses the default terminal color;
	// theme colors are for UI accents, not raw data).
	fmt.Fprintln(os.Stdout, string(safeMarshalIndent(data)))
}

// renderMapList renders []map[string]any rows in the styled list treatment the
// legacy `profile list` set the bar with (UX-4): a radio glyph for rows carrying
// an "active" bool, the "name" value as an accented header, then the remaining
// keys as sorted, indented field lines. Values pass the same redaction as
// renderResultFields.
func (h *HumanUX) renderMapList(rows []map[string]any) {
	if len(rows) == 0 {
		fmt.Fprintln(os.Stdout, lipgloss.NewStyle().Foreground(h.theme.Muted).Render("(none)"))
		return
	}
	nameStyle := lipgloss.NewStyle().Foreground(h.theme.Primary).Bold(true)
	keyStyle := lipgloss.NewStyle().Foreground(h.theme.Muted)
	onStyle := lipgloss.NewStyle().Foreground(h.theme.Success)
	offStyle := lipgloss.NewStyle().Foreground(h.theme.Muted)

	for _, row := range rows {
		header := ""
		if active, hasActive := row["active"].(bool); hasActive {
			if active {
				header += onStyle.Render(theme.SelectOn) + " "
			} else {
				header += offStyle.Render(theme.SelectOff) + " "
			}
		}
		if name, hasName := row["name"].(string); hasName {
			header += nameStyle.Render(name)
		}
		if header != "" {
			fmt.Fprintln(os.Stdout, header)
		}

		keys := make([]string, 0, len(row))
		for k := range row {
			if k == "name" || k == "active" {
				continue // already in the header
			}
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			fmt.Fprintln(os.Stdout, "    "+keyStyle.Render(k+":")+" "+h.fieldValue(k, row[k]))
		}
	}
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

// renderResultFields prints Result.Fields as indented themed field lines under
// the status line (UX-3): previously humans got ONLY the one-liner while agent
// mode carried the full envelope — e.g. `context view` dropped the
// environment/identity/mode map that is the whole point of the command. Keys
// are sorted for stable output; every value passes the same redaction layers as
// the JSON path (key heuristics + string scrub), and non-scalar values render
// as compact redacted JSON.
func (h *HumanUX) renderResultFields(fields map[string]any) {
	if len(fields) == 0 {
		return
	}
	keys := make([]string, 0, len(fields))
	for k := range fields {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	keyStyle := lipgloss.NewStyle().Foreground(h.theme.Muted)
	for _, k := range keys {
		// UX-23: suppress empty/nil field lines (nil, "", empty slice/map) — they
		// are noise a human doesn't need ("active_context:", "migrated_contexts:
		// null"). The full machine envelope (agent mode) still carries them for
		// programmatic branching; this is a human-render polish only.
		if isEmptyFieldValue(fields[k]) {
			continue
		}
		fmt.Fprintln(os.Stdout, "  "+keyStyle.Render(k+":")+" "+h.fieldValue(k, fields[k]))
	}
}

// isEmptyFieldValue reports whether a Result.Field value carries no information
// worth a human-facing line: a nil, an empty string, or an empty slice/map
// (UX-23). Scalars (bool/number), including a false bool, are NOT empty — false
// is meaningful (e.g. purged_legacy: false is worth showing only when a
// migration ran, which the caller already gates; here we only drop truly empty
// values).
func isEmptyFieldValue(v any) bool {
	switch tv := v.(type) {
	case nil:
		return true
	case string:
		return tv == ""
	}
	rv := reflect.ValueOf(v)
	switch rv.Kind() {
	case reflect.Slice, reflect.Map, reflect.Array:
		return rv.Len() == 0
	case reflect.Pointer, reflect.Interface:
		return rv.IsNil()
	}
	return false
}

// fieldValue formats one field value for human line rendering: scalars as-is,
// nested values as compact redacted JSON, secret-shaped keys fully masked, and
// every string through the same scrub the JSON path applies.
func (h *HumanUX) fieldValue(key string, v any) string {
	if isSensitiveKey(key) {
		return "[REDACTED]"
	}
	var val string
	switch tv := v.(type) {
	case nil:
		val = "-"
	case string:
		val = redactString(tv)
	case bool, int, int32, int64, float32, float64:
		val = fmt.Sprintf("%v", tv)
	default:
		val = string(safeMarshal(tv)) // compact, redacted JSON for nested values
	}
	return strings.TrimSpace(val)
}

// ReportError writes a redacted, styled error line (and optional next step) to stderr.
func (h *HumanUX) ReportError(err error, step string) {
	// Errors MUST go to stderr: stdout is reserved for Render payloads (a single
	// stray byte corrupts a downstream parser). Redact the error path too (M6):
	// error strings routinely echo backend bodies and callers capture 2>&1.
	var coded *CodedError
	if errors.As(err, &coded) {
		if step == "" {
			step = coded.Actionable
		}
		coded.MarkReported()
	}
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
