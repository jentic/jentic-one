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

// forceTTY makes ResolveThemeWithName's stdout-TTY probe return want for the
// duration of the test, so the human colour ladder can be exercised
// deterministically off a real terminal (the ladder assumes an interactive
// stdout; the non-TTY branch is covered separately by
// TestResolveThemeWithName_NonTTYForcesNoColor).
func forceTTY(t *testing.T, want bool) {
	t.Helper()
	prev := stdoutIsTTY
	stdoutIsTTY = func() bool { return want }
	t.Cleanup(func() { stdoutIsTTY = prev })
}

func TestResolveThemeWithName_Ladder(t *testing.T) {
	forceTTY(t, true)
	// Ensure NO_COLOR is absent (present-but-empty still counts as present).
	t.Setenv("NO_COLOR", "")
	os.Unsetenv("NO_COLOR")
	t.Setenv("JENTIC_THEME", "")

	t.Run("flag override wins", func(t *testing.T) {
		got, name := ResolveThemeWithName("light", "dark")
		if name != "light" || got.Primary != Themes["light"].Primary {
			t.Errorf("--theme light did not win (name=%q)", name)
		}
	})

	t.Run("JENTIC_THEME beats config", func(t *testing.T) {
		t.Setenv("JENTIC_THEME", "light")
		got, name := ResolveThemeWithName("", "dark")
		if name != "light" || got.Primary != Themes["light"].Primary {
			t.Errorf("JENTIC_THEME=light did not beat config dark (name=%q)", name)
		}
	})

	t.Run("config theme used when no flag/env", func(t *testing.T) {
		t.Setenv("JENTIC_THEME", "")
		got, name := ResolveThemeWithName("", "light")
		if name != "light" || got.Primary != Themes["light"].Primary {
			t.Errorf("config theme light not used (name=%q)", name)
		}
	})

	t.Run("default dark", func(t *testing.T) {
		t.Setenv("JENTIC_THEME", "")
		got, name := ResolveThemeWithName("", "")
		if name != "dark" || got.Primary != Themes["dark"].Primary {
			t.Errorf("expected dark fallback (name=%q)", name)
		}
	})

	t.Run("unknown name falls back to dark", func(t *testing.T) {
		got, name := ResolveThemeWithName("chartreuse", "")
		if name != "dark" || got.Primary != Themes["dark"].Primary {
			t.Errorf("unknown theme did not fall back to dark (name=%q)", name)
		}
	})
}

func TestResolveThemeWithName_NoColorEnv(t *testing.T) {
	forceTTY(t, true) // isolate the NO_COLOR decision from the non-TTY branch
	t.Setenv("NO_COLOR", "1")
	t.Setenv("JENTIC_THEME", "dark")

	// NO_COLOR present + no explicit --theme => no-color, beating JENTIC_THEME/config.
	got, name := ResolveThemeWithName("", "dark")
	if _, ok := got.Primary.(lipgloss.NoColor); !ok || name != "no-color" {
		t.Errorf("NO_COLOR should force no-color when no --theme is given (name=%q)", name)
	}
	// But an explicit --theme overrides NO_COLOR (per no-color.org: user asked).
	got, name = ResolveThemeWithName("light", "dark")
	if _, ok := got.Primary.(lipgloss.NoColor); ok || name != "light" {
		t.Errorf("explicit --theme light should override NO_COLOR (name=%q)", name)
	}
}

// TestResolveThemeWithName_NonTTYForcesNoColor pins OPS-1: when stdout is NOT a
// terminal (piped/redirected human run) and no --theme was given,
// ResolveThemeWithName degrades to no-color so ANSI never leaks into a consumer
// — but an explicit --theme still wins, because that is the operator
// deliberately asking for colour.
func TestResolveThemeWithName_NonTTYForcesNoColor(t *testing.T) {
	forceTTY(t, false)
	t.Setenv("NO_COLOR", "")
	os.Unsetenv("NO_COLOR")
	t.Setenv("JENTIC_THEME", "dark") // would pick dark on a TTY

	got, name := ResolveThemeWithName("", "dark")
	if _, ok := got.Primary.(lipgloss.NoColor); !ok || name != "no-color" {
		t.Errorf("non-TTY stdout should force no-color when no --theme is given (name=%q)", name)
	}
	got, name = ResolveThemeWithName("light", "dark")
	if _, ok := got.Primary.(lipgloss.NoColor); ok || name != "light" {
		t.Errorf("explicit --theme light should override non-TTY auto no-color (name=%q)", name)
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
