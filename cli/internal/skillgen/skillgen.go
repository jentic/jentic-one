// Package skillgen renders one canonical "how to use Jentic via the CLI" skill
// into each supported agent runtime's ("operator") native skill/instruction
// layout. It is the engine behind `jentic skill init/list/remove`: the
// canonical content (bundled now, hosted via #277 later) is shared, and a small
// per-operator Adapter owns where the file lives and how it is formatted.
//
// Writes are idempotent and edit-preserving: generated content lives inside a
// clearly-marked managed block so re-runs replace only our block and never
// clobber a user's surrounding edits.
package skillgen

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// Operator identifies a supported runtime (e.g. "claude", "hermes", "cursor").
type Operator string

// Supported operators.
const (
	OpClaude  Operator = "claude"
	OpHermes  Operator = "hermes"
	OpCursor  Operator = "cursor"
	OpCodex   Operator = "codex"
	OpGeneric Operator = "generic"
)

// Scope selects user-global vs repo-local placement of the generated skill.
type Scope string

// Placement scopes.
const (
	ScopeUser    Scope = "user"
	ScopeProject Scope = "project"
)

// Canonical is the source-agnostic skill content every adapter renders from.
// It is populated from the bundled fallback today and from #277's hosted
// `GET /skills/*` once that endpoint exists. Despite the name it now models two
// kinds (see Kind): the structured "canonical" jentic skill (sections/steps)
// and a "freeform" runbook whose Body is emitted verbatim.
type Canonical struct {
	Name         string   // skill slug, e.g. "jentic" (required frontmatter)
	Description  string   // one line; emitted verbatim in the claude/cursor SKILL.md frontmatter, adapted to a short one-sentence form for hermes; codex/generic (AGENTS.md) don't use it
	Version      string   // content version, e.g. "1"
	Kind         string   // "canonical" (structured) | "freeform" (verbatim body)
	Body         string   // verbatim markdown body for freeform skills
	ArgumentHint string   // optional metadata.argument-hint (Cursor slash-command field)
	WhenToUse    []string // bullet lines (canonical)
	Prereqs      []string // bullet lines (canonical)
	Steps        []Step   // the platform loop (canonical): register -> ... -> execute
	QuickRef     []string // command cheatsheet lines (canonical)
	Pitfalls     []string // bullet lines (canonical)
	Verify       []string // bullet lines (canonical)
	BaseURL      string   // resolved control-plane URL, interpolated into body
	Origin       Source   // where this content came from (bundled/hosted)
}

// isFreeform reports whether this skill is a verbatim-body runbook.
func (c Canonical) isFreeform() bool { return c.Kind == KindFreeform }

// Step is one stage of the platform loop, rendered as a markdown subsection.
type Step struct {
	Title string
	Body  string // markdown; may contain fenced `jentic ...` examples
}

// Source reports where the canonical content came from, recorded in the managed
// block so update/list can reason about provenance and drift.
type Source string

// Content provenance.
const (
	SourceBundled Source = "bundled"
	SourceHosted  Source = "hosted"
)

// DetectEnv carries the inputs adapters probe to decide whether an operator
// looks present. It is injected so detection is deterministic in tests.
type DetectEnv struct {
	Home   string            // user home directory
	Cwd    string            // working directory
	Lookup func(string) bool // reports whether a binary is on PATH
	Stat   func(string) bool // reports whether a path exists
}

// has reports whether name resolves on PATH (nil-safe).
func (e DetectEnv) has(name string) bool {
	return e.Lookup != nil && e.Lookup(name)
}

// exists reports whether path is present on disk (nil-safe).
func (e DetectEnv) exists(path string) bool {
	return e.Stat != nil && e.Stat(path)
}

