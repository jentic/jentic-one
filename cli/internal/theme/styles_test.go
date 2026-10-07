package theme

import (
	"os"
	"strings"
	"testing"

	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
)

// forceColor pins lipgloss's global renderer to TrueColor for the duration of a
// test (and restores it afterward) so Style.Render emits ANSI escapes even when
// the test binary's stdout is NOT a TTY. Without this, lipgloss detects the
// Ascii profile in CI and strips all colour, collapsing dark/light renders to
// the same plain text — which would mask the divergence the palette exists to
// produce.
func forceColor(t *testing.T) {
	t.Helper()
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })
}

// TestDarkStylesByteIdenticalToFixed pins the invariant that makes the STEP-1
// alias safe: the dark palette's Styles() must render every semantic role
// exactly as the historical FIXED brand styles did (bold/plain + brand hex), so
// dark output is byte-for-byte unchanged after retiring the package-level vars.
// The "expected" side reconstructs each fixed style inline from the brand tokens
// (the pre-change definitions in theme.go) rather than reading the retired vars,
// so the test still guards the mapping even though those vars now delegate.
func TestDarkStylesByteIdenticalToFixed(t *testing.T) {
	forceColor(t)
	st := Themes["dark"].Styles()
	const sample = "x"

	cases := []struct {
		role string
		got  string
		want string
	}{
		{"Heading", st.Heading.Render(sample), lipgloss.NewStyle().Bold(true).Foreground(Brand).Render(sample)},
		{"Step", st.Step.Render(sample), lipgloss.NewStyle().Bold(true).Foreground(Yellow).Render(sample)},
		{"Command", st.Command.Render(sample), lipgloss.NewStyle().Foreground(Green).Render(sample)},
		{"Dim", st.Dim.Render(sample), lipgloss.NewStyle().Foreground(Muted).Render(sample)},
		{"Success", st.Success.Render(sample), lipgloss.NewStyle().Bold(true).Foreground(Green).Render(sample)},
		{"Warn", st.Warn.Render(sample), lipgloss.NewStyle().Foreground(Orange).Render(sample)},
		{"Error", st.Error.Render(sample), lipgloss.NewStyle().Bold(true).Foreground(Red).Render(sample)},
		{"Info", st.Info.Render(sample), lipgloss.NewStyle().Foreground(Blue).Render(sample)},
		{"Accent", st.Accent.Render(sample), lipgloss.NewStyle().Foreground(Yellow).Render(sample)},
	}
	for _, c := range cases {
		if c.got != c.want {
			t.Errorf("dark %s.Render(%q) = %q, want byte-identical to fixed %q", c.role, sample, c.got, c.want)
		}
	}

	// Field: muted "%-9s " label + white value, exactly as the fixed helper.
	wantField := lipgloss.NewStyle().Foreground(Muted).Render("name:     ") +
		lipgloss.NewStyle().Foreground(White).Render("work")
	if got := st.Field("name", "work"); got != wantField {
		t.Errorf("dark Field = %q, want byte-identical to fixed %q", got, wantField)
	}
}

// TestRetiredVarsAliasDark proves the package-level vars/helpers now retired to
// the dark palette render identically to Themes["dark"].Styles() — i.e. every
// un-migrated call site (theme.Heading, theme.Successf, theme.Field, …) still
// emits the dark output.
func TestRetiredVarsAliasDark(t *testing.T) {
	forceColor(t)
	st := Themes["dark"].Styles()
	const s = "x"
	if Heading.Render(s) != st.Heading.Render(s) {
		t.Error("theme.Heading var did not alias dark Styles().Heading")
	}
	if Step.Render(s) != st.Step.Render(s) {
		t.Error("theme.Step var did not alias dark Styles().Step")
	}
	if Command.Render(s) != st.Command.Render(s) {
		t.Error("theme.Command var did not alias dark Styles().Command")
	}
	if Dim.Render(s) != st.Dim.Render(s) {
		t.Error("theme.Dim var did not alias dark Styles().Dim")
	}
	if Success.Render(s) != st.Success.Render(s) {
		t.Error("theme.Success var did not alias dark Styles().Success")
	}
	if Warn.Render(s) != st.Warn.Render(s) {
		t.Error("theme.Warn var did not alias dark Styles().Warn")
	}
	if Error.Render(s) != st.Error.Render(s) {
		t.Error("theme.Error var did not alias dark Styles().Error")
	}
	if Info.Render(s) != st.Info.Render(s) {
		t.Error("theme.Info var did not alias dark Styles().Info")
	}
	if Accent.Render(s) != st.Accent.Render(s) {
		t.Error("theme.Accent var did not alias dark Styles().Accent")
	}
	if Successf("%s", s) != st.Successf("%s", s) ||
		Warnf("%s", s) != st.Warnf("%s", s) ||
		Infof("%s", s) != st.Infof("%s", s) ||
		Dimf("%s", s) != st.Dimf("%s", s) ||
		Headingf("%s", s) != st.Headingf("%s", s) {
		t.Error("retired *f helpers did not alias dark Styles() equivalents")
	}
	if Field("k", "v") != st.Field("k", "v") {
		t.Error("theme.Field did not alias dark Styles().Field")
	}
}

