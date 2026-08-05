package skillgen

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// Outcome describes what applying one adapter did, for user-facing messaging
// and for the command's exit reporting.
type Outcome struct {
	Operator  Operator
	Skill     string
	Path      string
	Created   bool // the target file did not exist before
	Changed   bool // the skill content was written/updated
	Skipped   bool // nothing to do (already up to date)
	UserEdits bool // an existing install had manual edits (needs --force)
}

// ApplyOptions controls how Apply writes a skill.
type ApplyOptions struct {
	Scope  Scope // user vs project; zero value uses the adapter default
	Force  bool  // overwrite a user-edited install
	DryRun bool  // compute the outcome and target but write nothing
}

// Apply renders one skill through one adapter and writes it to the adapter's
// target, idempotently. For a shared AGENTS.md it splices only that skill's
// named block, leaving user content and sibling blocks intact; for an
// owned-file SKILL.md it writes a clean spec file plus a provenance sidecar. It
// refuses to overwrite a manually-edited install unless Force.
func Apply(a Adapter, c Canonical, env DetectEnv, opts ApplyOptions) (Outcome, error) {
	scope := opts.Scope
	if scope == "" {
		scope = a.DefaultScope()
	}
	target := a.Target(scope, c.Name, env)
	out := Outcome{Operator: a.Operator(), Skill: c.Name, Path: target}

	existing, err := os.ReadFile(target) //nolint:gosec // target is derived from adapter rules + env, not arbitrary input.
	if err != nil && !os.IsNotExist(err) {
		return out, fmt.Errorf("read %s: %w", target, err)
	}
	out.Created = os.IsNotExist(err)
	norm := []byte(normalizeNewlines(string(existing)))

	if a.OwnsWholeFile() {
		return applyOwnedFile(a, c, target, norm, out, opts)
	}
	return applySharedFile(a, c, target, norm, out, opts)
}

// applySharedFile handles AGENTS.md operators: only this skill's named block is
// touched; a user edit to *this* block (not a sibling's) refuses without Force.
func applySharedFile(a Adapter, c Canonical, target string, norm []byte, out Outcome, opts ApplyOptions) (Outcome, error) {
	if !opts.Force {
		blk := findBlock(norm, c.Name)
		if !blk.found {
			blk = findMigratableLegacyBlock(norm, c.Name)
		}
		if blk.found && blockUserEdited(norm, blk) {
			out.UserEdits = true
			return out, nil
		}
	}

	newBytes, changed, err := a.Render(c, norm)
	if err != nil {
		return out, fmt.Errorf("render %s %s skill: %w", a.Operator(), c.Name, err)
	}
	out.Changed = changed
	out.Skipped = !changed
	if !changed || opts.DryRun {
		return out, nil
	}
	if err := writeTarget(target, newBytes); err != nil {
		return out, err
	}
	return out, nil
}

// applyOwnedFile handles claude/cursor/hermes SKILL.md: a clean spec file plus
// a provenance sidecar. Edit detection compares the recorded sidecar body hash
// against the current file's body; when there is no sidecar but the file
// exists, it is treated as user content (refuse without Force) — unless it
// carries a legacy managed block, which is our own pre-migration write and is
// safely rewritten.
func applyOwnedFile(a Adapter, c Canonical, target string, norm []byte, out Outcome, opts ApplyOptions) (Outcome, error) {
	if !out.Created && !opts.Force {
		if ownedFileUserEdited(target, norm, c.Name) {
			out.UserEdits = true
			return out, nil
		}
	}

	newBytes, changed, err := a.Render(c, norm)
	if err != nil {
		return out, fmt.Errorf("render %s %s skill: %w", a.Operator(), c.Name, err)
	}
	out.Changed = changed
	out.Skipped = !changed
	if !changed || opts.DryRun {
		return out, nil
	}
	if err := writeTarget(target, newBytes); err != nil {
		return out, err
	}
	if err := writeSidecar(target, c); err != nil {
		return out, err
	}
	return out, nil
}

