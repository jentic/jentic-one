// clidocs_markdown.go renders the CLI reference model as GitHub-flavoured
// Markdown (docs/reference/cli.md), the counterpart of the JSON the docs SPA
// serves. Both outputs come from the same CLIReference, so the Markdown can
// never say anything the cobra tree does not.
//
// The output is a pure function of the model: no maps are iterated, no
// timestamps are written, and the command order is cobra's own, so two runs
// over the same tree produce identical bytes.

package clitree

import (
	"fmt"
	"strings"
)

// MarkdownHeader opens every rendered reference; the drift test and the CI
// `cli` job both rely on the file being regenerated rather than hand-edited.
const MarkdownHeader = `# CLI reference

<!-- GENERATED FILE — do not hand-edit. Regenerate with ` + "`make cli-reference`" + `
     (cli/cmd/clidocs). Drift-gated by TestCommittedCLIMarkdownUpToDate in
     cli/pkg/clitree and by the CI cli job. -->
`

// ungroupedTitle is the heading cobra's own help uses for commands without a
// group.
const ungroupedTitle = "Additional Commands"

// maxHeadingLevel caps nesting: Markdown has no heading deeper than h6.
const maxHeadingLevel = 6

// RenderMarkdown renders the whole reference as one Markdown document.
func RenderMarkdown(ref CLIReference) []byte {
	var b strings.Builder
	b.WriteString(MarkdownHeader)
	b.WriteString("\nEvery command, flag, and example of the `jentic` and `jenticctl` binaries,\n")
	b.WriteString("generated from the cobra command tree they ship with (the same source as\n")
	b.WriteString("`--help` and the in-app docs at `/app/docs`). Hidden commands and flags are\n")
	b.WriteString("left out. For an overview of the binaries see [`cli/README.md`](../../cli/README.md).\n\n")

	for _, bin := range ref.Binaries {
		fmt.Fprintf(&b, "- [`%s`](#%s) — %s\n", bin.Name, anchor(bin.Name), inline(bin.Tagline, false))
	}
	b.WriteString("\n")

	for _, bin := range ref.Binaries {
		renderBinary(&b, bin)
	}
	return []byte(strings.TrimRight(b.String(), "\n") + "\n")
}

func renderBinary(b *strings.Builder, bin BinaryDoc) {
	fmt.Fprintf(b, "## %s\n\n", bin.Name)
	if bin.Short != "" {
		fmt.Fprintf(b, "%s\n\n", inline(bin.Short, false))
	}
	if bin.Long != "" {
		writeFence(b, "text", bin.Long)
	}

	groups := groupCommands(bin)

	b.WriteString("**Commands**\n")
	for _, g := range groups {
		fmt.Fprintf(b, "\n*%s*\n\n", inline(g.title, false))
		writeCommandTable(b, g.commands)
	}
	b.WriteString("\n")

	if len(bin.GlobalFlags) > 0 {
		fmt.Fprintf(b, "### %s global flags\n\n", bin.Name)
		b.WriteString("Accepted by every command (inherited from the root).\n\n")
		writeFlagTable(b, bin.GlobalFlags)
		b.WriteString("\n")
	}

	for _, g := range groups {
		fmt.Fprintf(b, "### %s\n\n", inline(g.title, false))
		for _, cmd := range g.commands {
			renderCommand(b, cmd, 4, bin)
		}
	}
}

type commandGroup struct {
	title    string
	commands []CommandDoc
}

// groupCommands buckets the top-level commands by group in the order the
// root declares its groups (the order of the root `--help`), keeping cobra's
// command order inside each group. Ungrouped commands come last, as in cobra.
func groupCommands(bin BinaryDoc) []commandGroup {
	groups := make([]commandGroup, 0, len(bin.GroupTitles)+1)
	for _, title := range bin.GroupTitles {
		var cmds []CommandDoc
		for _, c := range bin.Commands {
			if c.GroupTitle == title {
				cmds = append(cmds, c)
			}
		}
		if len(cmds) > 0 {
			groups = append(groups, commandGroup{title: title, commands: cmds})
		}
	}
	var rest []CommandDoc
	for _, c := range bin.Commands {
		if !containsString(bin.GroupTitles, c.GroupTitle) {
			rest = append(rest, c)
		}
	}
	if len(rest) > 0 {
		groups = append(groups, commandGroup{title: ungroupedTitle, commands: rest})
	}
	return groups
}

func renderCommand(b *strings.Builder, cmd CommandDoc, level int, bin BinaryDoc) {
	fmt.Fprintf(b, "%s %s\n\n", strings.Repeat("#", min(level, maxHeadingLevel)), cmd.Path)
	if cmd.Short != "" {
		fmt.Fprintf(b, "%s\n\n", inline(cmd.Short, false))
	}
	writeFence(b, "text", usageLine(cmd))
	if len(cmd.Aliases) > 0 {
		quoted := make([]string, len(cmd.Aliases))
		for i, a := range cmd.Aliases {
			quoted[i] = "`" + a + "`"
		}
		fmt.Fprintf(b, "Aliases: %s\n\n", strings.Join(quoted, ", "))
	}
	if cmd.Long != "" && strings.TrimSpace(cmd.Long) != strings.TrimSpace(cmd.Short) {
		writeFence(b, "text", cmd.Long)
	}

	own := ownFlags(cmd.Flags, bin.GlobalFlags)
	if len(own) > 0 {
		b.WriteString("**Flags**\n\n")
		writeFlagTable(b, own)
		b.WriteString("\n")
	}
	if len(bin.GlobalFlags) > 0 {
		fmt.Fprintf(b, "Also accepts the [%s global flags](#%s).\n\n", bin.Name, anchor(bin.Name+" global flags"))
	}

	if cmd.Example != "" {
		b.WriteString("**Examples**\n\n")
		writeFence(b, "sh", dedent(cmd.Example))
	}

	if len(cmd.Subcommands) > 0 {
		b.WriteString("**Subcommands**\n\n")
		writeCommandTable(b, cmd.Subcommands)
		b.WriteString("\n")
		for _, sub := range cmd.Subcommands {
			renderCommand(b, sub, level+1, bin)
		}
	}
}

