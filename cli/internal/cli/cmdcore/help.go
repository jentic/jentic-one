package cmdcore

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

// cmdColors is cycled across the command list so each entry pops.
var cmdColors = []lipgloss.Color{theme.Green, theme.Blue, theme.Orange, theme.Pink, theme.Yellow, theme.Brand}

var (
	taglineStyle      = lipgloss.NewStyle().Foreground(theme.Muted).Italic(true)
	headingStyle      = theme.Heading
	sectionStyle      = lipgloss.NewStyle().Foreground(theme.Muted).Bold(true)
	groupHeadingStyle = lipgloss.NewStyle().Foreground(theme.Muted).Italic(true)
	descStyle         = lipgloss.NewStyle().Foreground(theme.White)
	usageStyle        = theme.Command
	flagStyle         = theme.Info
	mutedStyle        = theme.Dim
	accentStyle       = theme.Accent
)

// helpFunc is the colourful replacement for cobra's default help renderer. It
// is installed on the root command and propagates to every subcommand. It is a
// method on App so the root screen can resolve config and probe the server for
// its version.
func (a *App) helpFunc(cmd *cobra.Command, _ []string) {
	var b strings.Builder

	// The brand header (logo + version panel) sits at the top of every help
	// screen — root and subcommands alike — so the mark is present everywhere.
	b.WriteString(a.rootHeader())
	if !cmd.HasParent() {
		tagline := cmd.Annotations["tagline"]
		if tagline == "" {
			tagline = "install, manage, and run the Jentic platform"
		}
		b.WriteString(taglineStyle.Render("  " + tagline))
		b.WriteString("\n\n")
	} else {
		b.WriteString("\n")
		b.WriteString(headingStyle.Render(cmd.CommandPath()))
		b.WriteString("\n\n")
	}

	desc := cmd.Long
	if desc == "" {
		desc = cmd.Short
	}
	if desc != "" {
		for _, ln := range strings.Split(desc, "\n") {
			b.WriteString(descStyle.Render(ln) + "\n")
		}
		b.WriteString("\n")
	}

	b.WriteString(sectionStyle.Render("USAGE"))
	b.WriteString("\n  " + usageStyle.Render(cmd.UseLine()) + "\n")
	if cmd.HasAvailableSubCommands() {
		b.WriteString("  " + usageStyle.Render(cmd.CommandPath()+" [command]") + "\n")
	}
	b.WriteString("\n")

	if cmd.HasAvailableSubCommands() {
		b.WriteString(sectionStyle.Render("COMMANDS"))
		b.WriteString("\n")
		writeCommands(&b, cmd)
		b.WriteString("\n")
	}

	if local := renderFlags(cmd.LocalFlags()); local != "" {
		b.WriteString(sectionStyle.Render("FLAGS"))
		b.WriteString("\n" + local + "\n")
	}
	if inherited := renderFlags(cmd.InheritedFlags()); inherited != "" {
		b.WriteString(sectionStyle.Render("GLOBAL FLAGS"))
		b.WriteString("\n" + inherited + "\n")
	}

	if cmd.HasAvailableSubCommands() {
		hint := accentStyle.Render(cmd.CommandPath() + " [command] --help")
		b.WriteString(mutedStyle.Render("Run ") + hint + mutedStyle.Render(" for more about a command.") + "\n")
	}

	fmt.Fprint(cmd.OutOrStdout(), b.String())
}

// writeCommands renders cmd's subcommands grouped by their cobra group. Each
// group prints its title as a sub-heading followed by its commands; any command
// without a (known) group — including the built-in help/completion — falls
// under "Additional commands". Color cycling and column alignment run across
// every row so the list reads as one continuous, aligned block.
func writeCommands(b *strings.Builder, cmd *cobra.Command) {
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
		b.WriteString("    " + name + pad + mutedStyle.Render(c.Short) + "\n")
	}

	grouped := map[string]bool{}
	first := true
	writeSection := func(title string, rows []*cobra.Command) {
		if !first {
			b.WriteString("\n")
		}
		first = false
		b.WriteString("  " + groupHeadingStyle.Render(strings.TrimRight(title, ":")) + "\n")
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
func (a *App) rootHeader() string {
	return a.BrandHeader("", version)
}

// renderFlags formats a flag set into aligned, coloured rows. Returns "" when
// the set has no visible flags.
func renderFlags(fs *pflag.FlagSet) string {
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
		b.WriteString("  " + flagStyle.Render(r.left) + pad + mutedStyle.Render(r.right) + "\n")
	}
	return b.String()
}