// TestDotGlyphsAliasFixedAndDiverge pins the palette-bound status glyphs added
// for status/doctor: on dark they must render byte-identical to the historical
// fixed cmdcore.Dot* helpers (Success/Warn/Muted/Error + the ●/○/✗ runes), and
// on light they must re-tint like every other role.
func TestDotGlyphsAliasFixedAndDiverge(t *testing.T) {
	forceColor(t)
	dark := Themes["dark"].Styles()
	light := Themes["light"].Styles()

	cases := []struct {
		role string
		got  string
		want string
		lite string
	}{
		{"DotOK", dark.DotOK(), lipgloss.NewStyle().Bold(true).Foreground(Green).Render("●"), light.DotOK()},
		{"DotWarn", dark.DotWarn(), lipgloss.NewStyle().Foreground(Orange).Render("●"), light.DotWarn()},
		{"DotDown", dark.DotDown(), lipgloss.NewStyle().Foreground(Muted).Render("○"), light.DotDown()},
		{"DotFail", dark.DotFail(), lipgloss.NewStyle().Bold(true).Foreground(Red).Render("✗"), light.DotFail()},
	}
	for _, c := range cases {
		if c.got != c.want {
			t.Errorf("dark %s = %q, want byte-identical to fixed %q", c.role, c.got, c.want)
		}
		if c.got == c.lite {
			t.Errorf("light %s must differ from dark, both rendered %q", c.role, c.got)
		}
	}
}

// TestVersionPanelForDarkMatchesFixed pins that the palette-bound VersionPanelFor
// reproduces the historical fixed VersionPanel byte-for-byte on dark, so the
// branded header is unchanged there while still re-tinting under light.
func TestVersionPanelForDarkMatchesFixed(t *testing.T) {
	forceColor(t)
	dark := Themes["dark"].Styles()

	got := VersionPanelFor(dark, "1.2.3", "4.5.6", true)
	want := VersionPanel("1.2.3", "4.5.6", true)
	if len(got) != 1 || len(want) != 1 || got[0] != want[0] {
		t.Errorf("VersionPanelFor(dark) = %q, want byte-identical to fixed VersionPanel %q", got, want)
	}

	light := VersionPanelFor(Themes["light"].Styles(), "1.2.3", "4.5.6", true)
	if light[0] == want[0] {
		t.Errorf("VersionPanelFor(light) should differ from the dark panel, both rendered %q", light[0])
	}
}

// each role must render DIFFERENTLY from dark (different foreground hex), which
// is the whole point of the palette seam.
func TestLightStylesDivergeFromDark(t *testing.T) {
	forceColor(t)
	dark := Themes["dark"].Styles()
	light := Themes["light"].Styles()
	const s = "x"
	roles := []struct {
		name string
		d, l string
	}{
		{"Heading", dark.Heading.Render(s), light.Heading.Render(s)},
		{"Step", dark.Step.Render(s), light.Step.Render(s)},
		{"Command", dark.Command.Render(s), light.Command.Render(s)},
		{"Dim", dark.Dim.Render(s), light.Dim.Render(s)},
		{"Success", dark.Success.Render(s), light.Success.Render(s)},
		{"Warn", dark.Warn.Render(s), light.Warn.Render(s)},
		{"Error", dark.Error.Render(s), light.Error.Render(s)},
		{"Info", dark.Info.Render(s), light.Info.Render(s)},
		{"Accent", dark.Accent.Render(s), light.Accent.Render(s)},
	}
	for _, r := range roles {
		if r.d == r.l {
			t.Errorf("light %s must differ from dark, both rendered %q", r.name, r.d)
		}
	}
}

