package api

// mcp_resources_skew_test.go pins the hosted-skill version-skew gate (#1343):
// a hosted skill document only serves when every pointer in it resolves to a
// document this binary can serve, so a backend newer than the CLI can never
// route a stdio MCP session into resource-not-found.

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/internal/skillgen"
)

// hostedRouter builds a hosted jentic document whose body carries the given
// pointer prose.
func hostedRouter(name, body string) string {
	return "---\nname: " + name + "\ndescription: hosted router\nversion: 99\n---\n\n# Using Jentic\n\n" + body + "\n"
}

func TestMCPSkillResource_HostedPointerGate(t *testing.T) {
	for _, tc := range []struct {
		name       string
		status     int
		body       string
		wantHosted bool
		wantLog    string // substring of the fallback log line; "" when hosted serves
	}{
		{
			name:   "servable absolute and relative pointers serve hosted",
			status: http.StatusOK,
			body: hostedRouter("jentic", "MCP session: read skill://jentic/references/mcp.md. "+
				"CLI session: read `references/cli.md`. Recovery: skill://jentic/references/recovery.md, "+
				"`references/recovery.md`. Index: skill://index. Placeholder prose skill://<name> is not a pointer."),
			wantHosted: true,
		},
		{
			name:       "no pointers at all serves hosted",
			status:     http.StatusOK,
			body:       hostedRouter("jentic", "Plain body."),
			wantHosted: true,
		},
		{
			name:    "unknown skill:// reference falls back",
			status:  http.StatusOK,
			body:    hostedRouter("jentic", "Read skill://jentic/references/http.md first."),
			wantLog: "skill://jentic/references/http.md",
		},
		{
			name:    "unknown relative reference falls back",
			status:  http.StatusOK,
			body:    hostedRouter("jentic", "Read `references/a2a.md` for the A2A lane."),
			wantLog: "references/a2a.md",
		},
		{
			name:    "nested relative reference falls back",
			status:  http.StatusOK,
			body:    hostedRouter("jentic", "Read `references/v2/mcp.md` for the MCP lane."),
			wantLog: "references/v2/mcp.md",
		},
		{
			name:    "non-markdown relative reference falls back",
			status:  http.StatusOK,
			body:    hostedRouter("jentic", "Schemas live in references/tools.json."),
			wantLog: "references/tools.json",
		},
		{
			name:    "unknown skill:// skill falls back",
			status:  http.StatusOK,
			body:    hostedRouter("jentic", "See skill://brand-new-skill for more."),
			wantLog: "skill://brand-new-skill",
		},
		{
			// The CLI-lane file ships but is never registered on this MCP
			// server, so an absolute pointer to it is a dead end here.
			name:    "skill:// pointer to the unlisted CLI lane falls back",
			status:  http.StatusOK,
			body:    hostedRouter("jentic", "Read skill://jentic/references/cli.md."),
			wantLog: "skill://jentic/references/cli.md",
		},
		{
			name:    "name mismatch falls back",
			status:  http.StatusOK,
			body:    hostedRouter("other", "Plain body."),
			wantLog: "mismatched frontmatter name",
		},
		{
			name:    "fetch error falls back",
			status:  http.StatusInternalServerError,
			wantLog: "backend answered 500",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()

			var logs bytes.Buffer
			s := newTestMCPServer(t, nil)
			s.logger = slog.New(slog.NewTextHandler(&logs, &slog.HandlerOptions{Level: slog.LevelDebug}))

			res, err := s.handleSkillResource(activeCtx(srv.URL), readResourceRequest("skill://jentic"))
			if err != nil {
				t.Fatalf("handleSkillResource must never fail on a hosted problem: %v", err)
			}
			if tc.wantHosted {
				c := singleContents(t, res)
				if c.Meta[skillMetaSource] != string(skillgen.SourceHosted) {
					t.Fatalf("meta source = %v, want hosted; logs:\n%s", c.Meta[skillMetaSource], logs.String())
				}
				if c.Text != tc.body {
					t.Errorf("text = %q, want the hosted bytes verbatim", c.Text)
				}
				if c.Meta[skillMetaVersion] != "99" {
					t.Errorf("meta version = %v, want the hosted document's 99", c.Meta[skillMetaVersion])
				}
				if strings.Contains(logs.String(), "serving bundled") {
					t.Errorf("a hosted serve must not log a fallback:\n%s", logs.String())
				}
				return
			}
			assertBundledJentic(t, res)
			fallbacks := strings.Count(logs.String(), "hosted copy unusable, serving bundled")
			if fallbacks != 1 {
				t.Errorf("fallback log lines = %d, want exactly 1:\n%s", fallbacks, logs.String())
			}
			if !strings.Contains(logs.String(), tc.wantLog) {
				t.Errorf("fallback log must say why (%q):\n%s", tc.wantLog, logs.String())
			}
		})
	}
}

