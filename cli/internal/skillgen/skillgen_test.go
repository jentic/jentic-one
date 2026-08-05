package skillgen

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// jenticContent parses the bundled canonical jentic skill, failing on error.
func jenticContent(t *testing.T) Canonical {
	t.Helper()
	c, err := Bundled("jentic", "http://example.test")
	if err != nil {
		t.Fatalf("Bundled(jentic): %v", err)
	}
	return c
}

// freeformContent parses a bundled freeform skill.
func freeformContent(t *testing.T, name string) Canonical {
	t.Helper()
	c, err := Bundled(name, "http://example.test")
	if err != nil {
		t.Fatalf("Bundled(%s): %v", name, err)
	}
	return c
}

func TestRenderBodyIncludesBaseURLAndSections(t *testing.T) {
	body := renderBody(jenticContent(t))
	for _, want := range []string{
		"# Using Jentic from the CLI",
		"http://example.test",
		"## When to Use",
		"## Procedure",
		"### 1. Confirm you have a valid identity",
		"jentic access request",
		"## Verification",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("rendered body missing %q", want)
		}
	}
}

func TestManagedBlockRoundTrip(t *testing.T) {
	body := "hello\nworld\n"
	blkText := managedBlock("jentic", body, SourceBundled)
	if !strings.Contains(blkText, beginMarkerFor("jentic")) || !strings.Contains(blkText, endMarkerFor("jentic")) {
		t.Fatal("named markers missing")
	}
	blk := findBlock([]byte(blkText), "jentic")
	if !blk.found {
		t.Fatal("findBlock did not locate the named block")
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
	r1 := splice(nil, "jentic", "body one\n", SourceBundled)
	if !r1.created || !r1.changed {
		t.Fatalf("new file should be created+changed: %+v", r1)
	}

	existing := []byte("# user heading\n\nsome notes\n")
	r2 := splice(existing, "jentic", "body one\n", SourceBundled)
	if !strings.Contains(string(r2.out), "# user heading") || !strings.Contains(string(r2.out), "some notes") {
		t.Error("user content not preserved on splice")
	}
	if !strings.Contains(string(r2.out), beginMarkerFor("jentic")) {
		t.Error("managed block not added")
	}

	r3 := splice(r2.out, "jentic", "body one\n", SourceBundled)
	if r3.changed {
		t.Error("identical re-splice should be a no-op")
	}

	r4 := splice(r2.out, "jentic", "body two\n", SourceBundled)
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

// TestSpliceMultipleNamedBlocksCoexist proves several skills' named blocks live
// in one file and that replacing one leaves siblings byte-identical.
func TestSpliceMultipleNamedBlocksCoexist(t *testing.T) {
	out := splice(nil, "jentic", "jentic body\n", SourceBundled).out
	out = splice(out, "import-new-api", "import body\n", SourceBundled).out

	if !strings.Contains(string(out), beginMarkerFor("jentic")) ||
		!strings.Contains(string(out), beginMarkerFor("import-new-api")) {
		t.Fatal("both named blocks should be present")
	}

	// Snapshot the jentic block, then update only import-new-api.
	before := findBlock(out, "jentic")
	jenticRegion := string(out[before.start:before.endPos])

	updated := splice(out, "import-new-api", "import body v2\n", SourceBundled)
	if !updated.changed {
		t.Fatal("import-new-api update should change the file")
	}
	after := findBlock(updated.out, "jentic")
	if string(updated.out[after.start:after.endPos]) != jenticRegion {
		t.Error("sibling jentic block must be byte-identical after updating import-new-api")
	}
	if !strings.Contains(string(updated.out), "import body v2") {
		t.Error("import-new-api block not updated")
	}
}

func TestSpliceDetectsUserEdits(t *testing.T) {
	r := splice(nil, "jentic", "original\n", SourceBundled)
	tampered := strings.Replace(string(r.out), "original", "tampered", 1)
	res := splice([]byte(tampered), "jentic", "original\n", SourceBundled)
	if !res.userEdits {
		t.Error("expected userEdits to be detected on tampered block")
	}
}

// TestLegacyBlockMigration proves an old un-named AGENTS.md block is migrated to
// the named form and NOT falsely flagged as user-edited (its body hash still
// verifies via the legacy end marker).
func TestLegacyBlockMigration(t *testing.T) {
	body := "# Using Jentic from the CLI\n\nsome pointer text\n"
	// Build a legacy (un-named) block exactly as the old generator would.
	legacy := legacyBeginMarker + " hash=" + hashContent(body) + " source=bundled\n" +
		strings.TrimRight(body, "\n") + "\n" + legacyEndMarker
	existing := []byte("# user AGENTS\n\n" + legacy + "\n")

	// The legacy block must be locatable and NOT flagged user-edited.
	lb := findLegacyBlock(existing)
	if !lb.found {
		t.Fatal("legacy block not found")
	}
	if blockUserEdited(existing, lb) {
		t.Fatal("legacy block wrongly flagged as user-edited")
	}

	res := splice(existing, "jentic", body, SourceBundled)
	if !res.changed {
		t.Fatal("migration should rewrite the file")
	}
	if res.userEdits {
		t.Error("migration must not report user edits")
	}
	if strings.Contains(string(res.out), legacyBeginMarker) {
		t.Error("legacy marker should be gone after migration")
	}
	if !strings.Contains(string(res.out), beginMarkerFor("jentic")) {
		t.Error("named marker should replace the legacy one")
	}
	if !strings.Contains(string(res.out), "# user AGENTS") {
		t.Error("surrounding user content lost during migration")
	}
}

// TestLegacyBlockNotConsumedByFlowSkill pins the fix for the legacy-block
// misattribution bug: the old un-named block is jentic's by construction, so a
// *flow* skill (contribute-spec-fix / import-new-api) must never migrate/consume
// it. Splicing a flow skill over a legacy jentic block must leave the legacy
// block intact and append the flow skill as a fresh named block instead.
func TestLegacyBlockNotConsumedByFlowSkill(t *testing.T) {
	jbody := "# Using Jentic from the CLI\n\njentic pointer\n"
	legacy := legacyBeginMarker + " hash=" + hashContent(jbody) + " source=bundled\n" +
		strings.TrimRight(jbody, "\n") + "\n" + legacyEndMarker
	existing := []byte("# user AGENTS\n\n" + legacy + "\n")

	res := splice(existing, "contribute-spec-fix", "## contribute-spec-fix\n\nflow pointer\n", SourceBundled)

	// The legacy jentic block must survive untouched…
	if !strings.Contains(string(res.out), legacyBeginMarker) {
		t.Error("flow-skill splice destroyed the legacy jentic block")
	}
	if !strings.Contains(string(res.out), "jentic pointer") {
		t.Error("legacy jentic body was clobbered by the flow skill")
	}
	// …and the flow skill lands as its own named block.
	if !strings.Contains(string(res.out), beginMarkerFor("contribute-spec-fix")) {
		t.Error("flow skill not appended as a named block")
	}
	// A subsequent jentic splice then migrates the legacy block (only jentic).
	res2 := splice(res.out, "jentic", jbody, SourceBundled)
	if strings.Contains(string(res2.out), legacyBeginMarker) {
		t.Error("jentic splice should migrate the legacy block to the named form")
	}
	if !strings.Contains(string(res2.out), beginMarkerFor("jentic")) ||
		!strings.Contains(string(res2.out), beginMarkerFor("contribute-spec-fix")) {
		t.Error("both skills should end up as named blocks")
	}
}

// TestLegacyBlockFlowRemoveAndProbeDoNotMisattribute pins the remove/list half
// of the same fix: removing or probing a *flow* skill against a legacy-only
// install must not touch or claim the legacy jentic block.
func TestLegacyBlockFlowRemoveAndProbeDoNotMisattribute(t *testing.T) {
	jbody := "# Using Jentic from the CLI\n\njentic pointer\n"
	legacy := legacyBeginMarker + " hash=" + hashContent(jbody) + " source=bundled\n" +
		strings.TrimRight(jbody, "\n") + "\n" + legacyEndMarker
	existing := []byte(legacy + "\n")

	// A flow skill must not be reported installed off a legacy-only file.
	if blk := findMigratableLegacyBlock(existing, "import-new-api"); blk.found {
		t.Error("legacy block wrongly attributed to import-new-api")
	}
	// …but jentic still migrates it.
	if blk := findMigratableLegacyBlock(existing, "jentic"); !blk.found {
		t.Error("legacy block should be migratable for jentic")
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

// TestAdapterTargetsPerName pins that Target paths are parametric over the skill
// name (no hardcoded "jentic").
func TestAdapterTargetsPerName(t *testing.T) {
	env := DetectEnv{Home: "/home/u", Cwd: "/proj"}
	reg := DefaultRegistry()
	cases := []struct {
		op   Operator
		name string
		want string
	}{
		{OpClaude, "jentic", "/home/u/.claude/skills/jentic/SKILL.md"},
		{OpClaude, "contribute-spec-fix", "/home/u/.claude/skills/contribute-spec-fix/SKILL.md"},
		{OpCursor, "import-new-api", "/home/u/.cursor/skills/import-new-api/SKILL.md"},
		{OpHermes, "contribute-spec-fix", "/home/u/.hermes/skills/api/contribute-spec-fix/SKILL.md"},
		{OpCodex, "jentic", "/proj/AGENTS.md"},
		{OpGeneric, "import-new-api", "/proj/AGENTS.md"},
	}
	for _, c := range cases {
		ad, _ := reg.Resolve(string(c.op))
		if got := ad.Target(ad.DefaultScope(), c.name, env); got != c.want {
			t.Errorf("%s/%s target = %q, want %q", c.op, c.name, got, c.want)
		}
	}
}

func TestApplyAndRemoveSharedFile(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("generic")
	c := jenticContent(t)

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

	out, err = Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !out.Created {
		t.Error("first apply should create")
	}
	data, _ := os.ReadFile(out.Path)
	if !strings.Contains(string(data), beginMarkerFor("jentic")) {
		t.Error("named managed block not written")
	}
	// AGENTS.md is a pointer block, not the full body.
	if !strings.Contains(string(data), "See the full skill: GET http://example.test/skills/jentic.md") {
		t.Errorf("AGENTS.md should carry a pointer link, got:\n%s", data)
	}

	out2, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if out2.Changed || !out2.Skipped {
		t.Errorf("re-apply should be skipped: %+v", out2)
	}

	rout, err := Remove(ad, c, env, RemoveOptions{})
	if err != nil || !rout.Removed {
		t.Fatalf("remove failed: removed=%v err=%v", rout.Removed, err)
	}
	if _, err := os.Stat(rout.Path); !os.IsNotExist(err) {
		t.Error("AGENTS.md should be gone after removing the only block")
	}
}

// TestSharedFileTwoSkillsCoexistUpdateRemove exercises the full multi-block
// lifecycle on one AGENTS.md: install two skills, update one (sibling stays
// byte-identical), remove one (sibling survives, file not deleted).
func TestSharedFileTwoSkillsCoexistUpdateRemove(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("generic")
	jentic := jenticContent(t)
	flow := freeformContent(t, "import-new-api")
	target := ad.Target(ScopeProject, jentic.Name, env)

	if _, err := Apply(ad, jentic, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	if _, err := Apply(ad, flow, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(target)
	if !strings.Contains(string(data), beginMarkerFor("jentic")) ||
		!strings.Contains(string(data), beginMarkerFor("import-new-api")) {
		t.Fatal("both named blocks should coexist in AGENTS.md")
	}

	// Snapshot the jentic block, change the base URL of the flow skill, update.
	before := findBlock(data, "jentic")
	jenticRegion := string(data[before.start:before.endPos])
	flow.BaseURL = "http://changed.test"
	if _, err := Apply(ad, flow, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	data, _ = os.ReadFile(target)
	after := findBlock(data, "jentic")
	if string(data[after.start:after.endPos]) != jenticRegion {
		t.Error("jentic block must be byte-identical after updating a sibling")
	}
	if !strings.Contains(string(data), "http://changed.test/skills/import-new-api.md") {
		t.Error("import-new-api pointer not refreshed with new base URL")
	}

	// Remove one skill; the sibling and the file must survive.
	rout, err := Remove(ad, flow, env, RemoveOptions{Scope: ScopeProject})
	if err != nil || !rout.Removed {
		t.Fatalf("remove import-new-api failed: %+v err=%v", rout, err)
	}
	data, _ = os.ReadFile(target)
	if strings.Contains(string(data), beginMarkerFor("import-new-api")) {
		t.Error("removed skill block should be gone")
	}
	if !strings.Contains(string(data), beginMarkerFor("jentic")) {
		t.Error("sibling jentic block must survive removing another skill")
	}
}

// TestSharedFilePerSkillEditGuard proves a user edit to skill A's block does not
// freeze updates to skill B.
func TestSharedFilePerSkillEditGuard(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("generic")
	jentic := jenticContent(t)
	flow := freeformContent(t, "import-new-api")
	target := ad.Target(ScopeProject, jentic.Name, env)

	if _, err := Apply(ad, jentic, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	if _, err := Apply(ad, flow, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}

	// Tamper inside the jentic block only.
	data, _ := os.ReadFile(target)
	tampered := strings.Replace(string(data), "See the full skill: GET http://example.test/skills/jentic.md", "TAMPERED", 1)
	if tampered == string(data) {
		t.Fatal("tamper target not found")
	}
	if err := os.WriteFile(target, []byte(tampered), 0o644); err != nil {
		t.Fatal(err)
	}

	// Applying jentic must refuse (its block is edited)…
	out, err := Apply(ad, jentic, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !out.UserEdits {
		t.Error("edited jentic block must refuse without --force")
	}
	// …but applying import-new-api (a different base URL) must still succeed.
	flow.BaseURL = "http://sibling.test"
	out, err = Apply(ad, flow, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if out.UserEdits {
		t.Error("a user edit to jentic must not freeze import-new-api updates")
	}
	if !out.Changed {
		t.Error("import-new-api should update despite jentic's edit")
	}
}

// TestOwnedFileCleanNoMarkersWithSidecar pins decision 1: an owned-file
// SKILL.md is a clean spec file (frontmatter + verbatim body, NO managed
// markers) and provenance lives in a sidecar next to it.
func TestOwnedFileCleanNoMarkersWithSidecar(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	c := jenticContent(t)

	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !out.Created || filepath.Base(out.Path) != "SKILL.md" {
		t.Fatalf("unexpected outcome: %+v", out)
	}
	data, _ := os.ReadFile(out.Path)
	if strings.Contains(string(data), "BEGIN JENTIC MANAGED SKILL") || strings.Contains(string(data), "END JENTIC MANAGED SKILL") {
		t.Errorf("owned-file SKILL.md must NOT contain managed markers:\n%s", data)
	}
	if !strings.HasPrefix(string(data), "---\nname: jentic\n") {
		t.Error("SKILL.md must start with clean frontmatter")
	}
	if strings.Contains(string(data), "regenerated by Jentic") || strings.Contains(string(data), "hash=") {
		t.Error("provenance noise must not leak into the served body")
	}

	// The sidecar must exist next to the SKILL.md with a body hash.
	sc, ok := readSidecar(out.Path)
	if !ok {
		t.Fatal("sidecar .jentic-skill.json not written")
	}
	if sc.Name != "jentic" || sc.BodyHash == "" || sc.Source != "bundled" {
		t.Errorf("sidecar contents wrong: %+v", sc)
	}

	// Idempotent re-apply.
	out2, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if out2.Changed || !out2.Skipped {
		t.Errorf("owned-file re-apply should be skipped: %+v", out2)
	}
}

// TestOwnedFileFreeformVerbatimBody proves a freeform skill's body is emitted
// verbatim under clean frontmatter.
func TestOwnedFileFreeformVerbatimBody(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("cursor")
	c := freeformContent(t, "contribute-spec-fix")

	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(dir, ".cursor", "skills", "contribute-spec-fix", "SKILL.md"); out.Path != want {
		t.Errorf("path = %q, want %q", out.Path, want)
	}
	data, _ := os.ReadFile(out.Path)
	s := string(data)
	if strings.Contains(s, "BEGIN JENTIC MANAGED SKILL") {
		t.Error("freeform owned-file must have no markers")
	}
	// Frontmatter carries name + description + nested argument-hint.
	if !strings.Contains(s, "name: contribute-spec-fix") {
		t.Error("frontmatter name missing")
	}
	if !strings.Contains(s, "metadata:") || !strings.Contains(s, "argument-hint:") {
		t.Error("argument-hint should pass through under metadata")
	}
	// Verbatim body headings preserved exactly.
	if !strings.Contains(s, "## The flywheel — read this first") {
		t.Error("verbatim body heading missing")
	}
	if !strings.Contains(s, c.Body) {
		t.Error("body should be emitted verbatim")
	}
}

// TestOwnedFilePruneRespectsCursorBoundary pins that removing an owned-file
// skill prunes skills/<name>/ but never removes .cursor or the skills dir.
func TestOwnedFilePruneRespectsCursorBoundary(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("cursor")
	c := freeformContent(t, "import-new-api")

	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	skillDir := filepath.Dir(out.Path)   // .cursor/skills/import-new-api
	skillsDir := filepath.Dir(skillDir)  // .cursor/skills
	cursorDir := filepath.Dir(skillsDir) // .cursor
	// Drop another skill so skills/ is not empty after removing the first.
	other := freeformContent(t, "contribute-spec-fix")
	if _, err := Apply(ad, other, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}

	if rout, err := Remove(ad, c, env, RemoveOptions{}); err != nil || !rout.Removed {
		t.Fatalf("remove failed: %+v err=%v", rout, err)
	}
	if _, err := os.Stat(skillDir); !os.IsNotExist(err) {
		t.Error("skills/<name> should be pruned")
	}
	if _, err := os.Stat(skillsDir); err != nil {
		t.Error("skills dir must survive (sibling still installed)")
	}
	if _, err := os.Stat(cursorDir); err != nil {
		t.Error(".cursor must never be pruned")
	}

	// Remove the last skill: skills/<name> is pruned, but .cursor and skills
	// remain hard boundaries even when empty.
	if _, err := Remove(ad, other, env, RemoveOptions{}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(cursorDir); err != nil {
		t.Error(".cursor must never be removed even when skills is empty")
	}
}

// TestOwnedFileEditGuardViaSidecar proves a user edit to the SKILL.md body is
// detected via the sidecar hash and refused without --force.
func TestOwnedFileEditGuardViaSidecar(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	c := jenticContent(t)

	if _, err := Apply(ad, c, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	target := ad.Target(ScopeUser, c.Name, env)
	data, _ := os.ReadFile(target)
	edited := string(data) + "\n## My own notes\n\nkeep me\n"
	if err := os.WriteFile(target, []byte(edited), 0o644); err != nil {
		t.Fatal(err)
	}

	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !out.UserEdits {
		t.Fatal("edited SKILL.md body must be flagged via the sidecar")
	}
	cur, _ := os.ReadFile(target)
	if !strings.Contains(string(cur), "keep me") {
		t.Error("edited body should be preserved without --force")
	}

	if _, err := Apply(ad, c, env, ApplyOptions{Force: true}); err != nil {
		t.Fatal(err)
	}
	cur, _ = os.ReadFile(target)
	if strings.Contains(string(cur), "keep me") {
		t.Error("--force should restore the canonical body")
	}
}

// TestOwnedFileLegacyMigration proves a pre-split marker-wrapped dir SKILL.md is
// recognized as ours and rewritten clean (not flagged user-edited).
func TestOwnedFileLegacyMigration(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	c := jenticContent(t)
	target := ad.Target(ScopeUser, c.Name, env)

	// Fabricate an old dedicated file: frontmatter + a legacy managed block.
	body := renderBody(c)
	legacy := legacyBeginMarker + " hash=" + hashContent(body) + " source=bundled\n" +
		strings.TrimRight(body, "\n") + "\n" + legacyEndMarker
	old := "---\nname: jentic\ndescription: " + c.Description + "\n---\n\n" + legacy + "\n"
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, []byte(old), 0o644); err != nil {
		t.Fatal(err)
	}

	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if out.UserEdits {
		t.Fatal("a legacy marker-wrapped dir file is our own write, not a user edit")
	}
	cur, _ := os.ReadFile(target)
	if strings.Contains(string(cur), "BEGIN JENTIC MANAGED SKILL") {
		t.Error("legacy markers should be stripped on migration")
	}
	if _, ok := readSidecar(target); !ok {
		t.Error("migration should write the provenance sidecar")
	}
}

func TestApplyWritesAtomically(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("generic")
	c := jenticContent(t)

	target := ad.Target(ScopeProject, c.Name, env)
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
	if !strings.Contains(string(cur), beginMarkerFor("jentic")) {
		t.Error("managed block not written")
	}
	if info, _ := os.Stat(target); info.Mode().Perm() != 0o600 {
		t.Errorf("mode = %o, want 600", info.Mode().Perm())
	}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".jentic-skill-") {
			t.Errorf("leftover temp file: %s", e.Name())
		}
	}
}

func TestFindBlockIgnoresMarkerInsideContent(t *testing.T) {
	realBlock := splice(nil, "jentic", "real body\n", SourceBundled).out
	doc := "# Notes\n\nExample marker: `" + beginMarkerFor("jentic") + "` (quoted inline, not anchored)\n\n"
	combined := []byte(doc + string(realBlock))
	blk := findBlock(combined, "jentic")
	if !blk.found {
		t.Fatal("expected to find the real anchored block")
	}
	if hashContent(currentBlockBody(combined, blk)) != blk.hash {
		t.Error("findBlock latched onto a non-anchored marker mention")
	}
}

func TestSpliceHandlesCRLF(t *testing.T) {
	lf := splice(nil, "jentic", "body one\n", SourceBundled).out
	crlf := []byte(strings.ReplaceAll(string(lf), "\n", "\r\n"))
	res := splice(crlf, "jentic", "body one\n", SourceBundled)
	if res.changed {
		t.Errorf("CRLF re-splice should be a no-op, got changed=%v", res.changed)
	}
	if res.userEdits {
		t.Error("CRLF line endings should not be flagged as user edits")
	}
}

func TestMalformedHashIsRefreshableNotUserEdit(t *testing.T) {
	out := string(splice(nil, "jentic", "body\n", SourceBundled).out)
	corrupted := []byte(strings.Replace(out, "hash=", "hash=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef ", 1))
	blk := findBlock(corrupted, "jentic")
	if blockUserEdited(corrupted, blk) {
		t.Error("a malformed/foreign hash must be treated as refreshable")
	}
	res := splice(corrupted, "jentic", "body\n", SourceBundled)
	if res.userEdits {
		t.Error("re-splice over a malformed hash must not report userEdits")
	}
}

// TestDescriptionRenderingPerOperator: claude/cursor emit the full description
// verbatim; hermes adapts canonical to its <=60-char one-sentence rule but
// emits the freeform description in full.
func TestDescriptionRenderingPerOperator(t *testing.T) {
	c := jenticContent(t)
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

	hermesOut, _, err := (hermesAdapter{}).Render(c, nil)
	if err != nil {
		t.Fatalf("hermes render: %v", err)
	}
	for _, line := range strings.Split(string(hermesOut), "\n") {
		if desc, ok := strings.CutPrefix(line, "description: "); ok {
			if n := len([]rune(desc)); n > 60 {
				t.Errorf("hermes canonical description = %d runes, want <= 60: %q", n, desc)
			}
		}
	}

	// Freeform hermes keeps the full description and derives tags from the name.
	flow := freeformContent(t, "import-new-api")
	fout, _, err := (hermesAdapter{}).Render(flow, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(fout), "description: "+flow.Description) {
		t.Error("freeform hermes should emit the full description (no 60-char shortening)")
	}
	if !strings.Contains(string(fout), "tags: [import-new-api,") {
		t.Errorf("hermes tags should derive from the skill name:\n%s", fout)
	}
}
