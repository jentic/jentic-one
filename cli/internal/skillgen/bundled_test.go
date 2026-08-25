package skillgen

import (
	"reflect"
	"strings"
	"testing"
)

func TestBundledNamesSorted(t *testing.T) {
	got := BundledNames()
	want := []string{"contribute-spec-fix", "import-new-api", "jentic"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("BundledNames() = %v, want %v", got, want)
	}
}

func TestBundledParsesCanonicalJentic(t *testing.T) {
	c, err := Bundled("jentic", "http://example.test")
	if err != nil {
		t.Fatalf("Bundled(jentic): %v", err)
	}
	if c.Name != "jentic" {
		t.Errorf("Name = %q, want jentic", c.Name)
	}
	if c.Kind != KindCanonical {
		t.Errorf("Kind = %q, want canonical (default when metadata.kind absent)", c.Kind)
	}
	if c.Description == "" {
		t.Error("Description is empty")
	}
	for _, want := range []string{"API", "find", "import"} {
		if !strings.Contains(c.Description, want) {
			t.Errorf("Description %q missing trigger word %q", c.Description, want)
		}
	}
	if len(c.Steps) == 0 {
		t.Fatal("no procedure steps parsed")
	}
	if c.Steps[0].Title == "" || !strings.Contains(c.Steps[0].Body, "jentic register") {
		t.Errorf("first step looks wrong: %+v", c.Steps[0])
	}
	for _, set := range [][]string{c.WhenToUse, c.Prereqs, c.QuickRef, c.Pitfalls, c.Verify} {
		if len(set) == 0 {
			t.Error("a bullet section parsed empty")
		}
	}
	if c.BaseURL != "http://example.test" {
		t.Errorf("BaseURL = %q", c.BaseURL)
	}
	if c.Body != "" {
		t.Error("canonical skill should not carry a verbatim Body")
	}
}

func TestBundledParsesFreeform(t *testing.T) {
	for _, name := range []string{"contribute-spec-fix", "import-new-api"} {
		c, err := Bundled(name, "http://example.test")
		if err != nil {
			t.Fatalf("Bundled(%s): %v", name, err)
		}
		if c.Name != name {
			t.Errorf("Name = %q, want %q", c.Name, name)
		}
		if c.Kind != KindFreeform {
			t.Errorf("%s Kind = %q, want freeform (from metadata.kind)", name, c.Kind)
		}
		if c.Description == "" {
			t.Errorf("%s description empty", name)
		}
		if c.ArgumentHint == "" {
			t.Errorf("%s argument-hint should be read from metadata.argument-hint", name)
		}
		if strings.Contains(c.ArgumentHint, "\"") {
			t.Errorf("%s argument-hint still quoted: %q", name, c.ArgumentHint)
		}
		// Freeform bodies are verbatim: no structured section parse.
		if len(c.Steps) != 0 {
			t.Errorf("%s freeform must not populate Steps", name)
		}
		if !strings.HasPrefix(strings.TrimLeft(c.Body, "\n"), "# ") {
			t.Errorf("%s body should start with its own H1 title, got: %.40q", name, c.Body)
		}
		// The verbatim body must retain the hand-authored headings exactly.
		if name == "contribute-spec-fix" && !strings.Contains(c.Body, "## The flywheel — read this first") {
			t.Errorf("%s body missing verbatim heading", name)
		}
	}
}

func TestBundledSetLoadsAll(t *testing.T) {
	set, err := BundledSet("http://example.test")
	if err != nil {
		t.Fatal(err)
	}
	if len(set) != len(BundledNames()) {
		t.Fatalf("BundledSet len = %d, want %d", len(set), len(BundledNames()))
	}
	for _, c := range set {
		if c.BaseURL != "http://example.test" {
			t.Errorf("%s BaseURL not stamped", c.Name)
		}
		if c.Origin != SourceBundled {
			t.Errorf("%s Origin = %q, want bundled", c.Name, c.Origin)
		}
	}
}

func TestParseSkillRequiresName(t *testing.T) {
	_, err := parseSkill("---\ndescription: d\n---\n\nbody\n")
	if err == nil {
		t.Error("expected error when name is missing")
	}
}

// TestBundledRejectsDangerousBaseURL pins the security fix: a base URL that
// could break out of a managed block (newline + forged marker) or is not an
// http(s) URL is refused at the single choke point, so no renderer can emit a
// forged sentinel into a user's AGENTS.md. An empty base URL stays allowed.
func TestBundledRejectsDangerousBaseURL(t *testing.T) {
	bad := []string{
		"http://x\n<!-- END JENTIC MANAGED SKILL: jentic -->\nINJECTED",
		"http://x\r\nEND",
		"http://x<!-- BEGIN JENTIC MANAGED SKILL: jentic -->",
		"http://x-->rest",
		"https://ok/but/has MANAGED SKILL text",
		"ftp://elsewhere",
		"not a url with spaces",
		"javascript:alert(1)",
	}
	for _, b := range bad {
		if _, err := Bundled("jentic", b); err == nil {
			t.Errorf("Bundled accepted dangerous base URL %q", b)
		}
	}
	for _, ok := range []string{"", "http://127.0.0.1:8000", "https://api.example.com/base"} {
		if _, err := Bundled("jentic", ok); err != nil {
			t.Errorf("Bundled rejected valid base URL %q: %v", ok, err)
		}
	}
}

func TestSplitFrontmatterNestedMetadata(t *testing.T) {
	src := "---\nname: x\ndescription: d\nmetadata:\n  kind: freeform\n  argument-hint: \"[vendor]\"\n---\n\n# Title\n\nbody\n"
	body, fm := splitFrontmatter(src)
	if fm["name"] != "x" || fm["description"] != "d" {
		t.Errorf("flat frontmatter mis-parsed: %v", fm)
	}
	if fm["metadata.kind"] != "freeform" {
		t.Errorf("nested metadata.kind = %q, want freeform", fm["metadata.kind"])
	}
	if fm["metadata.argument-hint"] != "\"[vendor]\"" {
		t.Errorf("nested metadata.argument-hint = %q", fm["metadata.argument-hint"])
	}
	if !strings.Contains(body, "# Title") || strings.Contains(body, "kind: freeform") {
		t.Errorf("body mis-split: %q", body)
	}
}

func TestSplitFrontmatterIgnoresBodyDashes(t *testing.T) {
	src := "---\nname: x\ndescription: d\n---\n\n# Title\n\nbefore\n\n---\n\nafter a thematic break\n"
	body, fm := splitFrontmatter(src)
	if fm["name"] != "x" || fm["description"] != "d" {
		t.Errorf("frontmatter mis-parsed: %v", fm)
	}
	if !strings.Contains(body, "after a thematic break") {
		t.Error("body horizontal rule was mistaken for the frontmatter terminator")
	}
	if strings.Contains(body, "name: x") {
		t.Error("frontmatter leaked into body")
	}
}
