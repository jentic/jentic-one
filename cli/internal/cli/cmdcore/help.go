package cmdcore

import (
	"context"
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

// cmdColors is cycled across the command list so each entry pops. It stays on
// the fixed brand tokens: the command-list rainbow is a brand flourish, and the
// per-theme logo gradient (LogoForContext) is the palette-aware surface here.
var cmdColors = []lipgloss.Color{theme.Green, theme.Blue, theme.Orange, theme.Pink, theme.Yellow, theme.Brand}

// helpStyles is the palette-bound set of styles the help renderer draws through.
// It is resolved per-render from the command context (theme.StylesFromContext)
// so `--theme light`/`no-color` re-tint the help screen; the muted-derived
// styles (tagline/section/group headings) are composed from the same palette
// slot the Dim role uses.
type helpStyles struct {
	tagline      lipgloss.Style
	heading      lipgloss.Style
	section      lipgloss.Style
	groupHeading lipgloss.Style
	desc         lipgloss.Style
	usage        lipgloss.Style
	flag         lipgloss.Style
	muted        lipgloss.Style
	accent       lipgloss.Style
}

// helpStylesFor builds the help style set from the palette resolved into ctx.
func helpStylesFor(ctx context.Context) helpStyles {
	p := theme.FromContext(ctx)
	st := p.Styles()
	return helpStyles{
		tagline:      lipgloss.NewStyle().Foreground(p.Muted).Italic(true),
		heading:      st.Heading,
		section:      lipgloss.NewStyle().Foreground(p.Muted).Bold(true),
		groupHeading: lipgloss.NewStyle().Foreground(p.Muted).Italic(true),
		desc:         lipgloss.NewStyle().Foreground(theme.White),
		usage:        st.Command,
		flag:         st.Info,
		muted:        st.Dim,
		accent:       st.Accent,
	}
}

// helpFunc is the colourful replacement for cobra's default help renderer. It
// is installed on the root command and propagates to every subcommand. It is a
// method on App so the root screen can resolve config and probe the server for
// its version.
func (a *App) helpFunc(cmd *cobra.Command, _ []string) {
	var b strings.Builder
	ctx := cmd.Context()
	if ctx == nil {
		ctx = context.Background()
	}
	hs := helpStylesFor(ctx)

	// The brand header (logo + version panel) sits at the top of every help
	// screen — root and subcommands alike — so the mark is present everywhere.
	b.WriteString(a.rootHeader(ctx))
	if !cmd.HasParent() {
		tagline := cmd.Annotations["tagline"]
		if tagline == "" {
			tagline = "install, manage, and run the Jentic platform"
		}
		b.WriteString(hs.tagline.Render("  " + tagline))
		b.WriteString("\n\n")
	} else {
		b.WriteString("\n")
		b.WriteString(hs.heading.Render(cmd.CommandPath()))
		b.WriteString("\n\n")
	}

	desc := cmd.Long
	if desc == "" {
		desc = cmd.Short
	}
	if desc != "" {
		for _, ln := range strings.Split(desc, "\n") {
			b.WriteString(hs.desc.Render(ln) + "\n")
		}
		b.WriteString("\n")
	}

	b.WriteString(hs.section.Render("USAGE"))
	b.WriteString("\n  " + hs.usage.Render(cmd.UseLine()) + "\n")
	if cmd.HasAvailableSubCommands() {
		b.WriteString("  " + hs.usage.Render(cmd.CommandPath()+" [command]") + "\n")
	}
	b.WriteString("\n")

	if cmd.HasAvailableSubCommands() {
		b.WriteString(hs.section.Render("COMMANDS"))
		b.WriteString("\n")
		writeCommands(&b, cmd, hs)
		b.WriteString("\n")
	}

	if local := renderFlags(cmd.LocalFlags(), hs); local != "" {
		b.WriteString(hs.section.Render("FLAGS"))
		b.WriteString("\n" + local + "\n")
	}
	if inherited := renderFlags(cmd.InheritedFlags(), hs); inherited != "" {
		b.WriteString(hs.section.Render("GLOBAL FLAGS"))
		b.WriteString("\n" + inherited + "\n")
	}

	// EXAMPLES renders cmd.Example (UX-1). Cobra's default template shows it, but
	// this custom renderer omitted it, so every command's runnable examples were
	// invisible — the single most common thing a user (or agent) wants from -h.
	if ex := strings.TrimRight(cmd.Example, "\n"); ex != "" {
		b.WriteString(hs.section.Render("EXAMPLES"))
		b.WriteString("\n")
		for _, ln := range strings.Split(ex, "\n") {
			b.WriteString(hs.usage.Render(strings.TrimRight(ln, " ")) + "\n")
		}
		b.WriteString("\n")
	}

	if cmd.HasAvailableSubCommands() {
		hint := hs.accent.Render(cmd.CommandPath() + " [command] --help")
		b.WriteString(hs.muted.Render("Run ") + hint + hs.muted.Render(" for more about a command.") + "\n")
	}

	fmt.Fprint(cmd.OutOrStdout(), b.String())
}

// writeCommands renders cmd's subcommands grouped by their cobra group. Each
// group prints its title as a sub-heading followed by its commands; any command
// without a (known) group — including the built-in help/completion — falls
// under "Additional commands". Color cycling and column alignment run across
// every row so the list reads as one continuous, aligned block.
func writeCommands(b *strings.Builder, cmd *cobra.Command, hs helpStyles) {
	cmds := cmd.Commands()

	visible := func(c *cobra.Command) bool {
		return c.IsAvailableCommand() || c.Name() == "help"
	}

	maxLen := 0
	for _, c := range cmds {
		if visible(c) && len(c.Name()) > maxLen {
			maxLen = len(c.Name())
		}
	}

	color := 0
	writeRow := func(c *cobra.Command) {
		name := lipgloss.NewStyle().Foreground(cmdColors[color%len(cmdColors)]).Bold(true).Render(c.Name())
		color++
		pad := strings.Repeat(" ", maxLen-len(c.Name())+3)
		b.WriteString("    " + name + pad + hs.muted.Render(c.Short) + "\n")
	}

	grouped := map[string]bool{}
	first := true
	writeSection := func(title string, rows []*cobra.Command) {
		if !first {
			b.WriteString("\n")
		}
		first = false
		b.WriteString("  " + hs.groupHeading.Render(strings.TrimRight(title, ":")) + "\n")
		for _, c := range rows {
			writeRow(c)
		}
	}

	for _, g := range cmd.Groups() {
		var rows []*cobra.Command
		for _, c := range cmds {
			if visible(c) && c.GroupID == g.ID {
				rows = append(rows, c)
			}
		}
		if len(rows) == 0 {
			continue
		}
		grouped[g.ID] = true
		writeSection(g.Title, rows)
	}

	var extra []*cobra.Command
	for _, c := range cmds {
		if visible(c) && !grouped[c.GroupID] {
			extra = append(extra, c)
		}
	}
	if len(extra) > 0 {
		if len(grouped) > 0 {
			writeSection("Additional commands", extra)
		} else {
			for _, c := range extra {
				writeRow(c)
			}
		}
	}
}

// rootHeader renders the wordmark with a right-aligned version panel: the CLI
// version plus the server version when one is running.
func (a *App) rootHeader(ctx context.Context) string {
	return a.BrandHeader(ctx, "", version)
}

// renderFlags formats a flag set into aligned, coloured rows. Returns "" when
// the set has no visible flags.
func renderFlags(fs *pflag.FlagSet, hs helpStyles) string {
	type row struct{ left, right string }
	var rows []row
	maxLeft := 0

	fs.VisitAll(func(f *pflag.Flag) {
		if f.Hidden {
			return
		}
		left := "    "
		if f.Shorthand != "" {
			left = "-" + f.Shorthand + ", "
		}
		left += "--" + f.Name
		typ, usage := pflag.UnquoteUsage(f)
		if typ != "" {
			left += " " + typ
		}
		if f.DefValue != "" && f.DefValue != "false" {
			usage += fmt.Sprintf(" (default %s)", f.DefValue)
		}
		if len(left) > maxLeft {
			maxLeft = len(left)
		}
		rows = append(rows, row{left, usage})
	})

	if len(rows) == 0 {
		return ""
	}

	var b strings.Builder
	for _, r := range rows {
		pad := strings.Repeat(" ", maxLeft-len(r.left)+3)
		b.WriteString("  " + hs.flag.Render(r.left) + pad + hs.muted.Render(r.right) + "\n")
	}
	return b.String()
}
