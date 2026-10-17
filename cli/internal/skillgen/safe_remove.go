package skillgen

import (
	"crypto/rand"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// errUnsafeReference marks a recorded reference name that does not resolve to
// a plain file inside the skill dir. The sidecar is on-disk data anyone with
// write access to the skill dir (or a cloned repo shipping a project-scope
// install) controls, so its reference list is never trusted as a path.
var errUnsafeReference = errors.New("reference resolves outside the skill directory")

// errOutsideProject marks a project-scope target whose resolved location
// leaves the project directory, through a symlinked AGENTS.md, skill dir, or
// any ancestor between them. A cloned repo controls every one of those paths,
// so a project-scope write is only made where the project itself lives.
var errOutsideProject = errors.New("resolves outside the project directory")

// referencesDir is the reference subdirectory of a skill dir, relative to it.
const referencesDir = "references"

// validReferenceName reports whether ref is a flat file name: references are
// written as direct children of <skill dir>/references/, so anything that is
// absolute, climbs with "..", or carries a separator (either OS's) is not a
// name this generator could have written.
func validReferenceName(ref string) bool {
	if ref == "" || ref == "." || ref == ".." {
		return false
	}
	if strings.ContainsAny(ref, `/\`) || filepath.IsAbs(ref) || filepath.VolumeName(ref) != "" {
		return false
	}
	return filepath.IsLocal(ref)
}

// within reports whether path is root or lies beneath it. Both must already
// be cleaned and symlink-resolved.
func within(root, path string) bool {
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return false
	}
	return rel == "." || filepath.IsLocal(rel)
}

// resolveExisting returns the absolute form of path with every symlink in its
// longest existing prefix resolved; the components that do not exist yet are
// appended unchanged, since the caller creates them as real entries. A
// dangling symlink anywhere on the path is refused: a later MkdirAll or write
// would follow it to wherever it points.
func resolveExisting(path string) (string, error) {
	p, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	var rest []string
	for {
		resolved, err := filepath.EvalSymlinks(p)
		if err == nil {
			return filepath.Join(append([]string{resolved}, rest...)...), nil
		}
		if !errors.Is(err, fs.ErrNotExist) {
			return "", fmt.Errorf("resolve %s: %w", p, err)
		}
		if fi, lerr := os.Lstat(p); lerr == nil && fi.Mode()&os.ModeSymlink != 0 {
			return "", fmt.Errorf("%s is a symlink that cannot be resolved", p)
		}
		parent := filepath.Dir(p)
		if parent == p {
			return filepath.Join(append([]string{p}, rest...)...), nil
		}
		rest = append([]string{filepath.Base(p)}, rest...)
		p = parent
	}
}

// confineToProject refuses a project-scope target that, once every symlink on
// its path is resolved, lands outside the resolved project directory (cwd).
// Both sides are resolved with filepath.EvalSymlinks, so a project reached
// through a symlinked path still contains its own files. This covers a
// symlinked ./AGENTS.md (whose target would otherwise receive the Jentic
// block) and a symlinked skill dir or ancestor (which would otherwise receive
// SKILL.md, its sidecar and references). Nothing is written on refusal.
func confineToProject(cwd, target string) error {
	absCwd, err := filepath.Abs(cwd)
	if err != nil {
		return fmt.Errorf("resolve project directory %s: %w", cwd, err)
	}
	root, err := filepath.EvalSymlinks(absCwd)
	if err != nil {
		return fmt.Errorf("resolve project directory %s: %w", cwd, err)
	}
	resolved, err := resolveExisting(target)
	if err != nil {
		return fmt.Errorf("%w; edit the file it points to by hand", err)
	}
	if !within(root, resolved) {
		return fmt.Errorf("%s %w %s (it points to %s); Jentic does not write there, edit it by hand",
			target, errOutsideProject, root, resolved)
	}
	return nil
}

// referencesRoot opens skillDir as an os.Root and returns it once references/
// inside it is a plain directory (creating it first when create is set). Every
// reference read, write and delete then goes through the Root, which resolves
// each path component atomically and refuses any that leaves the skill dir —
// so a references/ swapped for a symlink or (on Windows) a junction between a
// check and its use cannot redirect the operation. A references/ that is
// itself a link or another irregular entry is refused outright. A missing
// skill dir or references/ reports fs.ErrNotExist.
func referencesRoot(skillDir string, create bool) (*os.Root, error) {
	root, err := os.OpenRoot(skillDir)
	if err != nil {
		return nil, err
	}
	if create {
		if err := root.MkdirAll(referencesDir, 0o755); err != nil {
			_ = root.Close()
			return nil, fmt.Errorf("%w: create %s: %w", errUnsafeReference, filepath.Join(skillDir, referencesDir), err)
		}
	}
	fi, err := root.Lstat(referencesDir)
	if err != nil {
		_ = root.Close()
		return nil, err
	}
	if !fi.IsDir() || fi.Mode()&(os.ModeSymlink|os.ModeIrregular) != 0 {
		_ = root.Close()
		return nil, fmt.Errorf("%w: %s is not a plain directory", errUnsafeReference, filepath.Join(skillDir, referencesDir))
	}
	return root, nil
}

// removeManagedReference deletes one recorded reference file of the skill in
// skillDir. It is the single removal path for sidecar-recorded references —
// both `skill remove` (removeOwnedFile) and the apply-time prune that
// `skill update` / `jenticctl update` run (applyOwnedReferences) go through it.
// The name must be flat and the delete runs inside referencesRoot, so it can
// only ever remove an entry under the skill dir. It never follows a symlink:
// the entry is Lstat'ed, and a symlink is removed as the link itself, leaving
// its target untouched. Directories are never removed. It reports whether a
// file was deleted; a missing file is not an error.
func removeManagedReference(skillDir, ref string) (bool, error) {
	if !validReferenceName(ref) {
		return false, fmt.Errorf("%w: %q", errUnsafeReference, ref)
	}
	root, err := referencesRoot(skillDir, false)
	if errors.Is(err, fs.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	defer func() { _ = root.Close() }()
	name := filepath.Join(referencesDir, ref)
	fi, err := root.Lstat(name)
	if errors.Is(err, fs.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("stat %s: %w", filepath.Join(skillDir, name), err)
	}
	if fi.IsDir() {
		return false, fmt.Errorf("%w: %s is a directory", errUnsafeReference, filepath.Join(skillDir, name))
	}
	// Remove on a symlink unlinks the link, never its target.
	if err := root.Remove(name); err != nil {
		return false, fmt.Errorf("remove %s: %w", filepath.Join(skillDir, name), err)
	}
	return true, nil
}

// writeManagedReference writes reference ref of the skill in skillDir unless
// the file already holds data (newline-normalized), reporting whether it
// wrote. The write is atomic (temp file + rename) and confined to the skill
// dir through referencesRoot.
func writeManagedReference(skillDir, ref string, data []byte) (bool, error) {
	if !validReferenceName(ref) {
		return false, fmt.Errorf("%w: %q", errUnsafeReference, ref)
	}
	root, err := referencesRoot(skillDir, true)
	if err != nil {
		return false, err
	}
	defer func() { _ = root.Close() }()
	name := filepath.Join(referencesDir, ref)
	if existing, err := root.ReadFile(name); err == nil &&
		normalizeNewlines(string(existing)) == normalizeNewlines(string(data)) {
		return false, nil
	}
	if err := writeFileAtomicInRoot(root, name, data); err != nil {
		return false, fmt.Errorf("write %s: %w", filepath.Join(skillDir, name), err)
	}
	return true, nil
}

// referenceState reports, read-only, whether reference ref of the skill in
// skillDir currently holds data (current) and whether any non-directory entry
// exists under that name (present). An unusable references/ reads as neither.
func referenceState(skillDir, ref string, data []byte) (current, present bool) {
	if !validReferenceName(ref) {
		return false, false
	}
	root, err := referencesRoot(skillDir, false)
	if err != nil {
		return false, false
	}
	defer func() { _ = root.Close() }()
	name := filepath.Join(referencesDir, ref)
	fi, err := root.Lstat(name)
	if err != nil || fi.IsDir() {
		return false, false
	}
	if data == nil {
		return false, true
	}
	existing, err := root.ReadFile(name)
	return err == nil && normalizeNewlines(string(existing)) == normalizeNewlines(string(data)), true
}

// writeFileAtomicInRoot is writeFileAtomic confined to root: the temp file is
// created next to name inside root and renamed over it, so a crash never
// truncates the existing file and neither step can leave the root. The
// existing mode is kept; new files default to 0o644.
func writeFileAtomicInRoot(root *os.Root, name string, data []byte) error {
	mode := os.FileMode(0o644)
	if fi, err := root.Lstat(name); err == nil && fi.Mode().IsRegular() {
		mode = fi.Mode().Perm()
	}
	tmpName := filepath.Join(filepath.Dir(name), ".jentic-skill-"+rand.Text()+".tmp")
	tmp, err := root.OpenFile(tmpName, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	renamed := false
	defer func() {
		if !renamed {
			_ = root.Remove(tmpName)
		}
	}()
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	// Chmod through the open handle, so the mode lands on this file even if
	// the name is swapped underneath.
	if err := tmp.Chmod(mode); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := root.Rename(tmpName, name); err != nil {
		return err
	}
	renamed = true
	return nil
}