// usageLine mirrors cobra's UseLine: the parent path, the command's Use (which
// carries the positional-argument shape), and "[flags]" when it takes any.
func usageLine(cmd CommandDoc) string {
	line := cmd.Use
	if parent, ok := strings.CutSuffix(cmd.Path, " "+cmd.Name); ok {
		line = parent + " " + cmd.Use
	}
	if len(cmd.Flags) > 0 && !strings.Contains(line, "[flags]") {
		line += " [flags]"
	}
	return line
}

// ownFlags drops the flags identical to an inherited global flag, so each
// command lists only what it adds (or overrides) on top of the root.
func ownFlags(flags, global []FlagDoc) []FlagDoc {
	out := make([]FlagDoc, 0, len(flags))
	for _, f := range flags {
		inherited := false
		for _, g := range global {
			if f == g {
				inherited = true
				break
			}
		}
		if !inherited {
			out = append(out, f)
		}
	}
	return out
}

func writeCommandTable(b *strings.Builder, cmds []CommandDoc) {
	b.WriteString("| Command | Description |\n|---|---|\n")
	for _, c := range cmds {
		fmt.Fprintf(b, "| [`%s`](#%s) | %s |\n", c.Path, anchor(c.Path), inline(c.Short, true))
	}
}

func writeFlagTable(b *strings.Builder, flags []FlagDoc) {
	b.WriteString("| Flag | Shorthand | Type | Default | Description |\n|---|---|---|---|---|\n")
	for _, f := range flags {
		short := ""
		if f.Shorthand != "" {
			short = "`-" + f.Shorthand + "`"
		}
		def := ""
		if f.Default != "" {
			def = codeSpan(f.Default, true)
		}
		fmt.Fprintf(b, "| `--%s` | %s | `%s` | %s | %s |\n",
			f.Name, short, f.Type, def, inline(f.Usage, true))
	}
}

// writeFence writes body as a fenced code block, lengthening the fence past
// any backtick run inside body so the block cannot close early.
func writeFence(b *strings.Builder, lang, body string) {
	fence := strings.Repeat("`", max(3, longestRun(body, '`')+1))
	fmt.Fprintf(b, "%s%s\n%s\n%s\n\n", fence, lang, strings.TrimRight(body, "\n"), fence)
}

// inline makes one-line help text safe as Markdown prose: newlines fold to
// spaces, and outside `code spans` the characters GitHub would read as HTML or
// emphasis are escaped. inTable additionally escapes "|" so a table cell
// cannot split.
func inline(s string, inTable bool) string {
	s = strings.Join(strings.Fields(s), " ")
	parts := strings.Split(s, "`")
	if len(parts)%2 == 0 { // unbalanced backticks: no code spans to preserve
		return escapeProse(s, inTable)
	}
	for i, p := range parts {
		if i%2 == 0 {
			parts[i] = escapeProse(p, inTable)
		} else if inTable {
			parts[i] = strings.ReplaceAll(p, "|", `\|`)
		}
	}
	return strings.Join(parts, "`")
}

var proseEscaper = strings.NewReplacer(`\`, `\\`, "<", "&lt;", ">", "&gt;", "*", `\*`, "[", `\[`, "]", `\]`, "$", `\$`)

func escapeProse(s string, inTable bool) string {
	s = proseEscaper.Replace(s)
	if inTable {
		s = strings.ReplaceAll(s, "|", `\|`)
	}
	return s
}

// codeSpan wraps s in backticks, widening the delimiter if s holds backticks.
func codeSpan(s string, inTable bool) string {
	if inTable {
		s = strings.ReplaceAll(s, "|", `\|`)
	}
	delim := strings.Repeat("`", longestRun(s, '`')+1)
	if strings.Contains(s, "`") {
		return delim + " " + s + " " + delim
	}
	return delim + s + delim
}

// anchor reproduces GitHub's heading-slug rule for the plain ASCII headings
// this renderer writes: lowercase, spaces to hyphens, other punctuation
// dropped.
func anchor(heading string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(heading) {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9', r == '-', r == '_':
			b.WriteRune(r)
		case r == ' ':
			b.WriteRune('-')
		}
	}
	return b.String()
}

// dedent strips the indentation common to every non-blank line (cobra
// examples are conventionally indented two spaces).
func dedent(s string) string {
	lines := strings.Split(strings.TrimRight(s, "\n"), "\n")
	common := -1
	for _, l := range lines {
		if strings.TrimSpace(l) == "" {
			continue
		}
		indent := len(l) - len(strings.TrimLeft(l, " \t"))
		if common < 0 || indent < common {
			common = indent
		}
	}
	if common <= 0 {
		return strings.Join(lines, "\n")
	}
	for i, l := range lines {
		if len(l) >= common {
			lines[i] = l[common:]
		} else {
			lines[i] = strings.TrimLeft(l, " \t")
		}
	}
	return strings.Join(lines, "\n")
}

func longestRun(s string, r byte) int {
	best, cur := 0, 0
	for i := range len(s) {
		if s[i] == r {
			cur++
			best = max(best, cur)
		} else {
			cur = 0
		}
	}
	return best
}

func containsString(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}