// ownedFileUserEdited reports whether an existing owned-file SKILL.md holds
// content Jentic did not write. It trusts the sidecar's recorded body hash when
// present; a legacy in-file managed block is recognized as our own pre-split
// write (migratable, not a user edit); anything else with no sidecar is treated
// as user content.
func ownedFileUserEdited(target string, norm []byte, name string) bool {
	sc, ok := readSidecar(target)
	if ok {
		return bodyHash(dedicatedBody(norm)) != sc.BodyHash
	}
	// No sidecar. A legacy marker-wrapped block is our own pre-migration file,
	// safe to rewrite; verify the block body still matches its recorded hash so
	// a user edit inside it is still caught. Only jentic could have a legacy
	// owned file (it was the sole pre-split skill), so scope the migration read
	// to it — a stray un-named block under any other skill's dir is user content.
	if blk := findMigratableLegacyBlock(norm, name); blk.found {
		return blockUserEdited(norm, blk)
	}
	// Unknown content with no provenance: treat as the user's.
	return true
}

// writeSidecar records the provenance of an owned-file skill next to it.
func writeSidecar(skillMD string, c Canonical) error {
	sc := sidecar{
		Name:     c.Name,
		BodyHash: bodyHash(skillBody(c)),
		Source:   string(c.source()),
		BaseURL:  c.BaseURL,
	}
	data, err := json.MarshalIndent(sc, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal sidecar: %w", err)
	}
	return writeFileAtomic(sidecarPath(skillMD), append(data, '\n'))
}

// readSidecar loads the provenance sidecar for an owned-file skill, reporting
// whether a well-formed one was present.
func readSidecar(skillMD string) (sidecar, bool) {
	data, err := os.ReadFile(sidecarPath(skillMD))
	if err != nil {
		return sidecar{}, false
	}
	var sc sidecar
	if err := json.Unmarshal(data, &sc); err != nil || sc.BodyHash == "" {
		return sidecar{}, false
	}
	return sc, true
}

// writeTarget mkdir-ps the target's directory and atomically writes data.
func writeTarget(target string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil { //nolint:gosec // skill dirs are world-readable by design.
		return fmt.Errorf("create %s: %w", filepath.Dir(target), err)
	}
	if err := writeFileAtomic(target, data); err != nil {
		return fmt.Errorf("write %s: %w", target, err)
	}
	return nil
}

// RemoveOptions controls how Remove strips a skill.
type RemoveOptions struct {
	Scope  Scope // user vs project; zero value uses the adapter default
	Force  bool  // remove even a manually-edited install
	DryRun bool  // compute the outcome and target but write nothing
}

// RemoveOutcome describes what removing one skill did, for messaging.
type RemoveOutcome struct {
	Path      string
	Removed   bool // the skill was stripped (or file deleted)
	Missing   bool // there was no matching install to remove
	UserEdits bool // the install had manual edits and was kept (needs --force)
}

// Remove strips one skill from an adapter's target. For a shared AGENTS.md it
// splices out only that skill's named block, leaving sibling blocks and user
// content; if that was the only block and nothing else remains, the file is
// deleted. For an owned-file SKILL.md it deletes the file and its sidecar and
// prunes now-empty skill dirs. A manually-edited install is preserved unless
// opts.Force is set.
func Remove(a Adapter, c Canonical, env DetectEnv, opts RemoveOptions) (RemoveOutcome, error) {
	scope := opts.Scope
	if scope == "" {
		scope = a.DefaultScope()
	}
	target := a.Target(scope, c.Name, env)
	out := RemoveOutcome{Path: target}

	existing, err := os.ReadFile(target) //nolint:gosec // target is derived from adapter rules + env.
	if os.IsNotExist(err) {
		out.Missing = true
		return out, nil
	}
	if err != nil {
		return out, fmt.Errorf("read %s: %w", target, err)
	}
	norm := []byte(normalizeNewlines(string(existing)))

	if a.OwnsWholeFile() {
		return removeOwnedFile(target, norm, c.Name, out, opts)
	}
	return removeSharedFile(target, norm, c, out, opts)
}

