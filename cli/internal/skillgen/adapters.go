package skillgen

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"path/filepath"
	"strings"
)

// renderBody builds the shared markdown body (heading → sections → steps) for a
// structured canonical skill. The leading `# Title` is included so single-file
// targets read naturally; dir targets (SKILL.md) keep it too, which is
// harmless under their frontmatter.
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

// skillBody returns the model-facing markdown body for a skill: the verbatim
// authored body for a freeform runbook (emitted exactly as written — it already
// begins with its own `# Title`), or the structured render for a canonical
// skill.
func skillBody(c Canonical) string {
	if c.isFreeform() {
		return strings.TrimRight(c.Body, "\n") + "\n"
	}
	return renderBody(c)
}

// titleFor is the human heading for a skill. The nice onboarding title is
// reserved for the canonical jentic skill; every other skill (canonical or
// freeform) uses its own name. Called both for the canonical body heading and
// for the AGENTS.md pointer block's `## <title>` line, so it must handle
// freeform skills too (they get their name).
func titleFor(c Canonical) string {
	if c.Kind == KindCanonical && c.Name == "jentic" {
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

// --- sidecar provenance (owned-file operators) -----------------------------

// sidecarName is the provenance file written next to an owned-file SKILL.md.
// The SKILL.md itself stays a clean spec file (frontmatter + verbatim body, no
// managed markers), so provenance lives here instead of inside the served
// bytes.
const sidecarName = ".jentic-skill.json"

// sidecar records what Jentic wrote for an owned-file skill so a re-run can
// tell our content from a user edit without polluting the SKILL.md body.
type sidecar struct {
	Name     string `json:"name"`
	BodyHash string `json:"body_sha256"`
	Source   string `json:"source"`
	BaseURL  string `json:"base_url,omitempty"`
}

// sidecarPath is the sidecar file for a given SKILL.md target.
func sidecarPath(skillMD string) string {
	return filepath.Join(filepath.Dir(skillMD), sidecarName)
}

// dedicatedFileHash fingerprints the *entire* rendered owned-file SKILL.md
// (frontmatter + body), normalized. Edit detection compares this over the
// on-disk file against the sidecar's recorded hash, so a hand-edit to EITHER
// the frontmatter (e.g. a tuned description/argument-hint) or the body is
// caught — not just body edits. It hashes exactly what renderDedicated writes.
func dedicatedFileHash(data []byte) string {
	sum := sha256.Sum256([]byte(strings.TrimRight(normalizeNewlines(string(data)), "\n")))
	return hex.EncodeToString(sum[:])
}

// renderDedicated builds a clean owned-file SKILL.md: YAML frontmatter followed
// by the verbatim skill body, with NO managed-block markers inside it. These
// runtimes load the whole file as the skill, so foreign HTML-comment markers
// would be off-spec noise in the model-facing body. Provenance is written
// separately to a sidecar (see Apply). "changed" reports whether the resulting
// file differs from what is already on disk.
func renderDedicated(frontmatter string, c Canonical, existing []byte) ([]byte, bool, error) {
	want := strings.TrimRight(frontmatter, "\n") + "\n\n" + strings.TrimRight(skillBody(c), "\n") + "\n"
	changed := normalizeNewlines(string(existing)) != want
	return []byte(want), changed, nil
}

// --- dir-skill adapter (claude / cursor) ------------------------------------

// dirSkillAdapter targets runtimes whose native skill layout is a dedicated
// directory `<base>/<dir>/skills/<name>/SKILL.md` holding a SKILL.md with
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

func (a dirSkillAdapter) Target(scope Scope, name string, env DetectEnv) string {
	base := env.Home
	if scope == ScopeProject {
		base = env.Cwd
	}
	return filepath.Join(base, a.dir, "skills", name, "SKILL.md")
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

// dirSkillFrontmatter is the `name` + `description` frontmatter both Claude
// Code and Cursor require on a SKILL.md; the full (rich) description is emitted
// verbatim so the model has the strongest possible trigger signal. An optional
// `metadata.argument-hint` (a Cursor slash-command field) is passed through,
// kept spec-nested under `metadata:`.
func dirSkillFrontmatter(c Canonical) string {
	var b strings.Builder
	b.WriteString("---\n")
	fmt.Fprintf(&b, "name: %s\n", c.Name)
	fmt.Fprintf(&b, "description: %s\n", c.Description)
	if c.ArgumentHint != "" {
		b.WriteString("metadata:\n")
		fmt.Fprintf(&b, "  argument-hint: %q\n", c.ArgumentHint)
	}
	b.WriteString("---\n")
	return b.String()
}

// --- hermes adapter ---------------------------------------------------------

// hermesAdapter targets NousResearch/hermes-agent: a SKILL.md under
// ~/.hermes/skills/<category>/<skill-name>/ with hermes frontmatter. It
// auto-registers as a /slash command on install.
type hermesAdapter struct{}

func (hermesAdapter) Operator() Operator  { return OpHermes }
func (hermesAdapter) Aliases() []string   { return []string{"hermes", "hermes-agent"} }
func (hermesAdapter) DefaultScope() Scope { return ScopeUser }

func (hermesAdapter) Target(scope Scope, name string, env DetectEnv) string {
	base := env.Home
	if scope == ScopeProject {
		base = env.Cwd
	}
	// <category>/<skill-name>: "api/<name>". The directory name is the install
	// slug and the matched skill name, so category and skill must differ.
	return filepath.Join(base, ".hermes", "skills", "api", name, "SKILL.md")
}

func (hermesAdapter) Render(c Canonical, existing []byte) ([]byte, bool, error) {
	return renderDedicated(hermesFrontmatter(c), c, existing)
}

func (hermesAdapter) OwnsWholeFile() bool { return true }

func (hermesAdapter) Detect(env DetectEnv) bool {
	return env.exists(filepath.Join(env.Home, ".hermes")) || env.has("hermes")
}

// hermesFrontmatter derives tags/category from the skill name (not a hardcoded
// jentic) and emits the real, full description. The lossy 60-char shortening is
// reserved for the canonical jentic skill (whose description is rich trigger
// prose written to that authoring rule); freeform runbooks emit their real
// description so nothing is dropped.
func hermesFrontmatter(c Canonical) string {
	desc := c.Description
	if c.Kind == KindCanonical {
		desc = hermesDescription(c.Description)
	}
	var b strings.Builder
	b.WriteString("---\n")
	fmt.Fprintf(&b, "name: %s\n", c.Name)
	fmt.Fprintf(&b, "description: %s\n", desc)
	fmt.Fprintf(&b, "version: %s\n", c.Version)
	fmt.Fprintf(&b, "metadata:\n  hermes:\n    category: api\n    tags: [%s, api, broker, cli]\n", c.Name)
	if c.ArgumentHint != "" {
		fmt.Fprintf(&b, "  argument-hint: %q\n", c.ArgumentHint)
	}
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
// and generic operators. It splices a named managed block into an existing
// AGENTS.md (or creates one), preserving surrounding user content and any
// sibling skills' blocks. Unlike the dir-skill runtimes, AGENTS.md is
// always-in-context (no description-based selection) with no progressive
// disclosure, so the block is a *pointer* — name + description + a
// fetch-on-demand link — not the full (potentially several-hundred-line) body.
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
// tripwires any change.
func (a agentsAdapter) DefaultScope() Scope { return ScopeProject }

func (a agentsAdapter) Target(scope Scope, _ string, env DetectEnv) string {
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
	res := splice(existing, c.Name, agentsPointerBody(c), c.source())
	return res.out, res.changed, nil
}

func (a agentsAdapter) OwnsWholeFile() bool { return false }

func (a agentsAdapter) Detect(env DetectEnv) bool {
	if a.detect == nil {
		return false
	}
	return a.detect(env)
}

// agentsPointerBody is the pointer block spliced into AGENTS.md: the skill
// name, its description, and a fetch-on-demand link to the full body. AGENTS.md
// is always-in-context and the flow skills are long, so splicing full bodies
// would add hundreds of lines of permanent prompt to every run; the pointer is
// the honest analogue of progressive disclosure for a format that lacks it
// (decision 2 in the plan). Pointer-for-all keeps the behavior uniform and the
// context bounded.
func agentsPointerBody(c Canonical) string {
	var b strings.Builder
	fmt.Fprintf(&b, "## %s\n\n", titleFor(c))
	fmt.Fprintf(&b, "%s\n\n", strings.TrimSpace(c.Description))
	if c.BaseURL != "" {
		fmt.Fprintf(&b, "See the full skill: GET %s/skills/%s.md\n", strings.TrimRight(c.BaseURL, "/"), c.Name)
	} else {
		fmt.Fprintf(&b, "See the full skill: GET /skills/%s.md\n", c.Name)
	}
	return b.String()
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
