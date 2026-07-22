package skillgen

import (
	"bufio"
	_ "embed"
	"errors"
	"fmt"
	"strings"
)

// bundledSkill is the canonical "how to use Jentic via the CLI" content shipped
// in the binary via go:embed. The very same file is force-included into the
// backend wheel (see `pyproject.toml` -> `jentic_one/skills/jentic.md`) and
// served over HTTP at `GET /skills/jentic.md` (alias `GET /SKILL.md`) by
// `shared/web/skill_router.py`, so a remote agent can fetch the skill from a
// deployment (issue #651). Because both the embed and the served route read
// this one committed file, the CLI-embedded copy and the served copy cannot
// drift; a backend drift-guard test pins that invariant.
//
//go:embed content/jentic.md
var bundledSkill string

// Bundled parses the embedded canonical skill into a Canonical, stamping the
// resolved control-plane base URL into it. A malformed embed is a build-time
// programming error (the content is compiled in via go:embed and covered by
// tests), so this should never fail at runtime — but it returns an error
// rather than panicking so a CLI command surfaces it cleanly instead of
// crashing.
func Bundled(baseURL string) (Canonical, error) {
	c, err := parseCanonical(bundledSkill)
	if err != nil {
		return Canonical{}, fmt.Errorf("parse bundled skill content: %w", err)
	}
	c.BaseURL = baseURL
	c.Origin = SourceBundled
	return c, nil
}

// parseCanonical reads the structured markdown (YAML-ish frontmatter + the
// known H2 sections) into a Canonical. It is intentionally permissive about
// ordering but expects the section titles authored in content/jentic.md.
func parseCanonical(src string) (Canonical, error) {
	c := Canonical{Name: "jentic", Version: "1"}

	body, fm := splitFrontmatter(src)
	for k, v := range fm {
		switch k {
		case "name":
			c.Name = v
		case "description":
			c.Description = v
		case "version":
			c.Version = v
		}
	}

	sections := splitSections(body)
	c.WhenToUse = bullets(sections["When to Use"])
	c.Prereqs = bullets(sections["Prerequisites"])
	c.QuickRef = bullets(sections["Quick Reference"])
	c.Pitfalls = bullets(sections["Pitfalls"])
	c.Verify = bullets(sections["Verification"])
	c.Steps = parseSteps(sections["Procedure"])

	if c.Description == "" {
		return c, errors.New("missing description in frontmatter")
	}
	if len(c.Steps) == 0 {
		return c, errors.New("no procedure steps found")
	}
	return c, nil
}

// splitFrontmatter separates a leading `---`-delimited YAML block (simple
// key: value pairs only) from the markdown body. The closing fence is only
// honored as a line that is exactly `---`, so a `---` thematic break or YAML
// embedded in the body does not truncate the frontmatter early.
func splitFrontmatter(src string) (body string, fm map[string]string) {
	fm = map[string]string{}
	s := strings.TrimLeft(src, "\n")
	if !strings.HasPrefix(s, "---\n") {
		return src, fm
	}
	rest := s[len("---\n"):]
	fenceStart, fenceEnd := closingFence(rest)
	if fenceStart < 0 {
		return src, fm
	}
	for _, line := range strings.Split(rest[:fenceStart], "\n") {
		k, v, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		fm[strings.TrimSpace(k)] = strings.TrimSpace(v)
	}
	return strings.TrimLeft(rest[fenceEnd:], "\n"), fm
}

// closingFence locates the YAML frontmatter terminator: a line consisting
// solely of `---`. It returns the byte offset where that line starts (so the
// frontmatter content is s[:start]) and where it ends (so the body is
// s[end:]). Both are -1 when no such line exists.
func closingFence(s string) (start, end int) {
	offset := 0
	for _, line := range strings.SplitAfter(s, "\n") {
		if strings.TrimRight(line, "\n") == "---" {
			return offset, offset + len(line)
		}
		offset += len(line)
	}
	return -1, -1
}

// splitSections maps each `## Heading` to the raw text beneath it (up to the
// next `## ` heading). The top-level `# Title` is ignored.
func splitSections(body string) map[string]string {
	out := map[string]string{}
	var cur string
	var buf strings.Builder
	flush := func() {
		if cur != "" {
			out[cur] = strings.Trim(buf.String(), "\n")
		}
		buf.Reset()
	}
	sc := bufio.NewScanner(strings.NewReader(body))
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := sc.Text()
		if h, ok := strings.CutPrefix(line, "## "); ok {
			flush()
			cur = strings.TrimSpace(h)
			continue
		}
		if cur != "" {
			buf.WriteString(line)
			buf.WriteByte('\n')
		}
	}
	flush()
	return out
}

// bullets extracts "- " list items from a section, joining wrapped lines.
func bullets(section string) []string {
	var out []string
	for _, raw := range strings.Split(section, "\n") {
		line := strings.TrimRight(raw, " ")
		item, ok := strings.CutPrefix(line, "- ")
		if ok {
			out = append(out, strings.TrimSpace(item))
			continue
		}
		// Continuation of the previous bullet (indented wrap).
		if len(out) > 0 && strings.HasPrefix(raw, "  ") {
			out[len(out)-1] += " " + strings.TrimSpace(raw)
		}
	}
	return out
}

// parseSteps reads `### N. Title` subsections within the Procedure section into
// ordered Steps, preserving each step's markdown body (including fenced code).
func parseSteps(section string) []Step {
	var steps []Step
	var cur *Step
	var buf strings.Builder
	flush := func() {
		if cur != nil {
			cur.Body = strings.Trim(buf.String(), "\n")
			steps = append(steps, *cur)
		}
		buf.Reset()
	}
	sc := bufio.NewScanner(strings.NewReader(section))
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := sc.Text()
		if h, ok := strings.CutPrefix(line, "### "); ok {
			flush()
			title := strings.TrimSpace(h)
			// Drop a leading "N. " ordinal if present.
			if _, after, ok := strings.Cut(title, ". "); ok {
				title = after
			}
			cur = &Step{Title: title}
			continue
		}
		if cur != nil {
			buf.WriteString(line)
			buf.WriteByte('\n')
		}
	}
	flush()
	return steps
}