// TestUnservablePointer_Parsing pins the pointer regexes' edges: sentence
// punctuation, code spans, and paths that merely contain "references/" must
// not produce false positives or false negatives.
func TestUnservablePointer_Parsing(t *testing.T) {
	for _, tc := range []struct {
		body string
		want string
	}{
		{"read skill://jentic/references/mcp.md.", ""},
		{"read (skill://jentic/references/mcp.md), then skill://jentic", ""},
		{"see `skill://jentic/references/mcp.md`", ""},
		{"see docs/references/nope.md and https://x.example/references/nope.md", ""},
		{"placeholder skill://<name>/references/<file>", ""},
		{"read references/nope.md.", "references/nope.md"},
		{"read skill://jentic/references/nope.md.", "skill://jentic/references/nope.md"},
		{"read skill://jentic/nope", "skill://jentic/nope"},
		// The full relative grammar: nested, non-markdown, extensionless and
		// traversal targets are all matched (so they are checked, never
		// skipped), each without its trailing punctuation.
		{"read `references/v2/mcp.md`.", "references/v2/mcp.md"},
		{"read references/mcp.md/extra.", "references/mcp.md/extra"},
		{"read references/schema.json.", "references/schema.json"},
		{"read references/mcp-tools.", "references/mcp-tools"},
		{"read references/../jentic.md", "references/../jentic.md"},
		{"read references/./mcp.md", "references/./mcp.md"},
		{"see references/v2/ for more", "references/v2"},
		// False-positive guards: a bare directory mention or trailing
		// punctuation is not a pointer.
		{"the `references/` files next to it", ""},
		{"see references/.", ""},
		{"see references/mcp.md, references/cli.md and references/recovery.md.", ""},
	} {
		if got := unservablePointer("jentic", []byte(tc.body)); got != tc.want {
			t.Errorf("unservablePointer(%q) = %q, want %q", tc.body, got, tc.want)
		}
	}
}

// TestBundledSkills_PassHostedGate pins self-consistency: every document this
// binary embeds passes the gate it applies to hosted copies. Without it, a
// same-version backend (which serves these exact bytes) would always be
// refused, and an embed that points at a reference it does not ship would go
// unnoticed.
func TestBundledSkills_PassHostedGate(t *testing.T) {
	for _, name := range skillgen.BundledNames() {
		raw, err := skillgen.RawBundled(name)
		if err != nil {
			t.Fatalf("RawBundled(%s): %v", name, err)
		}
		if err := validateHostedSkill(name, raw); err != nil {
			t.Errorf("bundled %s fails the hosted gate: %v", name, err)
		}
	}
	// The manifest a same-version backend serves lists exactly the embed's
	// skills and references; it must pass the index gate too.
	names := skillgen.BundledNames()
	rows := make([]map[string]any, 0, len(names))
	for _, name := range names {
		refs := make([]map[string]string, 0)
		for _, ref := range skillgen.BundledReferences(name) {
			refs = append(refs, map[string]string{"name": ref})
		}
		rows = append(rows, map[string]any{"name": name, "references": refs})
	}
	manifest, err := json.Marshal(rows)
	if err != nil {
		t.Fatalf("marshal manifest: %v", err)
	}
	if err := validateHostedIndex(manifest); err != nil {
		t.Errorf("a same-version manifest fails the hosted index gate: %v", err)
	}
}

// TestMCPSkillIndex_HostedSurfaceGate pins the index half of the skew
// gate: a hosted manifest serves only when every skill and reference it
// lists ships in this binary; a newer backend's manifest falls back to the
// bundled one, so skill://index never advertises documents this server can
// only serve bundled.
func TestMCPSkillIndex_HostedSurfaceGate(t *testing.T) {
	for _, tc := range []struct {
		name       string
		body       string
		wantHosted bool
		wantLog    string
	}{
		{
			name: "subset of the embed serves hosted",
			body: `[{"name":"jentic","version":"7","references":[{"name":"cli.md"},{"name":"mcp.md"},{"name":"recovery.md"}],"future_key":1},` +
				`{"name":"import-new-api","version":"1"}]`,
			wantHosted: true,
		},
		{
			name:    "unknown skill falls back",
			body:    `[{"name":"jentic"},{"name":"brand-new-skill"}]`,
			wantLog: "brand-new-skill",
		},
		{
			name:    "unknown reference falls back",
			body:    `[{"name":"jentic","references":[{"name":"mcp.md"},{"name":"http.md"}]}]`,
			wantLog: "jentic/references/http.md",
		},
		{
			name:    "rows of the wrong shape fall back",
			body:    `[{"name":7}]`,
			wantLog: "rows are malformed",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()

			var logs bytes.Buffer
			s := newTestMCPServer(t, nil)
			s.logger = slog.New(slog.NewTextHandler(&logs, &slog.HandlerOptions{Level: slog.LevelDebug}))

			res, err := s.handleSkillIndexResource(activeCtx(srv.URL), readResourceRequest(skillIndexURI))
			if err != nil {
				t.Fatalf("handleSkillIndexResource must never fail on a hosted problem: %v", err)
			}
			c := singleContents(t, res)
			if tc.wantHosted {
				if c.Meta[skillMetaSource] != string(skillgen.SourceHosted) || c.Text != tc.body {
					t.Fatalf("want the hosted manifest verbatim (source %v); logs:\n%s", c.Meta[skillMetaSource], logs.String())
				}
				return
			}
			want, err := bundledSkillIndex()
			if err != nil {
				t.Fatalf("bundledSkillIndex: %v", err)
			}
			if c.Meta[skillMetaSource] != string(skillgen.SourceBundled) || c.Text != string(want) {
				t.Errorf("want the bundled manifest (source %v)", c.Meta[skillMetaSource])
			}
			if n := strings.Count(logs.String(), "hosted copy unusable, serving bundled manifest"); n != 1 {
				t.Errorf("fallback log lines = %d, want exactly 1:\n%s", n, logs.String())
			}
			if !strings.Contains(logs.String(), tc.wantLog) {
				t.Errorf("fallback log must say why (%q):\n%s", tc.wantLog, logs.String())
			}
		})
	}
}

