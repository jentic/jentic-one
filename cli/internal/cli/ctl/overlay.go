package ctl

import (
	"fmt"

	"gopkg.in/yaml.v3"
)

// OverlaySettings merges the resolved installer settings into an existing
// jentic-one.yaml document, returning the re-serialised bytes. It operates on the
// yaml.Node tree rather than round-tripping through a typed struct so it PRESERVES
// what the OSS BackendConfig schema cannot describe: enterprise overlay extension
// sections (e.g. embeddings:) and operator comments in a hand-edited file
// (impl/6.0 §4, same posture as config.MutateConfig, impl/1.3 §2). A struct-based
// rewrite would silently delete both.
//
// Only the leaves the settings name are set; every other key (known or unknown)
// in the original document is left byte-for-byte intact. Settings values are
// mapped onto YAML scalar/mapping nodes; nested settings recurse into (creating,
// if absent) the matching mapping node.
func OverlaySettings(original []byte, settings Settings) ([]byte, error) {
	var root yaml.Node
	if len(original) > 0 {
		if err := yaml.Unmarshal(original, &root); err != nil {
			return nil, fmt.Errorf("parse existing config for overlay: %w", err)
		}
	}
	// An empty/whitespace document unmarshals to a zero Node; start a fresh doc.
	doc := documentMapping(&root)
	if err := overlayInto(doc, settings); err != nil {
		return nil, err
	}
	out, err := yaml.Marshal(&root)
	if err != nil {
		return nil, fmt.Errorf("re-serialise config after overlay: %w", err)
	}
	return out, nil
}

// documentMapping returns the top-level mapping node of the document, initialising
// root as a document→mapping when it is empty (a fresh install with no prior file).
func documentMapping(root *yaml.Node) *yaml.Node {
	if root.Kind == 0 {
		mapping := &yaml.Node{Kind: yaml.MappingNode, Tag: "!!map"}
		root.Kind = yaml.DocumentNode
		root.Content = []*yaml.Node{mapping}
		return mapping
	}
	if root.Kind == yaml.DocumentNode && len(root.Content) > 0 {
		return root.Content[0]
	}
	return root
}

// overlayInto writes each setting onto the mapping node, recursing into nested
// settings (creating child mappings as needed) and setting scalar leaves.
func overlayInto(mapping *yaml.Node, settings Settings) error {
	if mapping.Kind != yaml.MappingNode {
		return fmt.Errorf("cannot overlay onto a non-mapping YAML node (kind %d)", mapping.Kind)
	}
	for key, value := range settings {
		if sub, ok := asSettings(value); ok {
			child := childMapping(mapping, key)
			if err := overlayInto(child, sub); err != nil {
				return fmt.Errorf("%s.%w", key, err)
			}
			continue
		}
		if err := setScalar(mapping, key, value); err != nil {
			return fmt.Errorf("%s: %w", key, err)
		}
	}
	return nil
}

// childMapping returns the mapping node for key, creating (key: {}) if absent so
// a nested overlay has somewhere to land. Existing values under key are reused.
func childMapping(mapping *yaml.Node, key string) *yaml.Node {
	if i := findKey(mapping, key); i >= 0 {
		v := mapping.Content[i+1]
		if v.Kind != yaml.MappingNode {
			// Replace a scalar/other node with a fresh mapping so we can nest.
			*v = yaml.Node{Kind: yaml.MappingNode, Tag: "!!map"}
		}
		return v
	}
	child := &yaml.Node{Kind: yaml.MappingNode, Tag: "!!map"}
	mapping.Content = append(mapping.Content,
		&yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str", Value: key},
		child,
	)
	return child
}

// setScalar sets key to a scalar value, updating an existing node in place
// (preserving its position and any comment) or appending a new key/value pair.
func setScalar(mapping *yaml.Node, key string, value any) error {
	node := &yaml.Node{}
	if err := node.Encode(value); err != nil {
		return err
	}
	if i := findKey(mapping, key); i >= 0 {
		// Preserve the existing node's comments; swap only the value/kind/tag.
		existing := mapping.Content[i+1]
		node.HeadComment = existing.HeadComment
		node.LineComment = existing.LineComment
		node.FootComment = existing.FootComment
		*existing = *node
		return nil
	}
	mapping.Content = append(mapping.Content,
		&yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str", Value: key},
		node,
	)
	return nil
}

// findKey returns the index of key's scalar node in a mapping's Content (keys are
// at even indices, values at the following odd index), or -1 if absent.
func findKey(mapping *yaml.Node, key string) int {
	for i := 0; i+1 < len(mapping.Content); i += 2 {
		if mapping.Content[i].Value == key {
			return i
		}
	}
	return -1
}
