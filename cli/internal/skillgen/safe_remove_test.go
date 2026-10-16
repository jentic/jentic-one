package skillgen

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// setRecordedReferences overwrites the sidecar's recorded reference list for
// an installed owned-file skill. The sidecar's body hash covers SKILL.md only,
// so the install still reads as an unedited Jentic write — exactly the shape a
// planted project-scope install (e.g. shipped in a cloned repo) can take.
func setRecordedReferences(t *testing.T, target string, refs []string) {
	t.Helper()
	sc, ok := readSidecar(target)
	if !ok {
		t.Fatal("apply must have written a sidecar")
	}
	sc.References = refs
	data, err := json.Marshal(sc)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(sidecarPath(target), data, 0o600); err != nil {
		t.Fatal(err)
	}
}

func writeVictim(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("keep me\n"), 0o600); err != nil {
		t.Fatal(err)
	}
}

func skipNoSymlinks(t *testing.T) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("symlinks need elevated privileges on Windows")
	}
}

// escapingRefs builds recorded reference names that would leave the skill dir
// if joined naively, plus the outside files each one would hit.
func escapingRefs(t *testing.T, target string) (refs, victims []string) {
	t.Helper()
	refDir := filepath.Join(filepath.Dir(target), "references")
	outside := filepath.Join(t.TempDir(), "victim-abs.txt")
	refs = []string{
		"../../victim-up2.txt",
		"../../../../victim-up4.txt",
		outside,
		`..\..\victim-backslash.txt`,
		"sub/../../../victim-sub.txt",
		"..",
		"",
	}
	for _, r := range refs[:len(refs)-2] {
		if filepath.IsAbs(r) {
			victims = append(victims, r)
			continue
		}
		victims = append(victims, filepath.Clean(filepath.Join(refDir, filepath.FromSlash(strings.ReplaceAll(r, `\`, "/")))))
	}
	for _, v := range victims {
		writeVictim(t, v)
	}
	return refs, victims
}

func assertSurvive(t *testing.T, paths []string) {
	t.Helper()
	for _, p := range paths {
		if _, err := os.Stat(p); err != nil {
			t.Errorf("file outside the skill dir was deleted: %s (%v)", p, err)
		}
	}
}

// Pins the CWE-22 guard on `skill remove`: a sidecar whose recorded references
// climb out of the skill dir (relative "..", absolute, Windows separators,
// nested dirs) never deletes anything outside it.
func TestRemoveRejectsEscapingRecordedReferences(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	c := jenticContent(t)
	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	refs, victims := escapingRefs(t, out.Path)
	setRecordedReferences(t, out.Path, refs)

	rout, err := Remove(ad, c, env, RemoveOptions{})
	if err != nil || !rout.Removed {
		t.Fatalf("remove: %+v err=%v", rout, err)
	}
	assertSurvive(t, victims)
}

// The same guard on the apply-time prune that `skill update` and
// `jenticctl update`'s skill refresh run.
func TestApplyPruneRejectsEscapingRecordedReferences(t *testing.T) {
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	c := jenticContent(t)
	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	refs, victims := escapingRefs(t, out.Path)
	setRecordedReferences(t, out.Path, refs)

	if _, err := Apply(ad, c, env, ApplyOptions{DryRun: true}); err != nil {
		t.Fatalf("dry-run apply: %v", err)
	}
	if _, err := Apply(ad, c, env, ApplyOptions{}); err != nil {
		t.Fatalf("apply: %v", err)
	}
	assertSurvive(t, victims)
}

// A recorded reference that is itself a symlink to an outside file is
// unlinked; its target is never touched.
func TestRemoveUnlinksReferenceSymlinkNotTarget(t *testing.T) {
	skipNoSymlinks(t)
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	c := jenticContent(t)
	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	victim := filepath.Join(t.TempDir(), "victim.txt")
	writeVictim(t, victim)
	link := filepath.Join(filepath.Dir(out.Path), "references", "planted.md")
	if err := os.Symlink(victim, link); err != nil {
		t.Fatal(err)
	}
	setRecordedReferences(t, out.Path, append(renderedReferences("jentic"), "planted.md"))

	if _, err := Remove(ad, c, env, RemoveOptions{}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(link); !os.IsNotExist(err) {
		t.Error("the symlink itself must be removed")
	}
	assertSurvive(t, []string{victim})
}

// A references/ dir swapped for a symlink to an outside directory must not
// redirect deletes (remove) or writes (apply) into that directory.
func TestReferencesDirSymlinkEscapeRefused(t *testing.T) {
	skipNoSymlinks(t)
	dir := t.TempDir()
	env := DetectEnv{Home: dir, Cwd: dir}
	ad, _ := DefaultRegistry().Resolve("claude")
	c := jenticContent(t)
	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	refDir := filepath.Join(filepath.Dir(out.Path), "references")
	if err := os.RemoveAll(refDir); err != nil {
		t.Fatal(err)
	}
	outside := t.TempDir()
	var victims []string
	for _, ref := range renderedReferences("jentic") {
		v := filepath.Join(outside, ref)
		writeVictim(t, v)
		victims = append(victims, v)
	}
	if err := os.Symlink(outside, refDir); err != nil {
		t.Fatal(err)
	}

	if _, err := Apply(ad, c, env, ApplyOptions{}); err == nil {
		t.Error("apply must refuse to write through a references/ symlink that leaves the skill dir")
	}
	for _, v := range victims {
		if data, _ := os.ReadFile(v); string(data) != "keep me\n" { //nolint:gosec // test temp path.
			t.Errorf("apply wrote through the symlink into %s", v)
		}
	}
	if _, err := Remove(ad, c, env, RemoveOptions{}); err != nil {
		t.Fatal(err)
	}
	assertSurvive(t, victims)
}

func TestValidReferenceName(t *testing.T) {
	for name, want := range map[string]bool{
		"mcp.md": true, "cli-lane.md": true,
		"": false, ".": false, "..": false, "../x": false, "a/b": false, `a\b`: false,
		"/etc/passwd": false, `C:\x`: false, "sub/../x": false,
	} {
		if got := validReferenceName(name); got != want {
			t.Errorf("validReferenceName(%q) = %v, want %v", name, got, want)
		}
	}
}

// installSymlinkedAgents installs the jentic block for codex, then moves the
// file into a "dotfiles" location with a user prefix and symlinks AGENTS.md
// to it — the layout a dotfiles-managed AGENTS.md has.
func installSymlinkedAgents(t *testing.T, prefix string) (env DetectEnv, ad Adapter, c Canonical, link, real string) {
	t.Helper()
	skipNoSymlinks(t)
	dir := t.TempDir()
	env = DetectEnv{Home: dir, Cwd: dir}
	ad, _ = DefaultRegistry().Resolve("codex")
	c = jenticContent(t)
	out, err := Apply(ad, c, env, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	link = out.Path
	data, err := os.ReadFile(link) //nolint:gosec // test temp path.
	if err != nil {
		t.Fatal(err)
	}
	real = filepath.Join(t.TempDir(), "dotfiles-AGENTS.md")
	if err := os.WriteFile(real, append([]byte(prefix), data...), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(link); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	return env, ad, c, link, real
}

func assertStillSymlink(t *testing.T, link string) {
	t.Helper()
	fi, err := os.Lstat(link)
	if err != nil || fi.Mode()&os.ModeSymlink == 0 {
		t.Fatalf("AGENTS.md must stay a symlink, got %v (err %v)", fi, err)
	}
}

// A symlinked AGENTS.md with user content is edited at its target: the link
// survives and the real file loses the block but keeps the user's notes.
func TestRemoveSymlinkedAgentsEditsTargetInPlace(t *testing.T) {
	env, ad, c, link, real := installSymlinkedAgents(t, "# my notes\n\n")
	if _, err := Remove(ad, c, env, RemoveOptions{}); err != nil {
		t.Fatal(err)
	}
	assertStillSymlink(t, link)
	data, _ := os.ReadFile(real) //nolint:gosec // test temp path.
	if strings.Contains(string(data), "JENTIC MANAGED SKILL") {
		t.Errorf("the real file must lose the managed block:\n%s", data)
	}
	if !strings.Contains(string(data), "# my notes") {
		t.Errorf("the user's content must survive:\n%s", data)
	}
}

// With only the Jentic block, the link is kept and the real file is emptied —
// neither deleted (dangling the link) nor left holding the block.
func TestRemoveSymlinkedAgentsOnlyBlockEmptiesTarget(t *testing.T) {
	env, ad, c, link, real := installSymlinkedAgents(t, "")
	if _, err := Remove(ad, c, env, RemoveOptions{}); err != nil {
		t.Fatal(err)
	}
	assertStillSymlink(t, link)
	data, err := os.ReadFile(real) //nolint:gosec // test temp path.
	if err != nil {
		t.Fatalf("the real file must still exist: %v", err)
	}
	if strings.Contains(string(data), "JENTIC MANAGED SKILL") {
		t.Errorf("the real file must lose the managed block:\n%s", data)
	}
}

// Apply onto a symlinked AGENTS.md updates the real file, keeping the link.
func TestApplySymlinkedAgentsWritesTarget(t *testing.T) {
	env, ad, _, link, real := installSymlinkedAgents(t, "# my notes\n\n")
	c, err := Bundled("jentic", "http://other.test")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Apply(ad, c, env, ApplyOptions{}); err != nil {
		t.Fatal(err)
	}
	assertStillSymlink(t, link)
	data, _ := os.ReadFile(real) //nolint:gosec // test temp path.
	if !strings.Contains(string(data), "http://other.test") || !strings.Contains(string(data), "# my notes") {
		t.Errorf("the real file must carry the refreshed block and the user's notes:\n%s", data)
	}
}

// A dangling shared-target symlink is refused with a message naming it,
// never replaced by a regular file.
func TestResolveSharedTargetRefusesDanglingLink(t *testing.T) {
	skipNoSymlinks(t)
	dir := t.TempDir()
	link := filepath.Join(dir, "AGENTS.md")
	if err := os.Symlink(filepath.Join(dir, "gone.md"), link); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveSharedTarget(link); err == nil || !strings.Contains(err.Error(), "by hand") {
		t.Errorf("a dangling link must be refused with a manual hint, got %v", err)
	}
	assertStillSymlink(t, link)
}
