package skillgen

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode/utf8"
)

func testContent() Canonical {
	return testContentT(nil)
}

// testContentT parses the bundled content, failing the test on error. A nil t
// is allowed for callers that treat a malformed embed as a panic-worthy
// programming error.
func testContentT(t *testing.T) Canonical {
	c, err := Bundled("http://example.test")
	if err != nil {
		if t != nil {
			t.Fatalf("Bundled: %v", err)
		}
		panic("testContent: " + err.Error())
	}
	return c
}

func TestBundledParsesCanonical(t *testing.T) {
	c := testContent()
	if c.Name != "jentic" {
		t.Errorf("Name = %q, want jentic", c.Name)
	}
	if c.Description == "" {
		t.Error("Description is empty")
	}
	// The canonical description is intentionally rich (claude/cursor/codex read
	// it in full to decide whether to launch the skill); only hermes truncates
	// it. Assert it carries the key trigger words rather than capping its length.
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
}

func TestRenderBodyIncludesBaseURLAndSections(t *testing.T) {
	body := renderBody(testContent())
	for _, want := range []string{
		"# Using Jentic from the CLI",
		"http://example.test",
		"## When to Use",
		"## Procedure",
		"### 1. Confirm you have a valid identity",
		"### 2. Check what you can do, and request access if needed",
		"jentic access request",
		"credential_not_provisioned",
		"credential_identity_mismatch",
		"provisioning_url",
		"exits 2",
		"## Verification",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("rendered body missing %q", want)
		}
	}
}

func TestManagedBlockRoundTrip(t *testing.T) {
	body := "hello\nworld\n"
	blkText := managedBlock(body, SourceBundled)
	if !strings.Contains(blkText, beginMarker) || !strings.Contains(blkText, endMarker) {
		t.Fatal("markers missing")
	}
	blk := findBlock([]byte(blkText))
	if !blk.found {
		t.Fatal("findBlock did not locate the block")
	}
	if blk.source != string(SourceBundled) {
		t.Errorf("source = %q", blk.source)
	}
	got := currentBlockBody([]byte(blkText), blk)
	if hashContent(got) != blk.hash {
		t.Errorf("re-extracted body hash %q != recorded %q", hashContent(got), blk.hash)
	}
}

func TestSpliceCreatesPreservesAndReplaces(t *testing.T) {
	// New file: just the block.
	r1 := splice(nil, "body one\n", SourceBundled)
	if !r1.created || !r1.changed {
		t.Fatalf("new file should be created+changed: %+v", r1)
	}

	// Existing user content: block appended, user content kept.
	existing := []byte("# user heading\n\nsome notes\n")
	r2 := splice(existing, "body one\n", SourceBundled)
	if !strings.Contains(string(r2.out), "# user heading") || !strings.Contains(string(r2.out), "some notes") {
		t.Error("user content not preserved on splice")
	}
	if !strings.Contains(string(r2.out), beginMarker) {
		t.Error("managed block not added")
	}

	// Re-splice identical content: no change.
	r3 := splice(r2.out, "body one\n", SourceBundled)
	if r3.changed {
		t.Error("identical re-splice should be a no-op")
	}

	// New content: changed, user content still preserved.
	r4 := splice(r2.out, "body two\n", SourceBundled)
	if !r4.changed {
		t.Error("changed content should splice")
	}
	if !strings.Contains(string(r4.out), "some notes") || !strings.Contains(string(r4.out), "body two") {
		t.Error("user content lost or new content missing")
	}
	if strings.Contains(string(r4.out), "body one") {
		t.Error("old managed body should be gone")
	}
}

func TestSpliceDetectsUserEdits(t *testing.T) {
	r := splice(nil, "original\n", SourceBundled)
	// Tamper inside the block.
	tampered := strings.Replace(string(r.out), "original", "tampered", 1)
	res := splice([]byte(tampered), "original\n", SourceBundled)
	if !res.userEdits {
		t.Error("expected userEdits to be detected on tampered block")
	}
}

func TestRegistryResolveAndDetect(t *testing.T) {
	reg := DefaultRegistry()
	if _, ok := reg.Resolve("claude-code"); !ok {
		t.Error("alias claude-code should resolve")
	}
	if _, ok := reg.Resolve("CURSOR"); !ok {
		t.Error("resolve should be case-insensitive")
	}
	resolved, unknown := reg.ResolveAll([]string{"claude", "nope", "cursor"})
	if len(unknown) != 1 || unknown[0] != "nope" {
		t.Errorf("unknown = %v", unknown)
	}
	if len(resolved) != 2 {
		t.Errorf("resolved %d adapters, want 2", len(resolved))
	}

	env := DetectEnv{
		Home:   "/home/u",
		Cwd:    "/proj",
		Lookup: func(string) bool { return false },
		Stat:   func(p string) bool { return p == "/proj/.cursor" },
	}
	det := reg.Detected(env)
	if len(det) != 1 || det[0].Operator() != OpCursor {
		t.Errorf("Detected = %v, want [cursor]", det)
	}
}

