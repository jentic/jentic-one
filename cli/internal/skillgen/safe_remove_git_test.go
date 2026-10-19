package skillgen

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// gitConfigBody is a representative .git/config: git parses it on every
// command, so an appended Jentic block would break the repository.
const gitConfigBody = "[core]\n\trepositoryformatversion = 0\n\tbare = false\n[remote \"origin\"]\n\turl = https://example.test/repo.git\n"

// assertGitTargetRefused links repo/AGENTS.md to victim and checks that apply
// and remove (dry-run and real) all refuse with errInGitMetadata and a
// manual-edit hint, leaving the link and victim byte-identical.
func assertGitTargetRefused(t *testing.T, repo, victim string) {
	t.Helper()
	link := filepath.Join(repo, "AGENTS.md")
	if err := os.Symlink(victim, link); err != nil {
		t.Fatal(err)
	}
	want, err := os.ReadFile(victim)
	if err != nil {
		t.Fatal(err)
	}
	ad, _ := DefaultRegistry().Resolve("codex")
	c := jenticContent(t)
	env := DetectEnv{Home: t.TempDir(), Cwd: repo}
	for _, dry := range []bool{true, false} {
		_, err := Apply(ad, c, env, ApplyOptions{Scope: ScopeProject, DryRun: dry})
		if !errors.Is(err, errInGitMetadata) || !strings.Contains(err.Error(), "by hand") {
			t.Errorf("apply (dry=%v) must refuse a git-metadata target with a manual-edit hint, got %v", dry, err)
		}
		_, err = Remove(ad, c, env, RemoveOptions{Scope: ScopeProject, DryRun: dry})
		if !errors.Is(err, errInGitMetadata) || !strings.Contains(err.Error(), "by hand") {
			t.Errorf("remove (dry=%v) must refuse a git-metadata target with a manual-edit hint, got %v", dry, err)
		}
	}
	assertStillSymlink(t, link)
	assertUnchanged(t, victim, want)
}