// Adapter renders the canonical Jentic skill into one operator's native layout.
// Each adapter owns placement (Target) and formatting (Render); the canonical
// content is shared across all of them.
type Adapter interface {
	// Operator is the canonical operator identity this adapter targets.
	Operator() Operator
	// Aliases are command basenames / known names for this operator (e.g.
	// "claude", "claude-code"); used for --operator parsing and as a hint for a
	// possible future `run` integration (deferred — see the design doc).
	Aliases() []string
	// DefaultScope is the placement used when the user does not pass --scope.
	DefaultScope() Scope
	// Target returns the absolute path the named skill is written to, for the
	// given scope and detection environment (which supplies home/cwd).
	Target(scope Scope, name string, env DetectEnv) string
	// Render produces the full file bytes for content, splicing the named
	// managed block into existing (nil/empty when the file is new) for shared
	// files, or the whole clean SKILL.md for owned files. It returns the new
	// bytes and whether the file's content actually changed.
	Render(content Canonical, existing []byte) (out []byte, changed bool, err error)
	// Detect reports whether this operator looks present in env.
	Detect(env DetectEnv) bool
	// OwnsWholeFile reports whether the entire target file is generated by this
	// adapter (a dedicated SKILL.md we own outright) versus a shared file we
	// only splice a named block into (AGENTS.md). For owned-file targets,
	// edit detection is via a sidecar provenance file; for shared files it is
	// via the named block's recorded hash.
	OwnsWholeFile() bool
}

// Managed-block sentinels. The generated body always lives between these so a
// re-run replaces only our region and leaves user edits around it untouched.
//
// Markers are per-skill *named* so several skills can coexist in one shared
// file (AGENTS.md) without collision: findBlock scans for a specific skill's
// begin marker among possibly many. The legacy (un-named) constants are kept
// for migration reads of installs written before the multi-skill split — see
// findLegacyBlock.
const (
	legacyBeginMarker = "<!-- BEGIN JENTIC MANAGED SKILL (do not edit inside; regenerated by Jentic) -->"
	legacyEndMarker   = "<!-- END JENTIC MANAGED SKILL -->"
)

// legacySkillName is the only skill that could ever have been written as the
// old *un-named* managed block: before the multi-skill split there was exactly
// one skill ("jentic"). A legacy block therefore always belongs to jentic, and
// migration of it must be attributed to jentic alone — never to a flow skill
// that happens to be processed first (which would clobber jentic's block,
// freeze siblings, or misreport install state). See findMigratableLegacyBlock.
const legacySkillName = "jentic"

// beginMarkerFor / endMarkerFor build a skill's named managed-block sentinels.
func beginMarkerFor(name string) string {
	return fmt.Sprintf("<!-- BEGIN JENTIC MANAGED SKILL: %s (do not edit inside; regenerated by Jentic) -->", name)
}

func endMarkerFor(name string) string {
	return fmt.Sprintf("<!-- END JENTIC MANAGED SKILL: %s -->", name)
}

// block is the parsed metadata of a managed region found in an existing file.
type block struct {
	found  bool   // a managed region was present
	legacy bool   // matched the old un-named markers (migration path)
	begin  string // the begin marker string this block was located by
	end    string // the end marker string this block was located by
	hash   string // content hash recorded in the BEGIN line's metadata
	source string // provenance recorded in the BEGIN line's metadata
	start  int    // byte offset of the BEGIN marker
	endPos int    // byte offset just past the END marker
}

// hashContent returns a short, stable hash of the rendered body so re-runs can
// short-circuit when nothing changed. The body is normalized (CRLF collapsed,
// trailing newlines trimmed) so the recorded hash matches the hash of the body
// re-extracted from disk, where the END marker forces a trailing newline.
func hashContent(body string) string {
	sum := sha256.Sum256([]byte(strings.TrimRight(normalizeNewlines(body), "\n")))
	return hex.EncodeToString(sum[:])[:12]
}

// hashPattern matches a well-formed recorded hash (the 12-char hex truncation
// hashContent emits). A recorded hash that does not match this was written by
// something other than the current generator, so it is treated as unknown
// provenance (refreshable) rather than a deliberate user edit.
var hashPattern = regexp.MustCompile(`^[0-9a-f]{12}$`)

// normalizeNewlines collapses CRLF/CR line endings to LF so hashing and
// splicing behave identically regardless of how an editor saved the file.
func normalizeNewlines(s string) string {
	if !strings.ContainsRune(s, '\r') {
		return s
	}
	return strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n")
}

// managedBlock wraps body in the named BEGIN/END sentinels for skill name,
// annotating the BEGIN line with the content hash and source for provenance
// and drift detection.
func managedBlock(name, body string, src Source) string {
	h := hashContent(body)
	var b strings.Builder
	b.WriteString(beginMarkerFor(name))
	fmt.Fprintf(&b, " hash=%s source=%s", h, src)
	b.WriteByte('\n')
	b.WriteString(strings.TrimRight(body, "\n"))
	b.WriteByte('\n')
	b.WriteString(endMarkerFor(name))
	return b.String()
}

