package skillgen

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// errUnsafeReference marks a recorded reference name that does not resolve to
// a plain file inside the skill dir. The sidecar is on-disk data anyone with
// write access to the skill dir (or a cloned repo shipping a project-scope
// install) controls, so its reference list is never trusted as a path.
var errUnsafeReference = errors.New("reference resolves outside the skill directory")

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

// referencePath returns the on-disk path for reference ref of the skill whose
// SKILL.md lives in skillDir, or errUnsafeReference when it would land
// outside skillDir: the name must be flat, and the references dir — after
// filepath.Clean and filepath.EvalSymlinks — must still sit under the
// resolved skill dir, so a references/ symlink planted to point elsewhere
// cannot redirect a write or a delete. A references dir that does not exist
// yet is fine (nothing on disk to escape through); the caller creates it as a
// real directory.
func referencePath(skillDir, ref string) (string, error) {
	if !validReferenceName(ref) {
		return "", fmt.Errorf("%w: %q", errUnsafeReference, ref)
	}
	skillDir = filepath.Clean(skillDir)
	refDir := filepath.Join(skillDir, "references")
	root, err := filepath.EvalSymlinks(skillDir)
	if err != nil {
		if os.IsNotExist(err) {
			return filepath.Join(refDir, ref), nil
		}
		return "", fmt.Errorf("resolve %s: %w", skillDir, err)
	}
	resolvedRefDir, err := filepath.EvalSymlinks(refDir)
	switch {
	case err == nil:
		if !within(root, resolvedRefDir) {
			return "", fmt.Errorf("%w: %s points to %s", errUnsafeReference, refDir, resolvedRefDir)
		}
	case os.IsNotExist(err):
		// A dangling references/ symlink also reports not-exist; refuse it
		// rather than let a later MkdirAll/write follow the link.
		if fi, lerr := os.Lstat(refDir); lerr == nil && fi.Mode()&os.ModeSymlink != 0 {
			return "", fmt.Errorf("%w: %s is a dangling symlink", errUnsafeReference, refDir)
		}
	default:
		return "", fmt.Errorf("resolve %s: %w", refDir, err)
	}
	return filepath.Join(refDir, ref), nil
}

// removeManagedReference deletes one recorded reference file of the skill in
// skillDir. It is the single removal path for sidecar-recorded references —
// both `skill remove` (removeOwnedFile) and the apply-time prune that
// `skill update` / `jenticctl update` run (applyOwnedReferences) go through it.
// It never follows a symlink: the entry is Lstat'ed, and a symlink is removed
// as the link itself, leaving its target untouched. Directories are never
// removed. It reports whether a file was deleted; a missing file is not an
// error.
func removeManagedReference(skillDir, ref string) (bool, error) {
	path, err := referencePath(skillDir, ref)
	if err != nil {
		return false, err
	}
	fi, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("stat %s: %w", path, err)
	}
	if fi.IsDir() {
		return false, fmt.Errorf("%w: %s is a directory", errUnsafeReference, path)
	}
	// os.Remove on a symlink unlinks the link, never its target.
	if err := os.Remove(path); err != nil {
		return false, fmt.Errorf("remove %s: %w", path, err)
	}
	return true, nil
}