func TestAdapterTargets(t *testing.T) {
	env := DetectEnv{Home: "/home/u", Cwd: "/proj"}
	reg := DefaultRegistry()
	cases := map[Operator]string{
		OpClaude:  "/home/u/.claude/skills/jentic/SKILL.md",
		OpCursor:  "/home/u/.cursor/skills/jentic/SKILL.md",
		OpHermes:  "/home/u/.hermes/skills/api/jentic/SKILL.md",
		OpCodex:   "/proj/AGENTS.md",
		OpGeneric: "/proj/AGENTS.md",
	}
	for op, want := range cases {
		ad, _ := reg.Resolve(string(op))
		if got := ad.Target(ad.DefaultScope(), env); got != want {
			t.Errorf("%s target = %q, want %q", op, got, want)
		}
	}
}

func TestApplyAndRemoveSingleFile(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("generic")
	c := testContent()

	// Dry run writes nothing.
	out, err := Apply(ad, c, env, ApplyOptions{DryRun: true})
	if err != nil {
		t.Fatal(err)
	}
	if !out.Changed || !out.Created {
		t.Errorf("dry-run outcome = %+v", out)
	}
	if _, err := os.Stat(out.Path); !os.IsNotExist(err) {
		t.Error("dry run should not write the file")
	}

	// Real write.
	out, err = Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !out.Created {
		t.Error("first apply should create")
	}
	data, _ := os.ReadFile(out.Path)
	if !strings.Contains(string(data), beginMarker) {
		t.Error("managed block not written")
	}

	// Idempotent re-apply.
	out2, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if out2.Changed || !out2.Skipped {
		t.Errorf("re-apply should be skipped: %+v", out2)
	}

	// Remove.
	rout, err := Remove(ad, env, RemoveOptions{})
	if err != nil || !rout.Removed {
		t.Fatalf("remove failed: removed=%v err=%v", rout.Removed, err)
	}
	if _, err := os.Stat(rout.Path); !os.IsNotExist(err) {
		t.Error("AGENTS.md should be gone after removing the only block")
	}
}

