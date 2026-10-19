package skillgen

import (
	"encoding/json"
	"os"
	"os/exec"
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
	victims := make([]string, 0, len(renderedReferences("jentic")))
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
		if data, _ := os.ReadFile(v); string(data) != "keep me\n" {
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

// installSymlinkedAgents installs the jentic block for codex at scope, then
// moves the file to linked (with a user prefix) and symlinks the target to it.
// Project scope links to a CLAUDE.md inside the project — the common
// AGENTS.md -> CLAUDE.md layout; user scope links into a dotfiles checkout
// outside the probed home.
func installSymlinkedAgents(t *testing.T, scope Scope, prefix string) (env DetectEnv, ad Adapter, c Canonical, link, linked string) {
	t.Helper()
	skipNoSymlinks(t)
	home, cwd := t.TempDir(), t.TempDir()
	env = DetectEnv{Home: home, Cwd: cwd}
	ad, _ = DefaultRegistry().Resolve("codex")
	c = jenticContent(t)
	out, err := Apply(ad, c, env, ApplyOptions{Scope: scope})
	if err != nil {
		t.Fatal(err)
	}
	link = out.Path
	data, err := os.ReadFile(link)
	if err != nil {
		t.Fatal(err)
	}
	linked = filepath.Join(cwd, "CLAUDE.md")
	if scope == ScopeUser {
		linked = filepath.Join(t.TempDir(), "dotfiles", "AGENTS.md")
	}
	writeVictim(t, linked)
	if err := os.WriteFile(linked, append([]byte(prefix), data...), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(link); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(linked, link); err != nil {
		t.Fatal(err)
	}
	return env, ad, c, link, linked
}

func assertStillSymlink(t *testing.T, link string) {
	t.Helper()
	fi, err := os.Lstat(link)
	if err != nil || fi.Mode()&os.ModeSymlink == 0 {
		t.Fatalf("%s must stay a symlink, got %v (err %v)", link, fi, err)
	}
}

var symlinkScopes = []Scope{ScopeProject, ScopeUser}

// A symlinked AGENTS.md with user content is edited at its target: the link
// survives and the linked file loses the block but keeps the user's notes.
func TestRemoveSymlinkedAgentsEditsTargetInPlace(t *testing.T) {
	for _, scope := range symlinkScopes {
		t.Run(string(scope), func(t *testing.T) {
			env, ad, c, link, linked := installSymlinkedAgents(t, scope, "# my notes\n\n")
			if _, err := Remove(ad, c, env, RemoveOptions{Scope: scope}); err != nil {
				t.Fatal(err)
			}
			assertStillSymlink(t, link)
			data, _ := os.ReadFile(linked)
			if strings.Contains(string(data), "JENTIC MANAGED SKILL") {
				t.Errorf("the linked file must lose the managed block:\n%s", data)
			}
			if !strings.Contains(string(data), "# my notes") {
				t.Errorf("the user's content must survive:\n%s", data)
			}
		})
	}
}

// With only the Jentic block, the link is kept and the linked file is emptied —
// neither deleted (dangling the link) nor left holding the block.
func TestRemoveSymlinkedAgentsOnlyBlockEmptiesTarget(t *testing.T) {
	for _, scope := range symlinkScopes {
		t.Run(string(scope), func(t *testing.T) {
			env, ad, c, link, linked := installSymlinkedAgents(t, scope, "")
			if _, err := Remove(ad, c, env, RemoveOptions{Scope: scope}); err != nil {
				t.Fatal(err)
			}
			assertStillSymlink(t, link)
			data, err := os.ReadFile(linked)
			if err != nil {
				t.Fatalf("the linked file must still exist: %v", err)
			}
			if strings.Contains(string(data), "JENTIC MANAGED SKILL") {
				t.Errorf("the linked file must lose the managed block:\n%s", data)
			}
		})
	}
}

// Apply onto a symlinked AGENTS.md updates the linked file, keeping the link.
func TestApplySymlinkedAgentsWritesTarget(t *testing.T) {
	for _, scope := range symlinkScopes {
		t.Run(string(scope), func(t *testing.T) {
			env, ad, _, link, linked := installSymlinkedAgents(t, scope, "# my notes\n\n")
			c, err := Bundled("jentic", "http://other.test")
			if err != nil {
				t.Fatal(err)
			}
			if _, err := Apply(ad, c, env, ApplyOptions{Scope: scope}); err != nil {
				t.Fatal(err)
			}
			assertStillSymlink(t, link)
			data, _ := os.ReadFile(linked)
			if !strings.Contains(string(data), "http://other.test") || !strings.Contains(string(data), "# my notes") {
				t.Errorf("the linked file must carry the refreshed block and the user's notes:\n%s", data)
			}
		})
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

// assertUnchanged fails when path no longer holds exactly want.
func assertUnchanged(t *testing.T, path string, want []byte) {
	t.Helper()
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if string(got) != string(want) {
		t.Errorf("%s was modified:\n%s", path, got)
	}
}

// Pins CWE-59 on project scope: a cloned repo shipping AGENTS.md -> a file
// outside the project (here a shell rc file in the user's home) must not get
// the Jentic block appended by apply (dry-run or real), nor be rewritten by
// remove. Each refuses with a manual-edit hint and leaves the link and its
// target byte-identical.
func TestProjectAgentsSymlinkOutsideProjectRefused(t *testing.T) {
	skipNoSymlinks(t)
	home, repo := t.TempDir(), t.TempDir()
	env := DetectEnv{Home: home, Cwd: repo}
	ad, _ := DefaultRegistry().Resolve("codex")
	c := jenticContent(t)

	// A block-carrying victim, so remove has something it would strip.
	scratch := DetectEnv{Home: t.TempDir(), Cwd: t.TempDir()}
	o, err := Apply(ad, c, scratch, ApplyOptions{Scope: ScopeProject})
	if err != nil {
		t.Fatal(err)
	}
	block, err := os.ReadFile(o.Path)
	if err != nil {
		t.Fatal(err)
	}
	for name, victimBody := range map[string][]byte{
		"plain rc file":      []byte("export PATH=/usr/bin\n"),
		"rc file with block": append([]byte("export PATH=/usr/bin\n\n"), block...),
	} {
		t.Run(name, func(t *testing.T) {
			bashrc := filepath.Join(home, ".bashrc")
			if err := os.WriteFile(bashrc, victimBody, 0o600); err != nil {
				t.Fatal(err)
			}
			link := filepath.Join(repo, "AGENTS.md")
			_ = os.Remove(link)
			if err := os.Symlink(bashrc, link); err != nil {
				t.Fatal(err)
			}

			for _, dry := range []bool{true, false} {
				if _, err := Apply(ad, c, env, ApplyOptions{Scope: ScopeProject, DryRun: dry}); err == nil ||
					!strings.Contains(err.Error(), "by hand") {
					t.Errorf("apply (dry=%v) must refuse with a manual-edit hint, got %v", dry, err)
				}
				if _, err := Remove(ad, c, env, RemoveOptions{Scope: ScopeProject, DryRun: dry}); err == nil ||
					!strings.Contains(err.Error(), "by hand") {
					t.Errorf("remove (dry=%v) must refuse with a manual-edit hint, got %v", dry, err)
				}
			}
			assertStillSymlink(t, link)
			assertUnchanged(t, bashrc, victimBody)
		})
	}
}

// Pins the same containment for owned-file skills: a project-scope skill dir
// (or an ancestor such as .claude) symlinked out of the project must not
// receive SKILL.md, its sidecar or references, and remove must not delete an
// install that lives outside the project.
func TestProjectSkillDirSymlinkOutsideProjectRefused(t *testing.T) {
	skipNoSymlinks(t)
	ad, _ := DefaultRegistry().Resolve("claude")
	c := jenticContent(t)
	for name, linkRel := range map[string]string{
		"skill dir": filepath.Join(".claude", "skills", "jentic"),
		"ancestor":  ".claude",
	} {
		t.Run(name, func(t *testing.T) {
			repo := t.TempDir()
			env := DetectEnv{Home: t.TempDir(), Cwd: repo}
			outside := t.TempDir()
			link := filepath.Join(repo, linkRel)
			if err := os.MkdirAll(filepath.Dir(link), 0o750); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(outside, link); err != nil {
				t.Fatal(err)
			}
			for _, dry := range []bool{true, false} {
				if _, err := Apply(ad, c, env, ApplyOptions{Scope: ScopeProject, DryRun: dry}); err == nil ||
					!strings.Contains(err.Error(), "by hand") {
					t.Errorf("apply (dry=%v) must refuse a skill dir outside the project, got %v", dry, err)
				}
			}
			if entries, _ := os.ReadDir(outside); len(entries) != 0 {
				t.Errorf("apply wrote outside the project: %v", entries)
			}
		})
	}

	t.Run("remove", func(t *testing.T) {
		repo, elsewhere := t.TempDir(), t.TempDir()
		// A genuine, unedited install outside the project…
		o, err := Apply(ad, c, DetectEnv{Home: elsewhere, Cwd: elsewhere}, ApplyOptions{Scope: ScopeUser})
		if err != nil {
			t.Fatal(err)
		}
		// …that the repo's project-scope skill dir links to.
		link := filepath.Join(repo, ".claude", "skills", "jentic")
		if err := os.MkdirAll(filepath.Dir(link), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(filepath.Dir(o.Path), link); err != nil {
			t.Fatal(err)
		}
		env := DetectEnv{Home: t.TempDir(), Cwd: repo}
		if _, err := Remove(ad, c, env, RemoveOptions{Scope: ScopeProject}); err == nil ||
			!strings.Contains(err.Error(), "by hand") {
			t.Errorf("remove must refuse a skill dir outside the project, got %v", err)
		}
		if _, err := os.Stat(o.Path); err != nil {
			t.Errorf("remove deleted an install outside the project: %v", err)
		}
	})
}

// A symlink that stays inside the project is still followed: the containment
// check resolves both sides, so a skill dir linked to another project dir
// installs normally.
func TestProjectSkillDirSymlinkInsideProjectAllowed(t *testing.T) {
	skipNoSymlinks(t)
	repo := t.TempDir()
	shared := filepath.Join(repo, "shared-skills")
	if err := os.MkdirAll(shared, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(repo, ".claude", "skills"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(shared, filepath.Join(repo, ".claude", "skills", "jentic")); err != nil {
		t.Fatal(err)
	}
	ad, _ := DefaultRegistry().Resolve("claude")
	env := DetectEnv{Home: t.TempDir(), Cwd: repo}
	if _, err := Apply(ad, jenticContent(t), env, ApplyOptions{Scope: ScopeProject}); err != nil {
		t.Fatalf("an in-project symlink must be followed: %v", err)
	}
	if _, err := os.Stat(filepath.Join(shared, "SKILL.md")); err != nil {
		t.Errorf("SKILL.md must land at the in-project link target: %v", err)
	}
}

// Pins the junction half of the os.Root confinement: on Windows a references
// junction to another directory must not redirect a reference delete or write
// out of the skill dir. filepath.EvalSymlinks does not evaluate junctions, so
// only a Root-scoped operation (or an Lstat mode check) catches them.
func TestReferencesJunctionEscapeRefused(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("junctions are a Windows-only reparse point; the symlink form is TestReferencesDirSymlinkEscapeRefused")
	}
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
	victims := make([]string, 0, len(renderedReferences("jentic")))
	for _, ref := range renderedReferences("jentic") {
		v := filepath.Join(outside, ref)
		writeVictim(t, v)
		victims = append(victims, v)
	}
	if b, err := exec.Command("cmd", "/c", "mklink", "/J", refDir, outside).CombinedOutput(); err != nil {
		t.Skipf("cannot create a junction: %v: %s", err, b)
	}

	if _, err := Apply(ad, c, env, ApplyOptions{}); err == nil {
		t.Error("apply must refuse to write through a references junction")
	}
	for _, v := range victims {
		if data, _ := os.ReadFile(v); string(data) != "keep me\n" {
			t.Errorf("apply wrote through the junction into %s", v)
		}
	}
	if _, err := Remove(ad, c, env, RemoveOptions{}); err != nil {
		t.Fatal(err)
	}
	assertSurvive(t, victims)
}
