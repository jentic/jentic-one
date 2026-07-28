package skillgen

import (
	"fmt"
	"path/filepath"
	"strings"
)

// renderBody builds the shared markdown body (heading → sections → steps) that
// every adapter wraps in its own frontmatter and managed block. The leading
// `# Title` is included so single-file targets (AGENTS.md) read naturally; dir
// targets (SKILL.md) keep it too, which is harmless under their frontmatter.
func renderBody(c Canonical) string {
	var b strings.Builder
	fmt.Fprintf(&b, "# %s\n\n", titleFor(c))
	if c.BaseURL != "" {
		fmt.Fprintf(&b, "Jentic control plane: `%s`\n\n", c.BaseURL)
	}

	writeBullets(&b, "When to Use", c.WhenToUse)
	writeBullets(&b, "Prerequisites", c.Prereqs)

	if len(c.Steps) > 0 {
		b.WriteString("## Procedure\n\n")
		for i, s := range c.Steps {
			fmt.Fprintf(&b, "### %d. %s\n\n", i+1, s.Title)
			b.WriteString(strings.TrimRight(s.Body, "\n"))
			b.WriteString("\n\n")
		}
	}

	writeBullets(&b, "Quick Reference", c.QuickRef)
	writeBullets(&b, "Pitfalls", c.Pitfalls)
	writeBullets(&b, "Verification", c.Verify)

	return strings.TrimRight(b.String(), "\n") + "\n"
}

// titleFor is the human heading for the skill body.
func titleFor(c Canonical) string {
	if c.Name == "jentic" {
		return "Using Jentic from the CLI"
	}
	return c.Name
}

func writeBullets(b *strings.Builder, heading string, items []string) {
	if len(items) == 0 {
		return
	}
	fmt.Fprintf(b, "## %s\n\n", heading)
	for _, it := range items {
		fmt.Fprintf(b, "- %s\n", it)
	}
	b.WriteByte('\n')
}

// source returns the content's provenance, defaulting to bundled.
func (c Canonical) source() Source {
	if c.Origin == "" {
		return SourceBundled
	}
	return c.Origin
}

// renderSingleFile is the common Render path for single-file targets
// (AGENTS.md, CLAUDE.md): splice the managed body into existing content.
func renderSingleFile(c Canonical, existing []byte) ([]byte, bool, error) {
	res := splice(existing, renderBody(c), c.source())
	return res.out, res.changed, nil
}

// renderDedicated is the Render path for targets whose whole file is ours
// (claude/hermes SKILL.md): a frontmatter prelude followed by the managed
// block. The body lives in the block so manual-edit detection still works; the
// frontmatter is always (re)written. "changed" reports whether the resulting
// file differs from what is already on disk.
func renderDedicated(frontmatter string, c Canonical, existing []byte) ([]byte, bool, error) {
	block := managedBlock(renderBody(c), c.source())
	want := strings.TrimRight(frontmatter, "\n") + "\n\n" + block + "\n"
	changed := string(existing) != want
	return []byte(want), changed, nil
}

// --- dir-skill adapter (claude / cursor) ------------------------------------

// dirSkillAdapter targets runtimes whose native skill layout is a dedicated
// directory `<base>/<dir>/skills/jentic/SKILL.md` holding a SKILL.md with
// `name` + `description` YAML frontmatter, where the model decides whether to
// launch the skill from that description (progressive disclosure). Claude Code
// (`.claude`) and Cursor (`.cursor`) share this exact shape — Cursor even reads
// `.claude/skills` for compatibility, but we write its own `.cursor` dir so a
// Cursor-only user still gets it. This is the *canonical* mechanism for both,
// as opposed to the always-in-context AGENTS.md splice used for codex/generic.
type dirSkillAdapter struct {
	op      Operator
	dir     string // the runtime's config dir under the base, e.g. ".claude"
	aliases []string
	detect  func(env DetectEnv) bool
}

