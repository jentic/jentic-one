package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"

	"github.com/gofrs/flock"
	"gopkg.in/yaml.v3"
)

// validNamePattern is the canonical charset for user-supplied identity,
// environment, and context names: lowercase alnum start, then alnum/hyphen, up to
// 64 chars. No separators ("_") and no dots — both matter for the on-disk
// filename stems in client/auth (a "_" could collide two refs' stems; a "." could
// escape via path traversal or collide with the file-less sentinels). Enforced at
// creation by env add/identity add and re-checked fail-closed in auth (impl/4.1
// §1).
var validNamePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,63}$`)

// ValidName reports whether name is a legal identity/environment/context name.
// This is the single source of truth for the charset; client/auth aliases it.
func ValidName(name string) bool {
	return validNamePattern.MatchString(name)
}

// MutateConfig loads the current config, applies the mutator, and writes it back
// atomically under an advisory cross-process lock.
//
// Concurrency: registration (Phase 4.1) and other writers do read-modify-write on
// config.yaml; two concurrent CI pipelines registering the same identity could
// interleave and clobber one another's status/client_id. We hold an advisory
// flock for the whole critical section. The lock is on a SEPARATE config.lock
// sidecar (never config.yaml itself) so the atomic rename below cannot replace the
// file we hold the lock on.
//
// Unknown-key preservation: a naive Unmarshal(Config) + Marshal(Config) round-trip
// SILENTLY DROPS any key the struct doesn't know. Two real populations depend on
// unknown keys surviving: (1) the mixed-version reality (an older binary mutating a
// newer binary's config must not strip newer fields), and (2) the enterprise
// overlay's extension data. So we parse into a yaml.Node tree, decode the typed
// Config view for the mutator, then merge the mutated typed view BACK onto the
// original node — unknown keys and comments pass through untouched (impl/1.3 §2).
func MutateConfig(mutator func(*Config) error) error {
	dir, err := ConfigDir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("creating config dir: %w", err)
	}

	lock := flock.New(filepath.Join(dir, "config.lock"))
	if err := lock.Lock(); err != nil { // blocks until acquired
		return fmt.Errorf("locking config: %w", err)
	}
	defer func() { _ = lock.Unlock() }()

	file := filepath.Join(dir, "config.yaml")

	// Read the existing document into a node tree (empty when the file is absent).
	var root yaml.Node
	haveDoc := false
	data, err := os.ReadFile(file) //nolint:gosec // path is ConfigDir()/config.yaml, CLI-managed, not user input.
	switch {
	case err == nil:
		if len(data) > 0 {
			if uerr := yaml.Unmarshal(data, &root); uerr != nil {
				return fmt.Errorf("parsing config.yaml: %w", uerr)
			}
			haveDoc = root.Kind != 0
		}
	case os.IsNotExist(err):
		// fresh config — leave root empty
	default:
		return fmt.Errorf("reading config.yaml: %w", err)
	}

	// Project the typed view out of the node tree for the mutator.
	var cfg Config
	if haveDoc {
		if derr := root.Decode(&cfg); derr != nil {
			return fmt.Errorf("decoding config.yaml: %w", derr)
		}
	}
	if cfg.Contexts == nil {
		cfg.Contexts = make(map[string]Context)
	}
	if cfg.Environments == nil {
		cfg.Environments = make(map[string]Env)
	}
	if cfg.Identities == nil {
		cfg.Identities = make(map[string]Identity)
	}

	if err := mutator(&cfg); err != nil {
		return err
	}

	// Encode the mutated typed view into its own node, then merge its top-level
	// keys onto the original document node so unknown keys/comments survive.
	var typedNode yaml.Node
	if err := typedNode.Encode(&cfg); err != nil {
		return fmt.Errorf("encoding config: %w", err)
	}
	if err := mergeMappingKeys(&root, &typedNode); err != nil {
		return err
	}

	out, err := yaml.Marshal(&root)
	if err != nil {
		return fmt.Errorf("marshaling config: %w", err)
	}

	// Atomic write: temp file + rename. The lock prevents interleaving; the rename
	// prevents a torn file.
	tmp := file + ".tmp"
	if err := os.WriteFile(tmp, out, 0o600); err != nil {
		return fmt.Errorf("writing config.yaml: %w", err)
	}
	if err := os.Rename(tmp, file); err != nil {
		return fmt.Errorf("replacing config.yaml: %w", err)
	}
	return nil
}

// typedMapSections are the top-level keys whose value is a Go map fully owned by
// the schema (map[string]Env, map[string]Context, map[string]Identity). Their
// DIRECT children are entries, so a mutator deleting an entry must propagate as a
// removal. Everything else — the document top level (where extensions like an
// enterprise_overlay block live) and the FIELDS inside a single entry (where an
// unknown x_* field may live) — preserves dst-only keys instead of pruning them.
var typedMapSections = map[string]bool{
	"contexts":     true,
	"environments": true,
	"identities":   true,
}

// mergeMappingKeys deep-overlays src's mapping onto dst's mapping so that a
// mutation round-trips unknown keys/comments (impl/1.3 §2) while still honoring
// deletions of typed map entries:
//
//   - Document top level: overlay src's keys; KEEP dst-only keys (schema
//     extensions such as enterprise_overlay).
//   - Inside a typed map section (contexts/environments/identities): overlay AND
//     PRUNE dst entries the mutator removed — deletion must propagate.
//   - Inside a single entry (an Env/Context/Identity value): overlay src's known
//     fields but KEEP dst-only fields (unknown per-entry extensions).
//
// dst may be empty (fresh file), in which case it adopts the typed encoding.
func mergeMappingKeys(dst, src *yaml.Node) error {
	srcMap := mappingOf(src)
	if srcMap == nil {
		return errors.New("internal: encoded config is not a mapping")
	}
	dstMap := mappingOf(dst)
	if dstMap == nil {
		*dst = *src // fresh/non-mapping document: adopt the typed encoding wholesale
		return nil
	}
	mergeMapping(dstMap, srcMap, false)
	return nil
}

// mergeMapping overlays src onto dst. When pruneMissing is true, dst keys absent
// from src are removed (used one level deep inside a typed map section, where
// children are entries and deletion must propagate). For each retained key whose
// value is a typed map SECTION, its children are merged with pruneMissing=true;
// all other nested mappings merge with pruneMissing=false so per-entry unknown
// fields survive.
func mergeMapping(dst, src *yaml.Node, pruneMissing bool) {
	for i := 0; i+1 < len(src.Content); i += 2 {
		keyNode := src.Content[i]
		valNode := src.Content[i+1]
		childPrune := typedMapSections[keyNode.Value]
		if idx := findKey(dst, keyNode.Value); idx >= 0 {
			dstVal := dst.Content[idx+1]
			if dstVal.Kind == yaml.MappingNode && valNode.Kind == yaml.MappingNode {
				mergeMapping(dstVal, valNode, childPrune)
			} else {
				dst.Content[idx+1] = valNode
			}
		} else {
			dst.Content = append(dst.Content, keyNode, valNode)
		}
	}
	if pruneMissing {
		pruneKeysNotIn(dst, src)
	}
}

// pruneKeysNotIn removes every key/value pair from dst whose key is not present in
// src. Used to propagate typed-map-entry deletions.
func pruneKeysNotIn(dst, src *yaml.Node) {
	kept := dst.Content[:0]
	for i := 0; i+1 < len(dst.Content); i += 2 {
		if findKey(src, dst.Content[i].Value) >= 0 {
			kept = append(kept, dst.Content[i], dst.Content[i+1])
		}
	}
	dst.Content = kept
}

// mappingOf returns the mapping node inside a (possibly document) node, or nil.
func mappingOf(n *yaml.Node) *yaml.Node {
	if n == nil {
		return nil
	}
	if n.Kind == yaml.DocumentNode {
		if len(n.Content) == 0 {
			return nil
		}
		n = n.Content[0]
	}
	if n.Kind == yaml.MappingNode {
		return n
	}
	return nil
}

// findKey returns the index of the KEY node for key within a mapping's Content
// slice, or -1. Values sit at index+1.
func findKey(mapping *yaml.Node, key string) int {
	for i := 0; i+1 < len(mapping.Content); i += 2 {
		if mapping.Content[i].Value == key {
			return i
		}
	}
	return -1
}
