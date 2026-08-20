package cmdcore

import (
	"bytes"
	"context"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/muesli/termenv"
	"github.com/spf13/cobra"
)

// renderHelp runs the custom helpFunc for cmd and returns the plain text (styles
// still emit their content; NO_COLOR keeps it free of ANSI in CI). The command's
// output is redirected to a buffer so the renderer's fmt.Fprint lands there.
func renderHelp(t *testing.T, cmd *cobra.Command) string {
	t.Helper()
	t.Setenv("NO_COLOR", "1")
	var buf bytes.Buffer
	cmd.SetOut(&buf)
	testApp(t).helpFunc(cmd, nil)
	return buf.String()
}

// TestHelpRendersExamples pins UX-1: a command with cmd.Example must show an
// EXAMPLES section listing each example line. The custom renderer previously
// dropped Example entirely.
func TestHelpRendersExamples(t *testing.T) {
	cmd := &cobra.Command{
		Use:     "widget",
		Short:   "manage widgets",
		Example: "  jentic widget list\n  jentic widget get abc --json",
	}
	out := renderHelp(t, cmd)
	if !strings.Contains(out, "EXAMPLES") {
		t.Fatalf("help missing EXAMPLES section:\n%s", out)
	}
	if !strings.Contains(out, "jentic widget list") || !strings.Contains(out, "jentic widget get abc --json") {
		t.Errorf("EXAMPLES did not render both example lines:\n%s", out)
	}
}

// TestHelpOmitsExamplesWhenNone: a command with no Example must NOT emit an empty
// EXAMPLES heading.
func TestHelpOmitsExamplesWhenNone(t *testing.T) {
	cmd := &cobra.Command{Use: "plain", Short: "no examples here"}
	if out := renderHelp(t, cmd); strings.Contains(out, "EXAMPLES") {
		t.Errorf("help should not render EXAMPLES for a command with no Example:\n%s", out)
	}
}

// TestHelpReTintsForTheme is the end-to-end acceptance for the theme/light-mode
// P0: the help renderer draws through the palette carried in the command
// context, so `--theme dark` and `--theme light` produce DIFFERENT bytes. It
// forces the lipgloss colour profile (a test binary has no TTY, so colour would
// otherwise be stripped and both renders would collapse to identical plain
// text) and asserts divergence — proving the interceptor→context→helpStyles
// palette wiring is live, not just the pure Styles() unit test.
func TestHelpReTintsForTheme(t *testing.T) {
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })

	cmd := &cobra.Command{Use: "widget", Short: "manage widgets"}

	render := func(themeName string) string {
		var buf bytes.Buffer
		c := &cobra.Command{Use: cmd.Use, Short: cmd.Short}
		c.SetOut(&buf)
		ctx := theme.WithContext(context.Background(), theme.Themes[themeName])
		ctx = theme.WithThemeName(ctx, themeName)
		c.SetContext(ctx)
		testApp(t).helpFunc(c, nil)
		return buf.String()
	}

	dark := render("dark")
	light := render("light")
	if dark == light {
		t.Fatalf("help output did not re-tint between dark and light (palette not threaded through the help renderer)")
	}
	if !strings.Contains(dark, "\x1b[") || !strings.Contains(light, "\x1b[") {
		t.Fatalf("expected ANSI colour in both renders under a forced TrueColor profile")
	}
}

// TestHelpRendersUsageAndFlags: the core sections (USAGE, FLAGS) render, and a
// declared flag with its usage text appears.
func TestHelpRendersUsageAndFlags(t *testing.T) {
	cmd := &cobra.Command{Use: "thing", Short: "do a thing"}
	cmd.Flags().String("target", "", "where to aim")
	out := renderHelp(t, cmd)
	for _, want := range []string{"USAGE", "FLAGS", "--target", "where to aim"} {
		if !strings.Contains(out, want) {
			t.Errorf("help missing %q:\n%s", want, out)
		}
	}
}

// TestHelpRendersCommandsGrouped: a parent with subcommands renders a COMMANDS
// section listing each visible child, and the "for more about a command" hint.
func TestHelpRendersCommands(t *testing.T) {
	parent := &cobra.Command{Use: "parent", Short: "a parent"}
	parent.AddCommand(&cobra.Command{Use: "alpha", Short: "the alpha child", Run: func(*cobra.Command, []string) {}})
	parent.AddCommand(&cobra.Command{Use: "beta", Short: "the beta child", Run: func(*cobra.Command, []string) {}})
	out := renderHelp(t, parent)
	for _, want := range []string{"COMMANDS", "alpha", "beta", "the alpha child", "--help"} {
		if !strings.Contains(out, want) {
			t.Errorf("help missing %q:\n%s", want, out)
		}
	}
}

// TestProbeServerUsesSeam pins QA-4: the interactive header's server-version
// probe goes through the ProbeServer seam, so it can be redirected away from the
// network entirely. Without the seam the only bound was serverinfo.DefaultTimeout
// on a real dial; with it, a caller (and this test) can guarantee no dial at all.
func TestProbeServerUsesSeam(t *testing.T) {
	app := testApp(t)
	called := false
	app.ProbeServer = func(string) serverinfo.Info {
		called = true
		return serverinfo.Info{Running: true, Version: "seam-version"}
	}
	got := app.probeServer("http://unused.example")
	if !called {
		t.Fatal("probeServer did not use the injected ProbeServer seam")
	}
	if got.Version != "seam-version" {
		t.Errorf("probeServer returned %+v, want the seam's Info", got)
	}
}

// TestBrandHeaderNeverBlocksOffline proves the header path completes fast even
// when the configured base URL is unreachable (QA-4): with the probe behind a
// seam we can inject an instant "offline" result, so rendering the branded help
// header can never hang on a dead control plane. The real probe is additionally
// bounded by serverinfo.DefaultTimeout (400ms), asserted in serverinfo's own
// tests; here we assert the seam short-circuits it to ~instant.
func TestBrandHeaderNeverBlocksOffline(t *testing.T) {
	app := testApp(t)
	app.ProbeServer = func(string) serverinfo.Info { return serverinfo.Info{Running: false} }
	done := make(chan struct{})
	go func() {
		_ = app.BrandHeader(context.Background(), "http://10.255.255.1:1/unreachable", "v-test")
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("BrandHeader blocked for >2s with an unreachable base URL — the probe seam did not short-circuit")
	}
}

// TestHelpNeverPanicsOnEmptyCommand: the renderer must tolerate a minimal command
// (no long/short/flags/examples/subcommands) without panicking and still emit
// USAGE.
func TestHelpNeverPanicsOnEmptyCommand(t *testing.T) {
	out := renderHelp(t, &cobra.Command{Use: "bare"})
	if !strings.Contains(out, "USAGE") {
		t.Errorf("even a bare command should render USAGE:\n%s", out)
	}
}