func (a dirSkillAdapter) Operator() Operator { return a.op }
func (a dirSkillAdapter) Aliases() []string  { return a.aliases }

// DefaultScope is user, not project: these runtimes resolve *project* skills
// from the launch cwd, so a project-scoped install only loads when the agent
// runs from that exact directory (and, run from a source tree, leaves a stray
// skills dir behind). A "how to use Jentic" capability isn't tied to one repo,
// so default to user scope (~/.claude|.cursor/skills), available everywhere.
// Pass --scope project to pin it to a specific checkout.
//
// Ratified as the deliberate policy in #552 (claude/cursor/hermes → user,
// codex/generic → project); TestDefaultScopePolicy tripwires any change.
func (dirSkillAdapter) DefaultScope() Scope { return ScopeUser }

func (a dirSkillAdapter) Target(scope Scope, env DetectEnv) string {
	base := env.Home
	if scope == ScopeProject {
		base = env.Cwd
	}
	return filepath.Join(base, a.dir, "skills", "jentic", "SKILL.md")
}

func (a dirSkillAdapter) Render(c Canonical, existing []byte) ([]byte, bool, error) {
	return renderDedicated(dirSkillFrontmatter(c), c, existing)
}

func (dirSkillAdapter) OwnsWholeFile() bool { return true }

func (a dirSkillAdapter) Detect(env DetectEnv) bool {
	if a.detect == nil {
		return false
	}
	return a.detect(env)
}

// dirSkillFrontmatter is the minimal `name` + `description` frontmatter both
// Claude Code and Cursor require on a SKILL.md; the full (rich) description is
// emitted verbatim so the model has the strongest possible trigger signal.
func dirSkillFrontmatter(c Canonical) string {
	return fmt.Sprintf("---\nname: %s\ndescription: %s\n---\n", c.Name, c.Description)
}

// --- hermes adapter ---------------------------------------------------------

// hermesAdapter targets NousResearch/hermes-agent: a SKILL.md under
// ~/.hermes/skills/<category>/<skill-name>/ with hermes frontmatter and section
// order. It auto-registers as a /slash command on install.
type hermesAdapter struct{}

func (hermesAdapter) Operator() Operator  { return OpHermes }
func (hermesAdapter) Aliases() []string   { return []string{"hermes", "hermes-agent"} }
func (hermesAdapter) DefaultScope() Scope { return ScopeUser }

func (hermesAdapter) Target(scope Scope, env DetectEnv) string {
	base := env.Home
	if scope == ScopeProject {
		base = env.Cwd
	}
	// <category>/<skill-name>: "api/jentic". The directory name is the install
	// slug and the matched skill name, so category and skill must differ (a
	// "jentic/jentic" would make them identical).
	return filepath.Join(base, ".hermes", "skills", "api", "jentic", "SKILL.md")
}

func (hermesAdapter) Render(c Canonical, existing []byte) ([]byte, bool, error) {
	return renderDedicated(hermesFrontmatter(c), c, existing)
}

func (hermesAdapter) OwnsWholeFile() bool { return true }

func (hermesAdapter) Detect(env DetectEnv) bool {
	return env.exists(filepath.Join(env.Home, ".hermes")) || env.has("hermes")
}

func hermesFrontmatter(c Canonical) string {
	desc := hermesDescription(c.Description)
	var b strings.Builder
	b.WriteString("---\n")
	fmt.Fprintf(&b, "name: %s\n", c.Name)
	fmt.Fprintf(&b, "description: %s\n", desc)
	fmt.Fprintf(&b, "version: %s\n", c.Version)
	b.WriteString("metadata:\n  hermes:\n    category: api\n    tags: [jentic, api, broker, cli]\n")
	b.WriteString("---\n")
	return b.String()
}