// removeSharedFile strips one named block from AGENTS.md, preserving siblings.
func removeSharedFile(target string, norm []byte, c Canonical, out RemoveOutcome, opts RemoveOptions) (RemoveOutcome, error) {
	blk := findBlock(norm, c.Name)
	if !blk.found {
		blk = findMigratableLegacyBlock(norm, c.Name)
	}
	if !blk.found {
		out.Missing = true
		return out, nil
	}
	if !opts.Force && blockUserEdited(norm, blk) {
		out.UserEdits = true
		return out, nil
	}
	if opts.DryRun {
		out.Removed = true
		return out, nil
	}

	remainder := trimSurrounding(string(norm[:blk.start]) + string(norm[blk.endPos:]))
	if remainder == "" {
		if err := os.Remove(target); err != nil {
			return out, fmt.Errorf("remove %s: %w", target, err)
		}
		out.Removed = true
		return out, nil
	}
	if err := writeFileAtomic(target, []byte(remainder+"\n")); err != nil {
		return out, fmt.Errorf("write %s: %w", target, err)
	}
	out.Removed = true
	return out, nil
}

// removeOwnedFile deletes an owned-file SKILL.md and its sidecar. If the user
// added their own content beyond a clean Jentic write, the file is preserved
// (rewritten without our provenance) unless forced.
func removeOwnedFile(target string, norm []byte, name string, out RemoveOutcome, opts RemoveOptions) (RemoveOutcome, error) {
	if !opts.Force && ownedFileUserEdited(target, norm, name) {
		out.UserEdits = true
		return out, nil
	}
	if opts.DryRun {
		out.Removed = true
		return out, nil
	}
	if err := os.Remove(target); err != nil {
		return out, fmt.Errorf("remove %s: %w", target, err)
	}
	_ = os.Remove(sidecarPath(target))
	pruneEmptyDirs(filepath.Dir(target))
	out.Removed = true
	return out, nil
}

// writeFileAtomic writes data to a temp file in the target's directory, then
// renames it over the target. This guarantees a reader never sees a partially
// written file and a crash/disk-full mid-write cannot truncate or corrupt the
// existing target — which matters because some targets (AGENTS.md) interleave
// our managed block with user-owned content. The target's existing mode is
// preserved; new files default to 0o644 (skill files are meant to be read by
// other tools).
func writeFileAtomic(target string, data []byte) error {
	mode := os.FileMode(0o644)
	if info, err := os.Stat(target); err == nil {
		mode = info.Mode().Perm()
	}

	dir := filepath.Dir(target)
	tmp, err := os.CreateTemp(dir, ".jentic-skill-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	// Best-effort cleanup if we bail before the rename succeeds. Once the rename
	// lands, tmpName no longer exists, so skip the spurious remove.
	renamed := false
	defer func() {
		if !renamed {
			_ = os.Remove(tmpName)
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
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmpName, mode); err != nil {
		return err
	}
	if err := os.Rename(tmpName, target); err != nil {
		return err
	}
	renamed = true
	return nil
}

// trimSurrounding collapses the blank lines left behind when a block is spliced
// out of the middle of a file.
func trimSurrounding(s string) string {
	for len(s) > 0 && (s[0] == '\n') {
		s = s[1:]
	}
	for len(s) > 0 && s[len(s)-1] == '\n' {
		s = s[:len(s)-1]
	}
	return s
}

// maxPruneDepth bounds how far up the tree pruneEmptyDirs will walk; the
// jentic skill dir tree (skills/<name>/<category>) is shallow, so a small cap
// prevents an unbounded climb if a boundary dir is ever missing.
const maxPruneDepth = 4

// pruneEmptyDirs removes dir and any now-empty ancestors that the generator
// itself created (the `<name>/<category>` tree under a skills dir),
// best-effort. It stops at the first non-empty directory or at a boundary dir
// we did not create (.claude, .cursor, .hermes, skills, …) so the walk never
// climbs into user config.
func pruneEmptyDirs(dir string) {
	for range maxPruneDepth {
		base := filepath.Base(dir)
		if base == "skills" || base == ".claude" || base == ".cursor" || base == ".hermes" {
			return // reached a boundary we don't own.
		}
		entries, err := os.ReadDir(dir)
		if err != nil || len(entries) > 0 {
			return
		}
		if err := os.Remove(dir); err != nil {
			return
		}
		dir = filepath.Dir(dir)
	}
}
