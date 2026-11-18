package clitree

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// committedCLIMarkdown is the path to the generated, committed Markdown
// reference GitHub readers browse.
const committedCLIMarkdown = "../../../docs/reference/cli.md"

// TestCommittedCLIMarkdownUpToDate fails if docs/reference/cli.md drifts from
// the cobra definitions. Regenerate with `make cli-reference`.
func TestCommittedCLIMarkdownUpToDate(t *testing.T) {
	committed, err := os.ReadFile(filepath.Clean(committedCLIMarkdown))
	if err != nil {
		t.Fatalf("read committed Markdown reference: %v (run `make cli-reference`)", err)
	}
	if !bytes.Equal(committed, RenderMarkdown(BuildCLIReference())) {
		t.Errorf("docs/reference/cli.md is out of date with the CLI command tree.\n" +
			"Regenerate with `make cli-reference` and commit the result.")
	}
}

// TestRenderMarkdownLiveTree checks structural properties of the real
// reference: a section per binary, the root group titles as headings in
// declaration order, and no hidden helper commands.
func TestRenderMarkdownLiveTree(t *testing.T) {
	ref := BuildCLIReference()
	md := string(RenderMarkdown(ref))

	if !strings.HasPrefix(md, MarkdownHeader) {
		t.Error("rendered Markdown does not open with the generated-file header")
	}
	for _, bin := range ref.Binaries {
		if !strings.Contains(md, "\n## "+bin.Name+"\n") {
			t.Errorf("missing section for binary %q", bin.Name)
		}
		last := -1
		for _, title := range bin.GroupTitles {
			i := strings.Index(md, "\n### "+title+"\n")
			if i < 0 {
				t.Errorf("%s: missing group heading %q", bin.Name, title)
				continue
			}
			if i < last {
				t.Errorf("%s: group %q out of root --help order", bin.Name, title)
			}
			last = i
		}
	}
	for _, hidden := range []string{"#### jentic completion", "#### jentic help", "#### jenticctl help"} {
		if strings.Contains(md, hidden+"\n") {
			t.Errorf("rendered Markdown documents helper command %q", hidden)
		}
	}
}

func TestInlineEscaping(t *testing.T) {
	cases := []struct {
		name    string
		in      string
		inTable bool
		want    string
	}{
		{"plain", "list things", false, "list things"},
		{"html-like", "show <id>", false, "show &lt;id&gt;"},
		{"emphasis", "jak_* keys", false, `jak_\* keys`},
		{"dollar", "$JENTIC_MODE", false, `\$JENTIC_MODE`},
		{"pipe outside table", "a|b", false, "a|b"},
		{"pipe in table", "a|b", true, `a\|b`},
		{"code span kept", "run `jentic <cmd>`", false, "run `jentic <cmd>`"},
		{"pipe in code span in table", "`a|b` <x>", true, "`a\\|b` &lt;x&gt;"},
		{"unbalanced backtick", "a ` <b>", false, "a ` &lt;b&gt;"},
		{"newlines fold", "one\n  two", false, "one two"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := inline(tc.in, tc.inTable); got != tc.want {
				t.Errorf("inline(%q, %v) = %q, want %q", tc.in, tc.inTable, got, tc.want)
			}
		})
	}
}

func TestWriteFenceOutlastsBackticks(t *testing.T) {
	var b strings.Builder
	writeFence(&b, "text", "has ``` inside")
	want := "````text\nhas ``` inside\n````\n\n"
	if b.String() != want {
		t.Errorf("writeFence = %q, want %q", b.String(), want)
	}
}