// hermesDescription adapts the rich canonical description to Hermes' authoring
// rule: one sentence, ending with a period, kept short (<= 60 chars is the
// documented review HARDLINE; the loader itself accepts up to 1024). We take
// the first sentence of the canonical description, and if that is still over
// the cap fall back to a fixed, compliant one-liner rather than truncate a
// sentence mid-word (which would drop the trailing period the rule requires).
func hermesDescription(full string) string {
	const maxLen = 60
	s := strings.TrimSpace(full)
	if i := strings.IndexByte(s, '.'); i >= 0 {
		s = s[:i]
	}
	s = strings.TrimSpace(s)
	if s == "" || len([]rune(s)) > maxLen-1 { // -1 leaves room for the period
		return "Find and run third-party APIs via the Jentic CLI."
	}
	return s + "."
}

// --- agents adapter (codex / generic) ---------------------------------------

// agentsAdapter targets the cross-tool AGENTS.md standard, used for the codex
// and generic operators. It splices the managed block into an existing
// AGENTS.md (or creates one), preserving surrounding user content. Unlike the
// dir-skill runtimes, AGENTS.md is always-in-context (no description-based
// selection), so the block is present whenever the file is loaded.
type agentsAdapter struct {
	op      Operator
	aliases []string
	detect  func(env DetectEnv) bool
}

func (a agentsAdapter) Operator() Operator { return a.op }
func (a agentsAdapter) Aliases() []string  { return a.aliases }

// DefaultScope is project: AGENTS.md is the cross-tool *repo* instruction
// file, and codex only auto-loads a user-global copy from ~/.codex. Ratified
// in #552 alongside the dir-skill user default; TestDefaultScopePolicy
// tripwires any change. Interactive runs confirm placement via the scope
// prompt; non-interactive runs echo the resolved target before writing.
func (a agentsAdapter) DefaultScope() Scope { return ScopeProject }

func (a agentsAdapter) Target(scope Scope, env DetectEnv) string {
	base := env.Cwd
	if scope == ScopeUser {
		// Codex reads ~/.codex/AGENTS.md; generic falls back to ~/AGENTS.md.
		if a.op == OpCodex {
			return filepath.Join(env.Home, ".codex", "AGENTS.md")
		}
		base = env.Home
	}
	return filepath.Join(base, "AGENTS.md")
}

func (a agentsAdapter) Render(c Canonical, existing []byte) ([]byte, bool, error) {
	return renderSingleFile(c, existing)
}

func (a agentsAdapter) OwnsWholeFile() bool { return false }

func (a agentsAdapter) Detect(env DetectEnv) bool {
	if a.detect == nil {
		return false
	}
	return a.detect(env)
}

// DefaultRegistry returns the registry of supported adapters in the order the
// interactive picker should present them.
func DefaultRegistry() *Registry {
	return NewRegistry(
		dirSkillAdapter{
			op:      OpClaude,
			dir:     ".claude",
			aliases: []string{"claude", "claude-code", "claudecode"},
			detect: func(env DetectEnv) bool {
				return env.exists(filepath.Join(env.Home, ".claude")) ||
					env.exists(filepath.Join(env.Cwd, ".claude")) ||
					env.has("claude")
			},
		},
		dirSkillAdapter{
			op:      OpCursor,
			dir:     ".cursor",
			aliases: []string{"cursor", "cursor-agent"},
			detect: func(env DetectEnv) bool {
				return env.exists(filepath.Join(env.Home, ".cursor")) ||
					env.exists(filepath.Join(env.Cwd, ".cursor")) ||
					env.has("cursor") || env.has("cursor-agent")
			},
		},
		hermesAdapter{},
		agentsAdapter{
			op:      OpCodex,
			aliases: []string{"codex", "codex-cli"},
			detect: func(env DetectEnv) bool {
				return env.exists(filepath.Join(env.Home, ".codex")) || env.has("codex")
			},
		},
		agentsAdapter{
			op:      OpGeneric,
			aliases: []string{"generic", "agents", "agents.md"},
			// Generic is always offered explicitly, never auto-detected (it
			// would always match and add noise to the picker).
			detect: func(DetectEnv) bool { return false },
		},
	)
}
