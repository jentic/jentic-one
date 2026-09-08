// clidocs.go builds a machine-readable reference of the CLI command tree.
//
// It walks the assembled cobra roots for both binaries (`jentic` and
// `jenticctl`) and serialises every command, group, flag, and example. The docs
// SPA renders this so the CLI documentation is generated from the same cobra
// definitions the CLI ships with — it can never drift from `--help`.
//
// A tiny generator binary (cli/cmd/clidocs) calls BuildCLIReference and writes
// ui/public/cli-reference.json; a make target regenerates it.

package clitree

import (
	"sort"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"

	"github.com/jentic/jentic-one/cli/internal/cli/api"
	"github.com/jentic/jentic-one/cli/internal/cli/ctlcmd"
)

// CLIReferenceSchema identifies the JSON shape (bump on a breaking change).
const CLIReferenceSchema = "jentic.cli-reference/v1"

// FlagDoc is one flag of a command.
type FlagDoc struct {
	Name      string `json:"name"`
	Shorthand string `json:"shorthand,omitempty"`
	Type      string `json:"type"`
	Default   string `json:"default,omitempty"`
	Usage     string `json:"usage"`
}

// CommandDoc is one command (or subcommand) in the tree.
type CommandDoc struct {
	// Name is the leaf name (e.g. "add-key").
	Name string `json:"name"`
	// Path is the full invocation, e.g. "jentic profile add-key".
	Path string `json:"path"`
	// Use is cobra's usage line (carries the positional-arg shape).
	Use         string       `json:"use"`
	Short       string       `json:"short"`
	Long        string       `json:"long,omitempty"`
	Example     string       `json:"example,omitempty"`
	Aliases     []string     `json:"aliases,omitempty"`
	GroupTitle  string       `json:"group_title,omitempty"`
	Flags       []FlagDoc    `json:"flags,omitempty"`
	Subcommands []CommandDoc `json:"subcommands,omitempty"`
}

// BinaryDoc is one CLI binary and its top-level command tree.
type BinaryDoc struct {
	Name     string       `json:"name"`
	Tagline  string       `json:"tagline,omitempty"`
	Short    string       `json:"short"`
	Long     string       `json:"long,omitempty"`
	Commands []CommandDoc `json:"commands"`
}

// CLIReference is the full payload the docs SPA consumes.
type CLIReference struct {
	Schema   string      `json:"schema"`
	Binaries []BinaryDoc `json:"binaries"`
}

// BuildCLIReference assembles the reference for both binaries from their cobra
// definitions. It builds the roots with a throwaway App (no filesystem or
// network access happens at construction time — commands only act when run).
func BuildCLIReference() CLIReference {
	return CLIReference{
		Schema: CLIReferenceSchema,
		Binaries: []BinaryDoc{
			binaryDoc(api.NewDocsRoot()),
			binaryDoc(ctlcmd.NewDocsRoot()),
		},
	}
}

func binaryDoc(root *cobra.Command) BinaryDoc {
	groupTitle := groupTitles(root)
	cmds := make([]CommandDoc, 0, len(root.Commands()))
	for _, child := range root.Commands() {
		if skip(child) {
			continue
		}
		cmds = append(cmds, commandDoc(child, root.Name(), groupTitle))
	}
	return BinaryDoc{
		Name:     root.Name(),
		Tagline:  root.Annotations["tagline"],
		Short:    root.Short,
		Long:     root.Long,
		Commands: cmds,
	}
}

// groupTitles maps a command's GroupID to its human title for one parent.
func groupTitles(parent *cobra.Command) map[string]string {
	titles := map[string]string{}
	for _, g := range parent.Groups() {
		titles[g.ID] = g.Title
	}
	return titles
}

func commandDoc(cmd *cobra.Command, parentPath string, groupTitle map[string]string) CommandDoc {
	path := parentPath + " " + cmd.Name()

	subTitles := groupTitles(cmd)
	subs := make([]CommandDoc, 0, len(cmd.Commands()))
	for _, child := range cmd.Commands() {
		if skip(child) {
			continue
		}
		subs = append(subs, commandDoc(child, path, subTitles))
	}

	return CommandDoc{
		Name:        cmd.Name(),
		Path:        path,
		Use:         cmd.Use,
		Short:       cmd.Short,
		Long:        cmd.Long,
		Example:     cmd.Example,
		Aliases:     cmd.Aliases,
		GroupTitle:  groupTitle[cmd.GroupID],
		Flags:       flagDocs(cmd),
		Subcommands: subs,
	}
}

// flagDocs collects a command's own flags plus any persistent flags it inherits
// from parents (so a subcommand documents the --profile/--base-url it accepts),
// de-duplicated and sorted by name.
func flagDocs(cmd *cobra.Command) []FlagDoc {
	seen := map[string]FlagDoc{}

	collect := func(name, shorthand, ftype, def, usage string) {
		if name == "help" { // cobra's built-in, not interesting to document
			return
		}
		seen[name] = FlagDoc{
			Name:      name,
			Shorthand: shorthand,
			Type:      ftype,
			Default:   def,
			Usage:     usage,
		}
	}

	cmd.LocalFlags().VisitAll(func(f *pflag.Flag) {
		if f.Hidden {
			return // hidden flags are intentionally kept out of the public reference
		}
		collect(f.Name, f.Shorthand, f.Value.Type(), f.DefValue, f.Usage)
	})
	cmd.InheritedFlags().VisitAll(func(f *pflag.Flag) {
		if f.Hidden {
			return
		}
		collect(f.Name, f.Shorthand, f.Value.Type(), f.DefValue, f.Usage)
	})

	out := make([]FlagDoc, 0, len(seen))
	for _, f := range seen {
		out = append(out, f)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// skip hides cobra's auto-generated helper commands.
func skip(cmd *cobra.Command) bool {
	return cmd.Hidden || cmd.Name() == "help" || cmd.Name() == "completion"
}