// TestMCPSession_ServedSkillPointersResolve pins the end-to-end contract over
// the wire: whichever copy skill://jentic serves (hosted when coherent,
// bundled when the hosted router has outrun this binary), every skill://
// pointer in the served text is listed by resources/list and readable by
// resources/read, and skill://index reports the same source as the document.
// The backend double is coherent the way a real one is: a router pointing at
// a new reference comes with a manifest listing it.
func TestMCPSession_ServedSkillPointersResolve(t *testing.T) {
	for _, tc := range []struct {
		name       string
		body       string
		index      string
		wantSource skillgen.Source
	}{
		{
			name:       "coherent hosted router",
			body:       hostedRouter("jentic", "MCP session: read skill://jentic/references/mcp.md and skill://jentic/references/recovery.md."),
			index:      `[{"name":"jentic","references":[{"name":"cli.md"},{"name":"mcp.md"},{"name":"recovery.md"}]}]`,
			wantSource: skillgen.SourceHosted,
		},
		{
			name:       "hosted router ahead of this binary",
			body:       hostedRouter("jentic", "MCP session: read skill://jentic/references/mcp-v2.md."),
			index:      `[{"name":"jentic","references":[{"name":"cli.md"},{"name":"mcp-v2.md"},{"name":"recovery.md"}]}]`,
			wantSource: skillgen.SourceBundled,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/skills/jentic.md":
					_, _ = w.Write([]byte(tc.body))
				case "/skills/index.json":
					_, _ = w.Write([]byte(tc.index))
				default:
					http.NotFound(w, r)
				}
			}))
			defer srv.Close()

			s := newTestMCPServer(t, nil)
			cs := connectTestClientCtx(activeCtx(srv.URL), t, s)
			ctx := context.Background()

			list, err := cs.ListResources(ctx, nil)
			if err != nil {
				t.Fatalf("resources/list: %v", err)
			}
			listed := make(map[string]bool, len(list.Resources))
			for _, r := range list.Resources {
				listed[r.URI] = true
			}

			res, err := cs.ReadResource(ctx, &mcp.ReadResourceParams{URI: "skill://jentic"})
			if err != nil {
				t.Fatalf("resources/read skill://jentic: %v", err)
			}
			c := singleContents(t, res)
			if c.Meta[skillMetaSource] != string(tc.wantSource) {
				t.Fatalf("meta source = %v, want %s", c.Meta[skillMetaSource], tc.wantSource)
			}
			idx, err := cs.ReadResource(ctx, &mcp.ReadResourceParams{URI: skillIndexURI})
			if err != nil {
				t.Fatalf("resources/read skill://index: %v", err)
			}
			if got := singleContents(t, idx).Meta[skillMetaSource]; got != string(tc.wantSource) {
				t.Errorf("index source = %v, want %s (same source as the skill document it indexes)", got, tc.wantSource)
			}

			ptrs := skillPointerRE.FindAllString(c.Text, -1)
			if tc.wantSource == skillgen.SourceHosted && len(ptrs) == 0 {
				t.Fatalf("test body must carry skill:// pointers to exercise the contract")
			}
			for _, ptr := range ptrs {
				if !listed[ptr] {
					t.Errorf("served doc points at %q, which resources/list does not list", ptr)
					continue
				}
				if _, err := cs.ReadResource(ctx, &mcp.ReadResourceParams{URI: ptr}); err != nil {
					t.Errorf("served doc points at %q, which resources/read refuses: %v", ptr, err)
				}
			}
			// Relative pointers in the served doc name references this
			// binary ships for the skill.
			shipped := skillgen.BundledReferences("jentic")
			for _, m := range relativeReferenceRE.FindAllStringSubmatch(c.Text, -1) {
				if !slices.Contains(shipped, m[1]) {
					t.Errorf("served doc points at references/%s, which this binary does not ship", m[1])
				}
			}
		})
	}
}