// codexBlockFile returns a codex AGENTS.md holding only the Jentic block, so a
// victim can carry one that remove would otherwise strip.
func codexBlockFile(t *testing.T) []byte {
	t.Helper()
	ad, _ := DefaultRegistry().Resolve("codex")
	o, err := Apply(ad, jenticContent(t), DetectEnv{Home: t.TempDir(), Cwd: t.TempDir()}, ApplyOptions{Scope: ScopeProject})
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(o.Path)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

// Pins that "inside the project" excludes its git metadata: a cloned repo
// shipping AGENTS.md -> .git/config (or a hook) must not get the Jentic block
// appended there, nor have a planted block stripped from it.
func TestProjectAgentsSymlinkIntoGitDirRefused(t *testing.T) {
	skipNoSymlinks(t)
	block := codexBlockFile(t)
	for name, tc := range map[string]struct {
		rel  string
		body []byte
	}{
		"config":            {filepath.Join(".git", "config"), []byte(gitConfigBody)},
		"config with block": {filepath.Join(".git", "config"), append([]byte(gitConfigBody+"\n"), block...)},
		"hook":              {filepath.Join(".git", "hooks", "pre-commit"), []byte("#!/bin/sh\nexit 0\n")},
		"nested repo":       {filepath.Join("vendor", "lib", ".git", "config"), []byte(gitConfigBody)},
	} {
		t.Run(name, func(t *testing.T) {
			repo := t.TempDir()
			victim := filepath.Join(repo, tc.rel)
			if err := os.MkdirAll(filepath.Dir(victim), 0o750); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(victim, tc.body, 0o600); err != nil {
				t.Fatal(err)
			}
			assertGitTargetRefused(t, repo, victim)
		})
	}
}

// Pins the `.git` file form (linked worktree, submodule, --separate-git-dir):
// the gitdir it names and that gitdir's commondir are git metadata too, both
// when they live elsewhere and when they sit inside the project.
func TestProjectAgentsSymlinkIntoLinkedGitDirRefused(t *testing.T) {
	skipNoSymlinks(t)

	t.Run("worktree commondir config", func(t *testing.T) {
		mainRepo, repo := t.TempDir(), t.TempDir()
		config := filepath.Join(mainRepo, ".git", "config")
		wtGitDir := filepath.Join(mainRepo, ".git", "worktrees", "wt")
		if err := os.MkdirAll(wtGitDir, 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(config, []byte(gitConfigBody), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(wtGitDir, "commondir"), []byte("../..\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(repo, ".git"), []byte("gitdir: "+wtGitDir+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		assertGitTargetRefused(t, repo, config)
	})

	t.Run("separate gitdir inside project", func(t *testing.T) {
		repo := t.TempDir()
		config := filepath.Join(repo, "gitmeta", "config")
		if err := os.MkdirAll(filepath.Dir(config), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(config, []byte(gitConfigBody), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(repo, ".git"), []byte("gitdir: gitmeta\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		assertGitTargetRefused(t, repo, config)
	})
}

// An owned-file skill dir linked into .git/hooks must not receive SKILL.md,
// its sidecar or references.
func TestProjectSkillDirSymlinkIntoGitDirRefused(t *testing.T) {
	skipNoSymlinks(t)
	repo := t.TempDir()
	hooks := filepath.Join(repo, ".git", "hooks")
	if err := os.MkdirAll(hooks, 0o750); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(repo, ".claude", "skills", "jentic")
	if err := os.MkdirAll(filepath.Dir(link), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(hooks, link); err != nil {
		t.Fatal(err)
	}
	ad, _ := DefaultRegistry().Resolve("claude")
	env := DetectEnv{Home: t.TempDir(), Cwd: repo}
	for _, dry := range []bool{true, false} {
		if _, err := Apply(ad, jenticContent(t), env, ApplyOptions{Scope: ScopeProject, DryRun: dry}); !errors.Is(err, errInGitMetadata) {
			t.Errorf("apply (dry=%v) must refuse a skill dir inside .git, got %v", dry, err)
		}
	}
	if entries, _ := os.ReadDir(hooks); len(entries) != 0 {
		t.Errorf("apply wrote into .git/hooks: %v", entries)
	}
}

// A plain project with a real .git directory still installs: only targets
// that resolve into the metadata are refused.
func TestProjectWithGitDirStillInstalls(t *testing.T) {
	repo := t.TempDir()
	if err := os.MkdirAll(filepath.Join(repo, ".git"), 0o750); err != nil {
		t.Fatal(err)
	}
	for _, op := range []string{"codex", "claude"} {
		ad, _ := DefaultRegistry().Resolve(op)
		env := DetectEnv{Home: t.TempDir(), Cwd: repo}
		if _, err := Apply(ad, jenticContent(t), env, ApplyOptions{Scope: ScopeProject}); err != nil {
			t.Errorf("%s: a project with a .git dir must still install: %v", op, err)
		}
	}
}

// A malformed or oversized `.git` file names no gitdir, so it neither fails
// nor widens the refusal beyond the `.git` entry itself.
func TestLinkedGitDirsIgnoresMalformedGitFile(t *testing.T) {
	for name, body := range map[string]string{
		"no prefix":  "not a pointer\n",
		"empty path": "gitdir:   \n",
		"oversized":  "gitdir: x" + strings.Repeat("y", maxGitFileSize),
	} {
		t.Run(name, func(t *testing.T) {
			repo := t.TempDir()
			if err := os.WriteFile(filepath.Join(repo, ".git"), []byte(body), 0o600); err != nil {
				t.Fatal(err)
			}
			if dirs := linkedGitDirs(repo); len(dirs) != 0 {
				t.Errorf("a malformed .git file must name no gitdir, got %v", dirs)
			}
		})
	}
}