// findBlock locates skill name's named managed region in data and parses its
// metadata. The BEGIN marker is only honored at the start of a line so a marker
// quoted inside a user's fenced code block or prose is not mistaken for a real
// region.
func findBlock(data []byte, name string) block {
	return locateBlock(normalizeNewlines(string(data)), beginMarkerFor(name), endMarkerFor(name), false)
}

// findLegacyBlock locates an install written before the multi-skill split: the
// old un-named managed region. Used only for migration so the body hash still
// matches (stripping the correct end marker) and the block is not falsely
// flagged as user-edited.
func findLegacyBlock(data []byte) block {
	return locateBlock(normalizeNewlines(string(data)), legacyBeginMarker, legacyEndMarker, true)
}

// findMigratableLegacyBlock returns the legacy un-named block only when name is
// the skill that block could belong to (jentic). Every shared-file consumer
// (splice, apply, remove, status) must funnel through this rather than calling
// findLegacyBlock directly: the legacy block is jentic's by construction, so
// attributing it to a flow skill would let that skill overwrite jentic's block,
// freeze jentic as a "sibling", or misreport the flow skill as installed.
func findMigratableLegacyBlock(data []byte, name string) block {
	if name != legacySkillName {
		return block{}
	}
	return findLegacyBlock(data)
}

// locateBlock finds the region delimited by begin/end (both line-anchored) and
// parses the "hash=… source=…" tokens off the BEGIN line.
func locateBlock(s, begin, end string, legacy bool) block {
	bi := lineAnchoredIndex(s, begin)
	if bi < 0 {
		return block{}
	}
	relEnd := lineAnchoredIndex(s[bi:], end)
	if relEnd < 0 {
		return block{}
	}
	endPos := bi + relEnd + len(end)

	blk := block{found: true, legacy: legacy, begin: begin, end: end, start: bi, endPos: endPos}
	rest := s[bi:]
	lineEnd := strings.IndexByte(rest, '\n')
	if lineEnd < 0 {
		lineEnd = len(rest)
	}
	for _, tok := range strings.Fields(rest[len(begin):lineEnd]) {
		k, v, ok := strings.Cut(tok, "=")
		if !ok {
			continue
		}
		switch k {
		case "hash":
			blk.hash = v
		case "source":
			blk.source = v
		}
	}
	return blk
}

// lineAnchoredIndex returns the byte offset of the first occurrence of marker
// that begins a line (offset 0 or immediately after a '\n'), or -1.
func lineAnchoredIndex(s, marker string) int {
	from := 0
	for {
		i := strings.Index(s[from:], marker)
		if i < 0 {
			return -1
		}
		abs := from + i
		if abs == 0 || s[abs-1] == '\n' {
			return abs
		}
		from = abs + 1
	}
}

// spliceResult reports what splice did, so callers can message the user.
type spliceResult struct {
	out       []byte // the resulting file bytes
	changed   bool   // the managed block's content changed (or was created)
	created   bool   // there was no managed block before
	userEdits bool   // the existing block's hash did not match its recorded body
}

// blockUserEdited reports whether an existing managed block's recorded hash
// proves a deliberate user edit. A missing or malformed hash (anything the
// current generator would not have written) is treated as unknown provenance —
// refreshable, not a user edit — so a clean re-run can recover it.
func blockUserEdited(existing []byte, blk block) bool {
	if !hashPattern.MatchString(blk.hash) {
		return false
	}
	return hashContent(currentBlockBody(existing, blk)) != blk.hash
}

