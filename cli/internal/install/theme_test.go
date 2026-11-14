package install

import (
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// FormTheme must use the radio selector glyphs everywhere a selection is shown,
// and the brand-coloured prompt glyph for text inputs, so every form looks the
// same. Rendering the style emits the SetString glyph, which we assert on.
func TestFormThemeUsesRadioGlyphs(t *testing.T) {
	ft := prompt.FormTheme()
	cases := map[string]struct {
		got  string
		want string
	}{
		"select selector":   {ft.Focused.SelectSelector.Render(), theme.SelectOn},
		"selected prefix":   {ft.Focused.SelectedPrefix.Render(), theme.SelectOn},
		"unselected prefix": {ft.Focused.UnselectedPrefix.Render(), theme.SelectOff},
	}
	for name, c := range cases {
		if !strings.Contains(c.got, c.want) {
			t.Errorf("%s = %q, want it to contain %q", name, c.got, c.want)
		}
	}
}

// PromptGlyph (used by Input()) must be the filled radio ring so a text field's
// prompt matches the select selector.
func TestPromptGlyphIsRadioRing(t *testing.T) {
	if !strings.Contains(prompt.PromptGlyph, theme.SelectOn) {
		t.Errorf("PromptGlyph = %q, want it to contain %q", prompt.PromptGlyph, theme.SelectOn)
	}
}

// Confirm buttons must be repainted with Jentic brand colours rather than huh's
// default fuchsia focused button: the focused (selected) button uses the brand
// green background and the blurred button the muted grey-teal foreground.
func TestFormThemeBrandsConfirmButtons(t *testing.T) {
	ft := prompt.FormTheme()
	if got := ft.Focused.FocusedButton.GetBackground(); got != theme.Green {
		t.Errorf("focused confirm button background = %v, want brand green %v", got, theme.Green)
	}
	if got := ft.Focused.BlurredButton.GetForeground(); got != theme.Muted {
		t.Errorf("blurred confirm button foreground = %v, want muted %v", got, theme.Muted)
	}
}
