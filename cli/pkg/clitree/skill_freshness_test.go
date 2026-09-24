package clitree

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// skillContentDir holds the shipped skill bodies embedded into the CLI
// (go:embed content/*.md) and written into agent runtimes by `jentic skill
// init` / `jentic setup`. They are the authoritative "what the skill
// teaches an agent to run" surface.
const skillContentDir = "../../internal/skillgen/content"

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
// off the arguments/placeholders/flags. The binary name must be followed by
// whitespace (or end of line/inline-code span): "jentic/jentic-public-apis"
// (a GitHub repo slug) is a word-boundary match but NOT an invocation, and
// must not have the line's flags attributed to the `jentic` binary.
var invocationLine = regexp.MustCompile(`(?m)\b(jentic|jenticctl)\b(?:[ \t]([^\n` + "`" + `]*))?`)

// commandToken is a plausible subcommand name: lowercase letters/digits/hyphens.
// A token that isn't one of these (a flag, a <placeholder>, a "quoted string", a
// $VAR, a path, an operation_id with slashes/dots) ends the command path — the
// rest are arguments, not part of the command tree.
var commandToken = regexp.MustCompile(`^[a-z][a-z0-9-]*$`)

// invocation is one documented binary invocation: the resolved command path
// plus every long-flag name that appears on the same line.
type invocation struct {
	path  string
	flags []string
}

// longFlagToken matches a long flag at the start of a token and captures its
// name, tolerating an attached "=value" ("--trace=abc" -> "trace").
var longFlagToken = regexp.MustCompile(`^--([a-z][a-z0-9-]*)`)

// extractInvocations pulls every documented invocation from a skill body: the
// command path (leading bareword tokens) and the long flags on the rest of the
// line. Quoted arguments are elided BEFORE tokenizing so a "--flag" inside a
// JSON body or quoted string ('{"note": "--not-a-flag"}') is never mistaken
// for a flag of the command.
func extractInvocations(body string) []invocation {
	matches := invocationLine.FindAllStringSubmatch(body, -1)
	out := make([]invocation, 0, len(matches))
	for _, m := range matches {
		bin := m[1]
		rest := strings.Fields(stripQuoted(m[2]))
		parts := []string{bin}
		var flags []string
		pathDone := false
		for _, tok := range rest {
			if !pathDone && commandToken.MatchString(tok) {
				parts = append(parts, tok)
				continue
			}
			pathDone = true
			if fm := longFlagToken.FindStringSubmatch(tok); fm != nil {
				flags = append(flags, fm[1])
			}
		}
		out = append(out, invocation{path: strings.Join(parts, " "), flags: flags})
	}
	return out
}

// stripQuoted removes single- and double-quoted spans from a line so their
// contents can't be misread as flags or command tokens. Unterminated quotes
// drop the tail of the line — safer to under-read than to false-positive.
func stripQuoted(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c == '\'' || c == '"' {
			end := strings.IndexByte(s[i+1:], c)
			if end < 0 {
				break
			}
			i += end + 1
			continue
		}
		b.WriteByte(c)
	}
	return b.String()
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
//
// It also validates FLAGS (review gap: the gate checked command paths but not
// flags, which let a skill teach `history export` without its required
// `--trace`): every `--flag` token on an invocation line whose command path
// resolves exactly must exist on that command (inherited flags included).
func TestSkillsReferenceOnlyRealCommands(t *testing.T) {
	paths, flags := binaryCommandPaths(t)

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
		for _, inv := range extractInvocations(string(body)) {
			if !knownPathPrefix(inv.path, paths) {
				t.Errorf("skill %s references unknown command %q — it is not in the CLI reference; "+
					"a renamed/removed command must be updated in skills/<name>/SKILL.md (then `make skills`)",
					e.Name(), inv.path)
				continue
			}
			// Flags are only checkable when the path resolves to an exact
			// command (a bare group/prefix mention can't say which subcommand's
			// flags apply). Exact-path lines are the ones agents copy verbatim,
			// so they are precisely the surface that must not teach dead flags.
			known, exact := flags[inv.path]
			if !exact {
				continue
			}
			for _, fl := range inv.flags {
				// Cobra registers --help on every command implicitly; the
				// generated reference omits it, but it is always valid.
				if fl == "help" {
					continue
				}
				if !known[fl] {
					t.Errorf("skill %s documents flag --%s on %q, but the command does not define it; "+
						"update skills/<name>/SKILL.md (then `make skills`)",
						e.Name(), fl, inv.path)
				}
			}
		}
	}
	if scanned == 0 {
		t.Fatal("no skill content files scanned — the freshness gate must not pass vacuously")
	}
}