// splice inserts or replaces skill name's named managed block built from
// body+src within existing, leaving any sibling named blocks byte-identical.
// For a new file (existing empty / no matching block) it appends our block
// after any existing content (a leading prelude for single-file adapters, or
// sibling blocks) with a separating blank line; on a truly empty file the block
// is the whole file.
//
// A pre-multi-skill install may carry only the old un-named block: it is
// migrated in place (matched by the legacy markers so its hash still verifies)
// and rewritten as the named block.
//
// When the existing block's recorded hash does not match the hash of its actual
// on-disk body, userEdits is set so the caller can warn and require --force.
func splice(existing []byte, name, body string, src Source) spliceResult {
	existing = []byte(normalizeNewlines(string(existing)))
	newBlock := managedBlock(name, body, src)
	newHash := hashContent(body)

	blk := findBlock(existing, name)
	if !blk.found {
		// No named block for this skill. A legacy un-named block is migrated in
		// place *only* for the skill it could belong to (jentic); otherwise we
		// insert a fresh block.
		if legacy := findMigratableLegacyBlock(existing, name); legacy.found {
			return spliceReplace(existing, legacy, newBlock, newHash)
		}
		trimmed := strings.TrimRight(string(existing), "\n")
		if trimmed == "" {
			return spliceResult{out: []byte(newBlock + "\n"), changed: true, created: true}
		}
		return spliceResult{
			out:     []byte(trimmed + "\n\n" + newBlock + "\n"),
			changed: true,
			created: true,
		}
	}

	return spliceReplace(existing, blk, newBlock, newHash)
}

// spliceReplace rewrites blk (a located existing region, named or legacy) with
// newBlock, detecting a manual edit against the recorded hash.
func spliceReplace(existing []byte, blk block, newBlock, newHash string) spliceResult {
	userEdited := blockUserEdited(existing, blk)

	// An untouched, already-current, already-named block is a no-op. A legacy
	// block always rewrites (it must be migrated to the named form even when
	// the body is identical).
	if !blk.legacy && blk.hash == newHash && !userEdited {
		return spliceResult{out: existing, changed: false, userEdits: false}
	}

	out := string(existing[:blk.start]) + newBlock + string(existing[blk.endPos:])
	if out == string(existing) {
		return spliceResult{out: existing, changed: false, userEdits: userEdited}
	}
	return spliceResult{
		out:       []byte(out),
		changed:   true,
		userEdits: userEdited,
	}
}

// currentBlockBody extracts the body bytes between the BEGIN line and the END
// marker of an existing managed region (used to re-hash for edit detection).
// It honors the block's own end marker so a legacy block is stripped with the
// legacy marker and its hash still verifies.
func currentBlockBody(data []byte, blk block) string {
	region := string(data[blk.start:blk.endPos])
	// Drop the BEGIN line (up to and including its newline).
	if nl := strings.IndexByte(region, '\n'); nl >= 0 {
		region = region[nl+1:]
	}
	region = strings.TrimSuffix(region, blk.end)
	return strings.TrimRight(region, "\n")
}

// Registry holds the supported adapters and resolves operator names/aliases.
type Registry struct {
	adapters []Adapter
	byAlias  map[string]Adapter
}

// NewRegistry builds a registry from the given adapters, indexing every alias.
func NewRegistry(adapters ...Adapter) *Registry {
	r := &Registry{adapters: adapters, byAlias: map[string]Adapter{}}
	for _, a := range adapters {
		r.byAlias[string(a.Operator())] = a
		for _, al := range a.Aliases() {
			r.byAlias[strings.ToLower(al)] = a
		}
	}
	return r
}

// Adapters returns the registered adapters in registration order.
func (r *Registry) Adapters() []Adapter { return r.adapters }

// Resolve maps an operator name or alias to its adapter (case-insensitive).
func (r *Registry) Resolve(name string) (Adapter, bool) {
	a, ok := r.byAlias[strings.ToLower(strings.TrimSpace(name))]
	return a, ok
}

// ResolveAll resolves a list of names/aliases, deduping by operator and
// preserving the registry's order. Unknown names are returned separately.
func (r *Registry) ResolveAll(names []string) (resolved []Adapter, unknown []string) {
	want := map[Operator]bool{}
	for _, n := range names {
		a, ok := r.Resolve(n)
		if !ok {
			unknown = append(unknown, n)
			continue
		}
		want[a.Operator()] = true
	}
	for _, a := range r.adapters {
		if want[a.Operator()] {
			resolved = append(resolved, a)
		}
	}
	return resolved, unknown
}

// Detected returns the adapters whose Detect reports present in env, in
// registry order.
func (r *Registry) Detected(env DetectEnv) []Adapter {
	var out []Adapter
	for _, a := range r.adapters {
		if a.Detect(env) {
			out = append(out, a)
		}
	}
	return out
}

// Names returns every supported operator name, sorted, for help/error text.
func (r *Registry) Names() []string {
	out := make([]string, 0, len(r.adapters))
	for _, a := range r.adapters {
		out = append(out, string(a.Operator()))
	}
	sort.Strings(out)
	return out
}