func TestApplyRefusesUserEditsWithoutForce(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("generic")
	c := testContent()

	if _, err := Apply(ad, c, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	target := ad.Target(ad.DefaultScope(), env)
	data, _ := os.ReadFile(target)
	tampered := strings.Replace(string(data), "audited broker", "TAMPERED", 1)
	if err := os.WriteFile(target, []byte(tampered), 0o644); err != nil {
		t.Fatal(err)
	}

	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !out.UserEdits {
		t.Fatal("expected UserEdits without --force")
	}
	cur, _ := os.ReadFile(target)
	if !strings.Contains(string(cur), "TAMPERED") {
		t.Error("tampered content should be left untouched without --force")
	}

	out, err = Apply(ad, c, env, ApplyOptions{Force: true})
	if err != nil {
		t.Fatal(err)
	}
	if !out.Changed {
		t.Error("--force should overwrite")
	}
	cur, _ = os.ReadFile(target)
	if strings.Contains(string(cur), "TAMPERED") {
		t.Error("--force should have replaced the tampered block")
	}
}

func TestApplyClaudeDirAndRemovePrunes(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	c := testContent()

	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !out.Created {
		t.Error("claude apply should create SKILL.md")
	}
	if filepath.Base(out.Path) != "SKILL.md" {
		t.Errorf("unexpected target %q", out.Path)
	}
	data, _ := os.ReadFile(out.Path)
	if !strings.HasPrefix(string(data), "---\nname: jentic") {
		t.Error("claude SKILL.md missing frontmatter")
	}

	if rout, err := Remove(ad, env, RemoveOptions{}); err != nil || !rout.Removed {
		t.Fatalf("remove failed: %v %v", rout.Removed, err)
	}
	// The jentic skill dir should be pruned.
	if _, err := os.Stat(filepath.Dir(out.Path)); !os.IsNotExist(err) {
		t.Error("jentic skill dir should be pruned after removal")
	}
}

func TestApplyDedicatedDetectsFrontmatterEdit(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	c := testContent()

	if _, err := Apply(ad, c, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	target := ad.Target(ad.DefaultScope(), env)

	// Edit the frontmatter (outside the managed block). Because the block body
	// is untouched, a block-only hash check would miss this; the whole-file
	// guard must still flag it.
	data, _ := os.ReadFile(target)
	edited := strings.Replace(string(data), "name: jentic", "name: my-custom-name", 1)
	if edited == string(data) {
		t.Fatal("frontmatter edit did not change the file")
	}
	if err := os.WriteFile(target, []byte(edited), 0o644); err != nil {
		t.Fatal(err)
	}

	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !out.UserEdits {
		t.Fatal("expected UserEdits for a frontmatter edit on a dedicated target")
	}
	cur, _ := os.ReadFile(target)
	if !strings.Contains(string(cur), "my-custom-name") {
		t.Error("frontmatter edit should be preserved without --force")
	}

	// --force overwrites the frontmatter back to canonical.
	if _, err := Apply(ad, c, env, ApplyOptions{Force: true}); err != nil {
		t.Fatal(err)
	}
	cur, _ = os.ReadFile(target)
	if strings.Contains(string(cur), "my-custom-name") {
		t.Error("--force should have restored the canonical frontmatter")
	}
}

func TestApplyDedicatedDetectsTrailingEdit(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	c := testContent()

	if _, err := Apply(ad, c, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	target := ad.Target(ad.DefaultScope(), env)

	// Append prose *after* the managed block. The block body and the
	// frontmatter prelude are both untouched, so only the suffix check catches
	// this; without it the refresh would silently drop the user's notes.
	data, _ := os.ReadFile(target)
	withSuffix := string(data) + "\n## My own notes\n\nkeep me around\n"
	if err := os.WriteFile(target, []byte(withSuffix), 0o644); err != nil {
		t.Fatal(err)
	}

	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !out.UserEdits {
		t.Fatal("expected UserEdits for trailing prose on a dedicated target")
	}
	cur, _ := os.ReadFile(target)
	if !strings.Contains(string(cur), "keep me around") {
		t.Error("trailing user prose should be preserved without --force")
	}
}

func TestRemoveRefusesEditedBlockWithoutForce(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("generic")
	c := testContent()

	if _, err := Apply(ad, c, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	target := ad.Target(ad.DefaultScope(), env)

	// Tamper inside the managed block so its recorded hash no longer matches.
	data, _ := os.ReadFile(target)
	tampered := strings.Replace(string(data), "audited broker", "TAMPERED", 1)
	if tampered == string(data) {
		t.Fatal("tamper did not change the block body")
	}
	if err := os.WriteFile(target, []byte(tampered), 0o644); err != nil {
		t.Fatal(err)
	}

	// Without --force, remove must refuse and leave the file intact.
	out, err := Remove(ad, env, RemoveOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !out.UserEdits || out.Removed {
		t.Fatalf("expected refusal (UserEdits) without --force: %+v", out)
	}
	if _, err := os.Stat(target); err != nil {
		t.Error("file should remain when removal is refused")
	}

	// --dry-run with --force reports it would remove but writes nothing.
	out, err = Remove(ad, env, RemoveOptions{Force: true, DryRun: true})
	if err != nil {
		t.Fatal(err)
	}
	if !out.Removed {
		t.Errorf("force+dry-run should report removable: %+v", out)
	}
	if _, err := os.Stat(target); err != nil {
		t.Error("dry-run must not delete the file")
	}

	// --force actually removes the edited block.
	out, err = Remove(ad, env, RemoveOptions{Force: true})
	if err != nil || !out.Removed {
		t.Fatalf("force remove failed: %+v err=%v", out, err)
	}
	if _, err := os.Stat(target); !os.IsNotExist(err) {
		t.Error("file should be gone after forced removal of the only block")
	}
}

func TestApplyWritesAtomically(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("generic")
	c := testContent()

	// Pre-create AGENTS.md with user content and a non-default mode so we can
	// assert the atomic write preserves both surrounding content and mode.
	target := ad.Target(ad.DefaultScope(), env)
	if err := os.WriteFile(target, []byte("# User notes\n\nkeep me\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if _, err := Apply(ad, c, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}

	cur, _ := os.ReadFile(target)
	if !strings.Contains(string(cur), "keep me") {
		t.Error("user content not preserved across atomic write")
	}
	if !strings.Contains(string(cur), beginMarker) {
		t.Error("managed block not written")
	}
	if info, _ := os.Stat(target); info.Mode().Perm() != 0o600 {
		t.Errorf("mode = %o, want 600 (atomic write should preserve existing mode)", info.Mode().Perm())
	}

	// No temp files should be left behind in the directory.
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".jentic-skill-") {
			t.Errorf("leftover temp file: %s", e.Name())
		}
	}
}

// TestFindBlockIgnoresMarkerInsideContent verifies a marker quoted inline in
// user prose is not mistaken for a real managed region.
func TestFindBlockIgnoresMarkerInsideContent(t *testing.T) {
	// A user documenting the marker inside a fenced code block (indented, so
	// not line-anchored) must not be mistaken for a real managed region.
	realBlock := splice(nil, "real body\n", SourceBundled).out
	doc := "# Notes\n\nExample marker: `" + beginMarker + "` (quoted inline, not anchored)\n\n"
	combined := []byte(doc + string(realBlock))
	blk := findBlock(combined)
	if !blk.found {
		t.Fatal("expected to find the real anchored block")
	}
	// The located block must be the genuine one (its body re-hashes to the
	// recorded hash), not the inline mention.
	if hashContent(currentBlockBody(combined, blk)) != blk.hash {
		t.Error("findBlock latched onto a non-anchored marker mention")
	}
}

func TestSpliceHandlesCRLF(t *testing.T) {
	lf := splice(nil, "body one\n", SourceBundled).out
	crlf := []byte(strings.ReplaceAll(string(lf), "\n", "\r\n"))
	// Re-splicing identical content over a CRLF-saved file must be a no-op,
	// not a spurious "user edited" or perpetual change.
	res := splice(crlf, "body one\n", SourceBundled)
	if res.changed {
		t.Errorf("CRLF re-splice should be a no-op, got changed=%v userEdits=%v", res.changed, res.userEdits)
	}
	if res.userEdits {
		t.Error("CRLF line endings should not be flagged as user edits")
	}
}

func TestMalformedHashIsRefreshableNotUserEdit(t *testing.T) {
	out := string(splice(nil, "body\n", SourceBundled).out)
	// Corrupt the recorded hash to a 64-char (foreign-tool) form.
	corrupted := []byte(strings.Replace(out, "hash=", "hash=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef ", 1))
	blk := findBlock(corrupted)
	if blockUserEdited(corrupted, blk) {
		t.Error("a malformed/foreign hash must be treated as refreshable, not a user edit")
	}
	// And a clean re-splice should succeed in refreshing it.
	res := splice(corrupted, "body\n", SourceBundled)
	if res.userEdits {
		t.Error("re-splice over a malformed hash must not report userEdits")
	}
}

func TestRemovePreservesUserContentInDedicatedFile(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	c := testContent()

	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	// User appends their own prose after our managed block.
	data, _ := os.ReadFile(out.Path)
	withUser := string(data) + "\n## My own notes\n\nKeep me around.\n"
	if err := os.WriteFile(out.Path, []byte(withUser), 0o644); err != nil {
		t.Fatal(err)
	}

	if rout, err := Remove(ad, env, RemoveOptions{}); err != nil || !rout.Removed {
		t.Fatalf("remove failed: %v %v", rout.Removed, err)
	}
	// The file must survive because the user added content beyond our block.
	got, err := os.ReadFile(out.Path)
	if err != nil {
		t.Fatalf("dedicated SKILL.md was deleted despite user content: %v", err)
	}
	if !strings.Contains(string(got), "Keep me around.") {
		t.Error("user content should be preserved after removing the managed block")
	}
	if strings.Contains(string(got), beginMarker) {
		t.Error("managed block should have been stripped")
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

// The canonical description is long (rich trigger text). Claude AND Cursor emit
// it verbatim (dedicated SKILL.md, description-triggered); hermes adapts it to
// its one-sentence, <=60-char authoring rule.
func TestDescriptionRenderingPerOperator(t *testing.T) {
	c := testContent()
	reg := DefaultRegistry()

	for _, op := range []Operator{OpClaude, OpCursor} {
		ad, _ := reg.Resolve(string(op))
		out, _, err := ad.Render(c, nil)
		if err != nil {
			t.Fatalf("%s render: %v", op, err)
		}
		if !strings.Contains(string(out), "description: "+c.Description) {
			t.Errorf("%s frontmatter should carry the full canonical description", op)
		}
	}

	hermes, _, err := (hermesAdapter{}).Render(c, nil)
	if err != nil {
		t.Fatalf("hermes render: %v", err)
	}
	for _, line := range strings.Split(string(hermes), "\n") {
		if desc, ok := strings.CutPrefix(line, "description: "); ok {
			if n := len([]rune(desc)); n > 60 {
				t.Errorf("hermes description = %d runes, want <= 60: %q", n, desc)
			}
			if !strings.HasSuffix(desc, ".") {
				t.Errorf("hermes description must be one sentence ending in a period: %q", desc)
			}
			if !utf8.ValidString(desc) {
				t.Error("hermes description is not valid UTF-8")
			}
		}
	}
}
