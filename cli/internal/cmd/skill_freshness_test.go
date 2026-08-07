package cmd

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// skillContentDir holds the shipped skill bodies embedded into the CLI
// (go:embed content/*.md) and written into agent runtimes by `jentic skill
// init` / `jentic bootstrap`. They are the authoritative "what the skill
// teaches an agent to run" surface.
const skillContentDir = "../skillgen/content"

// binaryCommandPaths flattens a BuildCLIReference() tree into the set of every
// valid command invocation path ("jentic access request", "jenticctl install",
// …) plus, per path, its set of long flag names (including inherited). It is the
// ground truth the skill freshness gate checks documented invocations against.
func binaryCommandPaths(t *testing.T) (paths map[string]bool, flags map[string]map[string]bool) {
	t.Helper()
	paths = map[string]bool{}
	flags = map[string]map[string]bool{}

	var walk func(bin string, cmds []CommandDoc)
	walk = func(bin string, cmds []CommandDoc) {
		for _, c := range cmds {
			paths[c.Path] = true
			fs := map[string]bool{}
			for _, f := range c.Flags {
				fs[f.Name] = true
			}
			flags[c.Path] = fs
			walk(bin, c.Subcommands)
		}
	}
	for _, b := range BuildCLIReference().Binaries {
		// The bare binary name is always a valid invocation (help/root).
		paths[b.Name] = true
		flags[b.Name] = map[string]bool{}
		walk(b.Name, b.Commands)
	}
	return paths, flags
}

// invocationLine matches a shell line (in fenced blocks OR inline `code`) that
// starts an invocation of one of our binaries. The trailing capture is the rest
// of the line, from which we peel the command PATH (leading bareword tokens)
// off the arguments/placeholders/flags.
var invocationLine = regexp.MustCompile(`(?m)\b(jentic|jenticctl)\b([^\n` + "`" + `]*)`)

// commandToken is a plausible subcommand name: lowercase letters/digits/hyphens.
// A token that isn't one of these (a flag, a <placeholder>, a "quoted string", a
// $VAR, a path, an operation_id with slashes/dots) ends the command path — the
// rest are arguments, not part of the command tree.
var commandToken = regexp.MustCompile(`^[a-z][a-z0-9-]*$`)

// extractCommandPaths pulls the set of "<binary> <sub> <sub>…" command paths a
// skill body references. For each invocation it consumes leading command tokens
// until the first argument/flag/placeholder, so "jentic access request
// --provision <x>" yields "jentic access request".
func extractCommandPaths(body string) []string {
	matches := invocationLine.FindAllStringSubmatch(body, -1)
	out := make([]string, 0, len(matches))
	for _, m := range matches {
		bin := m[1]
		rest := strings.Fields(m[2])
		parts := []string{bin}
		for _, tok := range rest {
			// Stop at the first non-command token (flag, placeholder, quoted
			// arg, slash/dot-bearing id, etc.).
			if !commandToken.MatchString(tok) {
				break
			}
			parts = append(parts, tok)
		}
		out = append(out, strings.Join(parts, " "))
	}
	return out
}

// knownPathPrefix reports whether candidate is a real command path OR a valid
// prefix of one. A skill line like "jentic access" (the group, used mid-sentence
// before naming a subcommand) is legitimate even when only "jentic access
// request" is a leaf — cobra runs the group's help. So a candidate is fine if it
// exactly matches a path or is a strict prefix of some deeper path.
func knownPathPrefix(candidate string, paths map[string]bool) bool {
	if paths[candidate] {
		return true
	}
	prefix := candidate + " "
	for p := range paths {
		if strings.HasPrefix(p, prefix) {
			return true
		}
	}
	return false
}

// TestSkillsReferenceOnlyRealCommands is the skill-freshness gate (impl/8.0 §2
// item 6, plan.md Phase 5 rollout): every shipped skill body may only teach
// command invocations that exist in the generated CLI reference. A V2 surface
// change (renamed/removed command) that isn't reflected in the skill fails here,
// so agents never receive a skill that instructs them to run a command the
// binary no longer has. Distinct from the skill-DRIFT gate (which only checks
// the embedded copies are byte-identical to their source) — this validates the
// source's CONTENT against the real command tree.
func TestSkillsReferenceOnlyRealCommands(t *testing.T) {
	paths, _ := binaryCommandPaths(t)

	entries, err := os.ReadDir(skillContentDir)
	if err != nil {
		t.Fatalf("read skill content dir: %v", err)
	}
	var scanned int
	for _, e := range entries {
		if e.IsDir() || filepath.Ext(e.Name()) != ".md" {
			continue
		}
		body, err := os.ReadFile(filepath.Join(skillContentDir, e.Name()))
		if err != nil {
			t.Fatalf("read %s: %v", e.Name(), err)
		}
		scanned++
		for _, cand := range extractCommandPaths(string(body)) {
			if !knownPathPrefix(cand, paths) {
				t.Errorf("skill %s references unknown command %q — it is not in the CLI reference; "+
					"a renamed/removed command must be updated in skills/<name>/SKILL.md (then `make skills`)",
					e.Name(), cand)
			}
		}
	}
	if scanned == 0 {
		t.Fatal("no skill content files scanned — the freshness gate must not pass vacuously")
	}
}