// TestLogoForDarkMatchesFixedGradient pins that LogoFor("dark") reproduces the
// historical fixed 6-row gradient (Blue, Green, Brand, Yellow, Orange, Pink) —
// the invariant that keeps the wordmark byte-identical now that Logo() delegates
// to LogoFor("dark"). The expected side re-implements the fixed rendering inline
// from the brand tokens rather than calling Logo(), so it guards the gradient
// independently of the (now-aliased) Logo().
func TestLogoForDarkMatchesFixedGradient(t *testing.T) {
	forceColor(t)
	fixedColors := []lipgloss.Color{Blue, Green, Brand, Yellow, Orange, Pink}
	var b strings.Builder
	for i, ln := range logoLines {
		c := fixedColors[i%len(fixedColors)]
		b.WriteString(lipgloss.NewStyle().Foreground(c).Bold(true).Render(ln) + "\n")
	}
	want := b.String()
	if got := LogoFor("dark"); got != want {
		t.Errorf("LogoFor(dark) = %q, want fixed gradient %q", got, want)
	}
	// Light must re-tint the wordmark (different from dark).
	if LogoFor("light") == want {
		t.Error("LogoFor(light) should differ from the dark gradient")
	}
}

// TestDefaultThemeNameAutoDetect table-tests the final ladder rung: a light
// terminal background (COLORFGBG bg 15/7) resolves to "light"; dark/unset to
// "dark". It overrides the detectLightBackground package var so the test does
// not depend on the ambient COLORFGBG.
func TestDefaultThemeNameAutoDetect(t *testing.T) {
	prev := detectLightBackground
	t.Cleanup(func() { detectLightBackground = prev })

	cases := []struct {
		name  string
		light bool
		want  string
	}{
		{"light background", true, "light"},
		{"dark background", false, "dark"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			detectLightBackground = func() bool { return c.light }
			if got := defaultThemeName(); got != c.want {
				t.Errorf("defaultThemeName() = %q, want %q", got, c.want)
			}
		})
	}
}

// TestDetectLightBackgroundFromCOLORFGBG exercises the real COLORFGBG parser
// (not the override) so the actual bg-index classification is covered.
func TestDetectLightBackgroundFromCOLORFGBG(t *testing.T) {
	cases := []struct {
		env  string
		want bool
	}{
		{"", false},
		{"15;0", false}, // fg 15, bg 0 -> dark
		{"0;15", true},  // bg 15 -> light
		{"0;7", true},   // bg 7 -> light
		{"15;default;0", false},
		{"15;default;15", true},
	}
	for _, c := range cases {
		t.Run(c.env, func(t *testing.T) {
			t.Setenv("COLORFGBG", c.env)
			if c.env == "" {
				os.Unsetenv("COLORFGBG")
			}
			if got := detectLightBackground(); got != c.want {
				t.Errorf("detectLightBackground(COLORFGBG=%q) = %v, want %v", c.env, got, c.want)
			}
		})
	}
}

// TestExplicitThemeBeatsAutoDetect pins that an explicit --theme (or config)
// choice wins over COLORFGBG auto-detection: ResolveThemeWithName("dark", "")
// must resolve to dark even on a light-background terminal, and the resolved
// name must be reported for the logo gradient. Mirrors resolve_test.go's harness
// (forceTTY + NO_COLOR/JENTIC_THEME hygiene) so the human ladder is exercised
// deterministically.
func TestExplicitThemeBeatsAutoDetect(t *testing.T) {
	forceTTY(t, true)
	t.Setenv("NO_COLOR", "")
	os.Unsetenv("NO_COLOR")
	t.Setenv("JENTIC_THEME", "")

	prev := detectLightBackground
	t.Cleanup(func() { detectLightBackground = prev })
	detectLightBackground = func() bool { return true } // pretend a light terminal

	// Explicit --theme dark beats the light auto-detect.
	p, name := ResolveThemeWithName("dark", "")
	if name != "dark" || p.Primary != Themes["dark"].Primary {
		t.Errorf("--theme dark did not beat light auto-detect: name=%q", name)
	}

	// With nothing explicit, auto-detect wins -> light.
	_, name = ResolveThemeWithName("", "")
	if name != "light" {
		t.Errorf("auto-detect should resolve light background to %q, got %q", "light", name)
	}
}
