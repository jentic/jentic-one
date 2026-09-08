package theme

import (
	"context"
	"os"
	"testing"

	"github.com/charmbracelet/lipgloss"
)

func TestThemesRegistryClosedSet(t *testing.T) {
	for _, name := range []string{"dark", "light", "no-color"} {
		if _, ok := Themes[name]; !ok {
			t.Errorf("Themes missing %q", name)
		}
	}
	// no-color must zero every slot with NoColor{} so no ANSI escapes leak.
	nc := Themes["no-color"]
	for label, c := range map[string]lipgloss.TerminalColor{
		"Primary": nc.Primary, "Secondary": nc.Secondary, "Error": nc.Error,
		"Success": nc.Success, "Warning": nc.Warning, "Muted": nc.Muted,
	} {
		if _, ok := c.(lipgloss.NoColor); !ok {
			t.Errorf("no-color.%s = %T, want lipgloss.NoColor", label, c)
		}
	}
}

// forceTTY makes ResolveTheme's stdout-TTY probe return want for the duration of
// the test, so the human colour ladder can be exercised deterministically off a
// real terminal (the ladder assumes an interactive stdout; the non-TTY branch is
// covered separately by TestResolveTheme_NonTTYForcesNoColor).
func forceTTY(t *testing.T, want bool) {
	t.Helper()
	prev := stdoutIsTTY
	stdoutIsTTY = func() bool { return want }
	t.Cleanup(func() { stdoutIsTTY = prev })
}

func TestResolveTheme_Ladder(t *testing.T) {
	forceTTY(t, true)
	// Ensure NO_COLOR is absent (present-but-empty still counts as present).
	t.Setenv("NO_COLOR", "")
	os.Unsetenv("NO_COLOR")
	t.Setenv("JENTIC_THEME", "")

	t.Run("flag override wins", func(t *testing.T) {
		got := ResolveTheme("light", "dark")
		if got.Primary != Themes["light"].Primary {
			t.Error("--theme light did not win")
		}
	})

	t.Run("JENTIC_THEME beats config", func(t *testing.T) {
		t.Setenv("JENTIC_THEME", "light")
		got := ResolveTheme("", "dark")
		if got.Primary != Themes["light"].Primary {
			t.Error("JENTIC_THEME=light did not beat config dark")
		}
	})

	t.Run("config theme used when no flag/env", func(t *testing.T) {
		t.Setenv("JENTIC_THEME", "")
		got := ResolveTheme("", "light")
		if got.Primary != Themes["light"].Primary {
			t.Error("config theme light not used")
		}
	})

	t.Run("default dark", func(t *testing.T) {
		t.Setenv("JENTIC_THEME", "")
		got := ResolveTheme("", "")
		if got.Primary != Themes["dark"].Primary {
			t.Error("expected dark fallback")
		}
	})

	t.Run("unknown name falls back to dark", func(t *testing.T) {
		got := ResolveTheme("chartreuse", "")
		if got.Primary != Themes["dark"].Primary {
			t.Error("unknown theme did not fall back to dark")
		}
	})
}

func TestResolveTheme_NoColorEnv(t *testing.T) {
	forceTTY(t, true) // isolate the NO_COLOR decision from the non-TTY branch
	t.Setenv("NO_COLOR", "1")
	t.Setenv("JENTIC_THEME", "dark")

	// NO_COLOR present + no explicit --theme => no-color, beating JENTIC_THEME/config.
	if _, ok := ResolveTheme("", "dark").Primary.(lipgloss.NoColor); !ok {
		t.Error("NO_COLOR should force no-color when no --theme is given")
	}
	// But an explicit --theme overrides NO_COLOR (per no-color.org: user asked).
	if _, ok := ResolveTheme("light", "dark").Primary.(lipgloss.NoColor); ok {
		t.Error("explicit --theme light should override NO_COLOR")
	}
}

// TestResolveTheme_NonTTYForcesNoColor pins OPS-1: when stdout is NOT a terminal
// (piped/redirected human run) and no --theme was given, ResolveTheme degrades to
// no-color so ANSI never leaks into a consumer — but an explicit --theme still
// wins, because that is the operator deliberately asking for colour.
func TestResolveTheme_NonTTYForcesNoColor(t *testing.T) {
	forceTTY(t, false)
	t.Setenv("NO_COLOR", "")
	os.Unsetenv("NO_COLOR")
	t.Setenv("JENTIC_THEME", "dark") // would pick dark on a TTY

	if _, ok := ResolveTheme("", "dark").Primary.(lipgloss.NoColor); !ok {
		t.Error("non-TTY stdout should force no-color when no --theme is given")
	}
	if _, ok := ResolveTheme("light", "dark").Primary.(lipgloss.NoColor); ok {
		t.Error("explicit --theme light should override non-TTY auto no-color")
	}
}

func TestPaletteContextRoundTrip(t *testing.T) {
	ctx := WithContext(context.Background(), Themes["light"])
	if FromContext(ctx).Primary != Themes["light"].Primary {
		t.Error("palette did not round-trip through context")
	}
	// Missing palette falls back to dark.
	if FromContext(context.Background()).Primary != Themes["dark"].Primary {
		t.Error("empty context should return dark default")
	}
}
